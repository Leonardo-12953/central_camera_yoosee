"""FFmpeg process lifecycle for one camera.

One process per camera. The RTSP URL (which contains credentials) is never
logged: only a redacted version is written to logs.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from logging_utils import make_redactor

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def _double_bitrate(value: str) -> str:
    match = re.fullmatch(r"(\d+)([kKmM]?)", str(value).strip())
    if not match:
        return "5000k"
    number = int(match.group(1)) * 2
    suffix = (match.group(2) or "k").lower()
    return f"{number}{suffix}"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


class FFmpegProcess:
    def __init__(self, spec, password: str, base_dir: Path, hls_cfg: dict,
                 logger: logging.Logger):
        self.spec = spec
        self.password = password
        self.base_dir = base_dir
        if isinstance(hls_cfg, dict):
            self.hls_cfg = hls_cfg
        else:
            self.hls_cfg = {
                "segment_seconds": getattr(hls_cfg, "segment_seconds", 2),
                "playlist_size": getattr(hls_cfg, "playlist_size", 6),
                "delete_threshold": getattr(hls_cfg, "delete_threshold", 2),
                "replay_minutes": getattr(hls_cfg, "replay_minutes", 0),
            }
        self.logger = logger
        self.segment_seconds = max(1, int(self.hls_cfg["segment_seconds"]))
        # Fontes públicas informam uma URL completa em vez de host RTSP.
        self.source_url = str(getattr(spec, "source_url", "") or "").strip()
        # 30 minutos é o teto rígido do replay.
        self.replay_seconds = max(0, min(30, int(self.hls_cfg.get("replay_minutes", 0)))) * 60
        self.out_dir = base_dir / "streams" / spec.id
        self.playlist = self.out_dir / "stream.m3u8"
        self.pid_file = self.out_dir / ".ffmpeg.pid"
        self.log_file = base_dir / "logs" / f"ffmpeg-{_safe_id(spec.id)}.log"
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._log_lock = threading.Lock()
        self._redact = make_redactor([password])
        self._initial_playlist_mtime = 0.0
        self.on_stderr = None
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------------ URL
    def _url(self) -> str:
        if self.source_url:
            from source_resolver import resolve_source
            return resolve_source(self.source_url)
        user = quote(self.spec.username, safe="")
        secret = quote(self.password, safe="")
        return (f"rtsp://{user}:{secret}@{self.spec.host}:{self.spec.port}"
                f"{self.spec.path}")

    def safe_url(self) -> str:
        if self.source_url:
            from source_resolver import display_url
            return display_url(self.source_url)
        return (f"rtsp://{self.spec.username}:***@{self.spec.host}:"
                f"{self.spec.port}{self.spec.path}")

    # -------------------------------------------------------------- command
    def _playlist_size(self) -> int:
        """Entradas necessárias para cobrir a janela de replay."""
        base = max(2, int(self.hls_cfg["playlist_size"]))
        if self.replay_seconds <= 0:
            return base
        needed = math.ceil(self.replay_seconds / self.segment_seconds)
        return max(base, needed)

    def build_command(self) -> list[str]:
        seg = self.segment_seconds
        gop = max(1, int(round(self.spec.input_fps * seg)))
        bitrate = str(self.spec.video_bitrate)
        bufsize = _double_bitrate(bitrate)

        filters = []
        if self.spec.output_height and self.spec.output_height > 0:
            filters.append(f"scale=-2:{int(self.spec.output_height)}")
        elif self.spec.output_width and self.spec.output_width > 0:
            filters.append(f"scale={int(self.spec.output_width)}:-2")
        filters.append("format=yuv420p")

        hls_flags = "delete_segments+independent_segments+omit_endlist+temp_file+program_date_time"
        if self.replay_seconds > 0:
            # Mantém a playlist/numeração entre reinícios do FFmpeg para não
            # perder o histórico já gravado.
            hls_flags += "+append_list"

        cmd = [
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "info",
        ]
        if self.source_url:
            # URL pública (YouTube/HLS): não é RTSP e a URL assinada expira;
            # o resolve_source é chamado de novo a cada reinício do FFmpeg.
            cmd += ["-rw_timeout", "30000000", "-live_start_index", "-1"]
        else:
            cmd += [
                "-rtsp_transport", self.spec.transport,
                "-timeout", "10000000",
            ]
        cmd += [
            "-i", self._url(),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", ",".join(filters),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-profile:v", "main",
            "-pix_fmt", "yuv420p",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{seg})",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-c:a", "aac",
            "-b:a", str(self.spec.audio_bitrate),
            "-ac", "1",
            "-f", "hls",
            "-hls_time", str(seg),
            "-hls_list_size", str(self._playlist_size()),
            "-hls_delete_threshold", str(int(self.hls_cfg["delete_threshold"])),
            "-hls_flags", hls_flags,
            "-hls_allow_cache", "0",
            "-hls_segment_filename", str(self.out_dir / "seg_%06d.ts"),
            str(self.playlist),
        ]
        return cmd

    # --------------------------------------------------------------- start
    def start(self) -> int:
        with self._start_lock:
            if self.is_alive():
                return self._proc.pid  # type: ignore[union-attr]
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("ffmpeg não encontrado no PATH")
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._prepare_output_dir()
            if self.log_file.exists() and self.log_file.stat().st_size > 5 * 1024 * 1024:
                try:
                    self.log_file.unlink()
                except OSError:
                    pass

            self.logger.info("[%s] iniciando ffmpeg: %s", self.spec.id, self.safe_url())
            try:
                self._initial_playlist_mtime = self.playlist.stat().st_mtime
            except OSError:
                self._initial_playlist_mtime = 0.0
            self._proc = subprocess.Popen(
                self.build_command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            self._write_pid_file(self._proc.pid)
            self._reader = threading.Thread(
                target=self._read_stderr, name=f"ffmpeg-log-{self.spec.id}", daemon=True
            )
            self._reader.start()
            return self._proc.pid

    def _clean_output_dir(self) -> None:
        for pattern in ("*.ts", "*.m3u8", "*.m3u8.tmp", "*.tmp"):
            for path in self.out_dir.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _prepare_output_dir(self) -> None:
        """Limpa temporários e preserva a janela de replay entre reinícios."""
        for pattern in ("*.tmp", "*.m3u8.tmp"):
            for path in self.out_dir.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    pass
        if self.replay_seconds <= 0 or not self.playlist.exists():
            self._clean_output_dir()
            return
        self._prune_playlist()

    def _prune_playlist(self) -> None:
        """Remove da playlist entradas mais antigas que a janela de replay."""
        try:
            text = self.playlist.read_text(encoding="utf-8")
        except OSError:
            self._clean_output_dir()
            return

        media_sequence = 0
        discontinuity_sequence = 0
        target_duration = self.segment_seconds
        entries: list[tuple[bool, str, str]] = []
        pending_duration: str | None = None
        pending_discontinuity = False

        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    media_sequence = int(line.split(":", 1)[1])
                except ValueError:
                    media_sequence = 0
            elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
                try:
                    discontinuity_sequence = int(line.split(":", 1)[1])
                except ValueError:
                    discontinuity_sequence = 0
            elif line.startswith("#EXT-X-TARGETDURATION:"):
                try:
                    target_duration = int(float(line.split(":", 1)[1]))
                except ValueError:
                    pass
            elif line == "#EXT-X-DISCONTINUITY":
                pending_discontinuity = True
            elif line.startswith("#EXTINF:"):
                pending_duration = line
            elif not line.startswith("#") and pending_duration is not None:
                entries.append((pending_discontinuity, pending_duration, line))
                pending_duration = None
                pending_discontinuity = False

        if not entries:
            # Playlist vazia/corrompida: não dá para continuar com append_list.
            self._clean_output_dir()
            return

        now = time.time()
        first_kept: int | None = None
        dropped_discontinuities = 0
        for index, (has_discontinuity, _duration, name) in enumerate(entries):
            try:
                age = now - (self.out_dir / name).stat().st_mtime
            except OSError:
                if first_kept is None:
                    if has_discontinuity:
                        dropped_discontinuities += 1
                    continue
                self.logger.warning(
                    "[%s] playlist com segmento ausente (%s); reiniciando histórico",
                    self.spec.id, name,
                )
                self._clean_output_dir()
                return
            if age <= self.replay_seconds:
                first_kept = index
                break
            if has_discontinuity:
                dropped_discontinuities += 1

        if first_kept is None:
            self._clean_output_dir()
            return

        kept = entries[first_kept:]
        for _has_discontinuity, _duration, name in kept:
            if not (self.out_dir / name).is_file():
                self.logger.warning(
                    "[%s] playlist com segmento ausente (%s); reiniciando histórico",
                    self.spec.id, name,
                )
                self._clean_output_dir()
                return

        if first_kept > 0:
            for _has_discontinuity, _duration, name in entries[:first_kept]:
                try:
                    (self.out_dir / name).unlink()
                except OSError:
                    pass

            new_dseq = discontinuity_sequence + dropped_discontinuities
            lines = [
                "#EXTM3U",
                "#EXT-X-VERSION:6",
                f"#EXT-X-TARGETDURATION:{max(1, target_duration)}",
                f"#EXT-X-MEDIA-SEQUENCE:{media_sequence + first_kept}",
            ]
            if new_dseq:
                lines.append(f"#EXT-X-DISCONTINUITY-SEQUENCE:{new_dseq}")
            lines.append("#EXT-X-INDEPENDENT-SEGMENTS")
            for has_discontinuity, duration, name in kept:
                if has_discontinuity:
                    lines.append("#EXT-X-DISCONTINUITY")
                lines.append(duration)
                lines.append(name)
            tmp = self.out_dir / (self.playlist.name + ".tmp")
            try:
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, self.playlist)
            except OSError:
                return

        # Arquivos antigos fora da playlist não precisam ocupar espaço.
        referenced = {name for _d, _e, name in kept}
        cutoff = now - self.replay_seconds
        for path in self.out_dir.glob("seg_*.ts"):
            if path.name in referenced:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def _write_pid_file(self, pid: int) -> None:
        payload = {
            "pid": pid,
            "started_at": time.time(),
            "marker": str(self.out_dir.resolve()),
        }
        tmp = self.pid_file.with_suffix(".pid.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.pid_file)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                line = self._redact(line)[:2000]
                if self.source_url:
                    from source_resolver import is_noisy_source_line, redact_stream_urls
                    if is_noisy_source_line(line):
                        continue
                    line = redact_stream_urls(line)
                self.logger.warning("[%s] ffmpeg: %s", self.spec.id, line)
                if hasattr(self, "on_stderr"):
                    try:
                        self.on_stderr(line)
                    except Exception:  # noqa: BLE001
                        pass
                with self._log_lock:
                    try:
                        with self.log_file.open("a", encoding="utf-8") as fh:
                            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
                    except OSError:
                        pass
        except (ValueError, OSError):
            pass

    # ---------------------------------------------------------------- state
    @property
    def pid(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.pid

    def poll(self) -> int | None:
        if self._proc is None:
            return -1
        return self._proc.poll()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def playlist_age(self) -> float | None:
        try:
            return time.time() - self.playlist.stat().st_mtime
        except OSError:
            return None

    def playlist_fresh(self) -> bool:
        """A playlist foi escrita por este processo (não é histórico antigo)?"""
        try:
            return self.playlist.stat().st_mtime > self._initial_playlist_mtime
        except OSError:
            return False

    def newest_segment_age(self) -> float | None:
        newest = None
        try:
            for path in self.out_dir.glob("seg_*.ts"):
                mtime = path.stat().st_mtime
                if newest is None or mtime > newest:
                    newest = mtime
        except OSError:
            return None
        return None if newest is None else time.time() - newest

    # ----------------------------------------------------------------- stop
    def stop(self, grace: float = 6.0) -> None:
        with self._start_lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                self.logger.info("[%s] encerrando ffmpeg (pid=%s)", self.spec.id, proc.pid)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                deadline = time.time() + grace
                while time.time() < deadline and proc.poll() is None:
                    time.sleep(0.1)
                if proc.poll() is None:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        try:
                            proc.kill()
                        except OSError:
                            pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            self._proc = None
            try:
                self.pid_file.unlink()
            except OSError:
                pass
            self._cleanup_temp_files()

    def _cleanup_temp_files(self) -> None:
        try:
            for pattern in ("*.tmp", "*.m3u8.tmp"):
                for path in self.out_dir.glob(pattern):
                    try:
                        path.unlink()
                    except OSError:
                        pass
        except OSError:
            pass


def cleanup_stale_pidfile(pid_file: Path, marker: str, logger: logging.Logger) -> None:
    """Kill an orphaned ffmpeg from a previous run of *this* project only."""
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        try:
            pid_file.unlink()
        except OSError:
            pass
        return

    alive = False
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True

    if alive:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ")
        except OSError:
            cmdline = b""
        if marker.encode() in cmdline:
            logger.warning("encerrando ffmpeg órfão pid=%s (%s)", pid, marker)
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    break
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        else:
            logger.warning("pidfile órfão de outro processo ignorado (pid=%s)", pid)
    try:
        pid_file.unlink()
    except OSError:
        pass
