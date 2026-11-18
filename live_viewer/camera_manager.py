"""Camera configuration and supervision.

One supervisor thread per camera. It starts FFmpeg when the camera is
reachable, watches the HLS playlist, and restarts on exit, stall or request.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import socket
import stat
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ffmpeg_manager import FFmpegProcess, cleanup_stale_pidfile

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

RESTART_REASON = "Reinício solicitado pela interface"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraSpec:
    id: str
    name: str
    host: str
    port: int
    username: str
    secret: str
    path: str
    transport: str = "udp"
    enabled: bool = True
    output_width: int = 1280
    output_height: int = 0
    video_bitrate: str = "2500k"
    audio_bitrate: str = "64k"
    input_fps: int = 15

    def safe_url(self) -> str:
        return (f"rtsp://{self.username}:***@{self.host}:{self.port}{self.path}")


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8088
    base_path: str = ""


@dataclass
class HlsConfig:
    segment_seconds: int = 2
    playlist_size: int = 6
    delete_threshold: int = 2
    stall_timeout_seconds: int = 15
    start_grace_seconds: int = 12
    replay_minutes: int = 30


def _require(data: dict, key: str, where: str):
    if key not in data:
        raise ConfigError(f"campo obrigatório ausente em {where}: {key}")
    return data[key]


def _positive_int(value, default, where):
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where} deve ser inteiro")
    if number < 0:
        raise ConfigError(f"{where} não pode ser negativo")
    return number


def load_config(path: Path) -> tuple[ServerConfig, HlsConfig, list[CameraSpec]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"não foi possível ler {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON inválido em {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config.json deve ser um objeto JSON")

    server_raw = raw.get("server") or {}
    base_path = str(server_raw.get("base_path", "") or "").strip()
    if base_path and not base_path.startswith("/"):
        base_path = "/" + base_path
    base_path = base_path.rstrip("/")
    if base_path == "/":
        base_path = ""
    server = ServerConfig(
        host=str(server_raw.get("host", "127.0.0.1")),
        port=int(server_raw.get("port", 8088)),
        base_path=base_path,
    )
    if not (1 <= server.port <= 65535):
        raise ConfigError("server.port inválido")

    hls_raw = raw.get("hls") or {}
    try:
        replay_minutes = int(hls_raw.get("replay_minutes", 30))
    except (TypeError, ValueError):
        raise ConfigError("hls.replay_minutes deve ser inteiro")
    # Limite duro: o replay nunca passa de 30 minutos.
    replay_minutes = max(0, min(30, replay_minutes))
    hls = HlsConfig(
        segment_seconds=max(1, int(hls_raw.get("segment_seconds", 2))),
        playlist_size=max(2, int(hls_raw.get("playlist_size", 6))),
        delete_threshold=max(0, int(hls_raw.get("delete_threshold", 2))),
        stall_timeout_seconds=max(5, int(hls_raw.get("stall_timeout_seconds", 15))),
        start_grace_seconds=max(5, int(hls_raw.get("start_grace_seconds", 12))),
        replay_minutes=replay_minutes,
    )

    cameras_raw = raw.get("cameras")
    if not isinstance(cameras_raw, list) or not cameras_raw:
        raise ConfigError("config.json precisa ter ao menos uma câmera em 'cameras'")

    cameras: list[CameraSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(cameras_raw):
        where = f"cameras[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} deve ser um objeto")
        cam_id = str(_require(item, "id", where)).strip()
        if not ID_RE.fullmatch(cam_id):
            raise ConfigError(f"{where}.id inválido (use letras, números, '-' ou '_')")
        if cam_id in seen:
            raise ConfigError(f"id duplicado: {cam_id}")
        seen.add(cam_id)

        host = str(_require(item, "host", where)).strip()
        if not host:
            raise ConfigError(f"{where}.host vazio")
        try:
            port = int(item.get("port", 554))
        except (TypeError, ValueError):
            raise ConfigError(f"{where}.port inválido")
        if not (1 <= port <= 65535):
            raise ConfigError(f"{where}.port fora do intervalo")

        username = str(item.get("username", "admin")).strip()
        if not username:
            raise ConfigError(f"{where}.username vazio")
        secret = str(_require(item, "secret", where)).strip()
        if not secret:
            raise ConfigError(f"{where}.secret vazio")

        path = str(item.get("path", "/onvif1")).strip()
        if not path.startswith("/"):
            raise ConfigError(f"{where}.path deve começar com '/'")

        transport = str(item.get("transport", "udp")).lower()
        if transport not in {"udp", "tcp"}:
            raise ConfigError(f"{where}.transport deve ser 'udp' ou 'tcp'")

        cameras.append(CameraSpec(
            id=cam_id,
            name=str(item.get("name", cam_id)),
            host=host,
            port=port,
            username=username,
            secret=secret,
            path=path,
            transport=transport,
            enabled=bool(item.get("enabled", True)),
            output_width=_positive_int(item.get("output_width", 1280), 1280, f"{where}.output_width"),
            output_height=_positive_int(item.get("output_height", 0), 0, f"{where}.output_height"),
            video_bitrate=str(item.get("video_bitrate", "2500k")),
            audio_bitrate=str(item.get("audio_bitrate", "64k")),
            input_fps=max(1, int(item.get("input_fps", 15))),
        ))
    return server, hls, cameras


def load_secrets(path: Path, logger: logging.Logger) -> dict[str, str]:
    secrets: dict[str, str] = {}
    if path.exists():
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                logger.warning("permissões de %s são %o; recomendado 600", path.name, mode)
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                secrets = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"não foi possível ler {path.name}: {exc}") from exc

    # Environment overrides: YV_SECRET_<KEY> (KEY em minúsculas/maiúsculas)
    for env_name, value in os.environ.items():
        if env_name.startswith("YV_SECRET_") and value:
            secrets[env_name[len("YV_SECRET_"):].lower()] = value
    for key in list(secrets):
        env_key = f"YV_SECRET_{key.upper()}"
        if env_key in os.environ:
            secrets[key] = os.environ[env_key]
    return secrets


def history_seconds(out_dir: Path, segment_seconds: int, limit_seconds: int) -> float:
    """Duração da janela HLS atual (= o quanto o player consegue voltar)."""
    if limit_seconds <= 0:
        return 0.0
    playlist = out_dir / "stream.m3u8"
    total = 0.0
    count = 0
    try:
        with playlist.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("#EXTINF:"):
                    continue
                value = line[len("#EXTINF:"):].split(",", 1)[0].strip()
                try:
                    total += float(value)
                except ValueError:
                    total += max(1, segment_seconds)
                count += 1
                if count >= 20000:  # sanidade
                    break
    except OSError:
        return 0.0
    if count == 0:
        return 0.0
    return round(min(float(limit_seconds), max(0.0, total)), 1)


def replay_window_bounds(out_dir: Path, segment_seconds: int, limit_seconds: int) -> dict:
    """Estimativa de início/fim da janela de replay com base nos segmentos."""
    bounds = {"window_start": None, "window_end": None}
    if limit_seconds <= 0:
        return bounds
    playlist = out_dir / "stream.m3u8"
    segments: list[str] = []
    try:
        with playlist.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                segments.append(line)
    except OSError:
        return bounds
    if not segments:
        return bounds
    try:
        first_m = (out_dir / segments[0]).stat().st_mtime
        last_m = (out_dir / segments[-1]).stat().st_mtime
    except OSError:
        return bounds
    window_end = last_m + max(1, segment_seconds)
    duration = history_seconds(out_dir, segment_seconds, limit_seconds)
    window_start = max(first_m, window_end - duration)
    bounds["window_start"] = datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat()
    bounds["window_end"] = datetime.fromtimestamp(window_end, tz=timezone.utc).isoformat()
    return bounds


class CameraRuntime:
    def __init__(self, spec: CameraSpec, password: str, base_dir: Path,
                 hls_cfg: HlsConfig, logger: logging.Logger, redact, base_path: str = ""):
        self.spec = spec
        self.password = password
        self.base_dir = base_dir
        self.hls_cfg = hls_cfg
        self.logger = logger
        self.redact = redact
        self.base_path = (base_path or "").rstrip("/")
        self.proc: FFmpegProcess | None = None
        self.state = "stopped"
        self.message = "Aguardando"
        self.last_error = ""
        self.last_error_at: float | None = None
        self.restarts = 0
        # Pedidos de reinício são numerados: um clique só pode ser consumido
        # uma vez, mesmo se chegar enquanto a câmera ainda está conectando.
        self.restart_seq = 0
        self.handled_restart_seq = 0
        self.started_at: float | None = None
        self.connected_since: float | None = None
        self.total_connected_seconds = 0.0
        self.last_frame_at: float | None = None
        self.last_progress_at: float | None = None
        self.detected_resolution: str | None = None
        self.detected_fps: str | None = None
        self.updated_at = time.time()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def set_state(self, state: str, message: str) -> None:
        with self.lock:
            self.state = state
            self.message = message
            self.updated_at = time.time()
        self.logger.info("[%s] estado=%s: %s", self.spec.id, state, message)

    def set_error(self, error: str) -> None:
        with self.lock:
            self.last_error = self.redact(error)[:500]
            self.last_error_at = time.time()
            self.updated_at = time.time()

    def clear_proc(self) -> None:
        with self.lock:
            self.proc = None

    def inc_restarts(self) -> None:
        with self.lock:
            self.restarts += 1

    def note_connected(self) -> None:
        with self.lock:
            if self.connected_since is None:
                self.connected_since = time.time()

    def note_disconnected(self) -> None:
        with self.lock:
            if self.connected_since is not None:
                self.total_connected_seconds += max(0.0, time.time() - self.connected_since)
                self.connected_since = None

    def on_ffmpeg_stderr(self, line: str) -> None:
        m = re.search(r"Stream #\d+:\d+.*Video:[^,]*,\s*[^,]*,\s*(\d{2,5}x\d{2,5})", line)
        if m:
            with self.lock:
                self.detected_resolution = m.group(1)
        m = re.search(r",\s*([0-9]+(?:\.[0-9]+)?)\s*fps\b", line)
        if m:
            with self.lock:
                self.detected_fps = m.group(1)
        # Progresso de encoding/transmissão: "frame=..." e linhas com "time=" indicam avanço.
        if line.startswith("frame=") or " time=" in line or " bitrate=" in line:
            now = time.time()
            with self.lock:
                self.last_frame_at = now
                self.last_progress_at = now

    def snapshot(self) -> dict:
        with self.lock:
            proc = self.proc
            state = self.state
            message = self.message
            last_error = self.last_error
            last_error_at = self.last_error_at
            restarts = self.restarts
            started_at = self.started_at
            updated_at = self.updated_at
            connected_since = self.connected_since
            total_connected = self.total_connected_seconds
            if connected_since is not None:
                total_connected += max(0.0, time.time() - connected_since)
            last_frame_at = self.last_frame_at
            last_progress_at = self.last_progress_at
            detected_resolution = self.detected_resolution
            detected_fps = self.detected_fps
        playlist_age = proc.playlist_age() if proc else None
        segment_age = proc.newest_segment_age() if proc else None
        last_segment_mtime = None
        if proc is not None:
            newest = proc.newest_segment_age()
            if newest is not None:
                last_segment_mtime = time.time() - newest
        replay_seconds = max(0, min(30, int(self.hls_cfg.replay_minutes))) * 60
        stream_dir = self.base_dir / "streams" / self.spec.id
        history = history_seconds(stream_dir, self.hls_cfg.segment_seconds, replay_seconds)
        bounds = replay_window_bounds(stream_dir, self.hls_cfg.segment_seconds, replay_seconds)
        ui_state = state
        if state == "running":
            ui_state = "live"
        elif state == "starting":
            ui_state = "connecting"
        elif state == "unreachable":
            ui_state = "offline"
        elif state == "error":
            ui_state = "error"
        elif state == "reconnecting":
            ui_state = "reconnecting"
        ui_labels = {
            "connecting": "Conectando",
            "live": "Ao vivo",
            "replay": "Replay",
            "reconnecting": "Reconectando",
            "offline": "Offline",
            "error": "Erro na transmissão",
            "disabled": "Desativada",
            "stopped": "Parada",
        }
        stall_limit = self.hls_cfg.stall_timeout_seconds
        is_stalled = bool(state == "running" and (
            (playlist_age is not None and playlist_age > stall_limit) or
            (segment_age is not None and segment_age > stall_limit)
        ))
        stream_healthy = bool(state == "running" and not is_stalled and playlist_age is not None and playlist_age <= stall_limit)
        return {
            "id": self.spec.id,
            "name": self.spec.name,
            "state": state,
            "ui_state": ui_state,
            "ui_state_label": ui_labels.get(ui_state, ui_state),
            "message": message,
            "last_error": last_error,
            "last_error_at": _iso(last_error_at),
            "restarts": restarts,
            "pid": proc.pid if proc else None,
            "started_at": _iso(started_at),
            "updated_at": _iso(updated_at),
            "connected_since": _iso(connected_since),
            "connection_uptime_seconds": round(total_connected, 1),
            "last_frame_at": _iso(last_frame_at),
            "last_progress_at": _iso(last_progress_at),
            "last_segment_at": _iso(last_segment_mtime),
            "playlist_age_seconds": None if playlist_age is None else round(playlist_age, 1),
            "segment_age_seconds": None if segment_age is None else round(segment_age, 1),
            "stall_timeout_seconds": stall_limit,
            "is_stalled": is_stalled,
            "stream_healthy": stream_healthy,
            "replay_enabled": replay_seconds > 0,
            "replay_seconds": replay_seconds,
            "history_seconds": history,
            "window_start": bounds["window_start"],
            "window_end": bounds["window_end"],
            "detected_resolution": detected_resolution,
            "detected_fps": detected_fps,
            "configured_resolution": (f"{self.spec.output_width}x{self.spec.output_height}" if self.spec.output_height else f"{self.spec.output_width}w"),
            "configured_input_fps": self.spec.input_fps,
            "hls_url": f"{self.base_path}/streams/{self.spec.id}/stream.m3u8",
            "rtsp": self.spec.safe_url(),
            "transport": self.spec.transport,
        }


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


class CameraManager:
    def __init__(self, cameras: list[CameraSpec], secrets: dict[str, str],
                 hls_cfg: HlsConfig, base_dir: Path, logger: logging.Logger,
                 redact, base_path: str = ""):
        self.hls_cfg = hls_cfg
        self.base_dir = base_dir
        self.logger = logger
        self.redact = redact
        self.base_path = (base_path or "").rstrip("/")
        self.stop_event = threading.Event()
        self.runtimes: dict[str, CameraRuntime] = {}
        self._runtimes_lock = threading.RLock()
        self._restart_lock = threading.Lock()

        streams_root = base_dir / "streams"
        streams_root.mkdir(parents=True, exist_ok=True)
        for spec in cameras:
            self.runtimes[spec.id] = self._build_runtime(spec, secrets)

    def _build_runtime(self, spec: CameraSpec, secrets: dict[str, str]) -> CameraRuntime:
        if spec.secret not in secrets or not secrets[spec.secret]:
            raise ConfigError(
                f"segredo '{spec.secret}' da câmera '{spec.id}' não encontrado. "
                f"Defina em secrets.json ou na variável YV_SECRET_{spec.secret.upper()}."
            )
        cam_dir = self.base_dir / "streams" / spec.id
        cam_dir.mkdir(parents=True, exist_ok=True)
        cleanup_stale_pidfile(cam_dir / ".ffmpeg.pid", str(cam_dir.resolve()), self.logger)
        return CameraRuntime(
            spec, secrets[spec.secret], self.base_dir, self.hls_cfg,
            self.logger, self.redact, self.base_path,
        )

    def add_camera(self, spec: CameraSpec, password: str) -> None:
        """Registra uma câmera nova e inicia seu supervisor sem reiniciar o viewer."""
        with self._runtimes_lock:
            if spec.id in self.runtimes:
                raise ConfigError(f"já existe uma câmera com o id '{spec.id}'.")
            if self.stop_event.is_set():
                raise ConfigError("o visualizador está encerrando; tente novamente.")

            cam_dir = self.base_dir / "streams" / spec.id
            cam_dir.mkdir(parents=True, exist_ok=True)
            cleanup_stale_pidfile(cam_dir / ".ffmpeg.pid", str(cam_dir.resolve()), self.logger)
            runtime = CameraRuntime(
                spec, password, self.base_dir, self.hls_cfg,
                self.logger, self.redact, self.base_path,
            )
            self.runtimes[spec.id] = runtime

        thread = threading.Thread(
            target=self._supervise, args=(runtime,),
            name=f"cam-{spec.id}", daemon=False,
        )
        runtime.thread = thread
        thread.start()
        self.logger.info("câmera %s (%s) adicionada; supervisor iniciado", spec.id, spec.name)

    def rename_camera(self, camera_id: str, name: str) -> CameraSpec | None:
        """Troca o nome amigável sem reiniciar o stream da câmera."""
        with self._runtimes_lock:
            runtime = self.runtimes.get(camera_id)
            if runtime is None:
                return None
            new_spec = replace(runtime.spec, name=name)
            runtime.spec = new_spec
            return new_spec

    def start(self) -> None:
        with self._runtimes_lock:
            runtimes = list(self.runtimes.values())
        for runtime in runtimes:
            thread = threading.Thread(
                target=self._supervise, args=(runtime,),
                name=f"cam-{runtime.spec.id}", daemon=False,
            )
            runtime.thread = thread
            thread.start()

    def stop(self) -> None:
        self.logger.info("parando supervisor de câmeras")
        self.stop_event.set()
        with self._runtimes_lock:
            runtimes = list(self.runtimes.values())
        for runtime in runtimes:
            if runtime.thread is not None:
                runtime.thread.join(timeout=15)
        for runtime in runtimes:
            with runtime.lock:
                proc = runtime.proc
            if proc is not None:
                proc.stop()
                runtime.clear_proc()
            runtime.note_disconnected()
            runtime.set_state("stopped", "Aplicação encerrada")
        self.logger.info("supervisor encerrado")

    def request_restart(self, camera_id: str) -> bool:
        with self._runtimes_lock:
            runtime = self.runtimes.get(camera_id)
            if runtime is None:
                return False
            with self._restart_lock:
                runtime.restart_seq += 1
        runtime.set_state("reconnecting", RESTART_REASON)
        return True

    def snapshot(self) -> list[dict]:
        with self._runtimes_lock:
            runtimes = list(self.runtimes.values())
        return [runtime.snapshot() for runtime in runtimes]

    # ------------------------------------------------------------- internals
    def _consume_restart(self, runtime: CameraRuntime) -> bool:
        """Consome um pedido de reinício pendente, no máximo uma vez por clique."""
        with self._restart_lock:
            if runtime.restart_seq <= runtime.handled_restart_seq:
                return False
            runtime.handled_restart_seq = runtime.restart_seq
        runtime.inc_restarts()
        return True

    def _sleep(self, seconds: float, runtime: CameraRuntime | None = None) -> bool:
        """Dorme, mas acorda antes se um reinício for solicitado.

        Devolve True quando o pedido foi consumido (o chamador deve reiniciar).
        """
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            if runtime is not None and self._consume_restart(runtime):
                return True
            time.sleep(0.2)
        return False

    def _reachable(self, spec: CameraSpec) -> bool:
        try:
            with socket.create_connection((spec.host, spec.port), timeout=3):
                return True
        except OSError:
            return False

    def _supervise(self, runtime: CameraRuntime) -> None:
        spec = runtime.spec
        backoff = 1.0
        while not self.stop_event.is_set():
            # Reconhece cliques que chegaram antes de existir stream ativo.
            if self._consume_restart(runtime):
                runtime.set_state("reconnecting", RESTART_REASON)
                continue
            if not spec.enabled:
                runtime.note_disconnected()
                runtime.set_state("disabled", "Câmera desativada na configuração")
                self._sleep(3, runtime)
                continue

            if not self._reachable(spec):
                runtime.note_disconnected()
                runtime.set_state(
                    "unreachable",
                    f"Câmera inacessível em {spec.host}:{spec.port}. Tentando novamente…",
                )
                self._sleep(min(backoff, 10) + random.uniform(0, 0.4), runtime)
                backoff = min(backoff * 2, 30)
                continue

            runtime.set_state("starting", "Conectando e aguardando playlist HLS")
            proc = FFmpegProcess(spec, runtime.password, self.base_dir,
                                 self.hls_cfg, self.logger)
            proc.on_stderr = runtime.on_ffmpeg_stderr
            with runtime.lock:
                runtime.proc = proc
                runtime.last_progress_at = time.time()
            try:
                runtime.started_at = time.time()
                proc.start()
            except Exception as exc:  # noqa: BLE001 - report and retry
                runtime.set_error(f"Falha ao iniciar FFmpeg: {exc}")
                runtime.set_state("error", "Falha ao iniciar FFmpeg")
                runtime.clear_proc()
                self._sleep(backoff + random.uniform(0, 0.4), runtime)
                backoff = min(backoff * 2, 30)
                continue

            wait_reason = self._wait_for_playlist(runtime, proc)
            if self.stop_event.is_set():
                runtime.note_disconnected()
                proc.stop()
                runtime.clear_proc()
                break
            if wait_reason is not None:
                reason = wait_reason
                runtime.set_error(reason)
                runtime.set_state("reconnecting", reason)
                runtime.note_disconnected()
                proc.stop()
                runtime.clear_proc()
                if reason != RESTART_REASON:
                    runtime.inc_restarts()
                self._sleep(backoff + random.uniform(0, 0.4), runtime)
                backoff = min(backoff * 2, 30)
                continue

            runtime.note_connected()
            runtime.set_state("running", "Transmissão ativa")
            stable_since = time.time()
            reason = None
            last_reach_check = time.time()

            while not self.stop_event.is_set():
                if self._consume_restart(runtime):
                    reason = RESTART_REASON
                    break
                rc = proc.poll()
                if rc is not None:
                    reason = f"FFmpeg encerrou inesperadamente (código {rc})"
                    break
                age = proc.playlist_age()
                seg_age = proc.newest_segment_age()
                if age is None:
                    reason = "Playlist HLS desapareceu"
                    break
                stall_limit = self.hls_cfg.stall_timeout_seconds
                if age > stall_limit:
                    reason = (
                        f"Sem atualização da playlist há {age:.0f}s "
                        f"(limite {stall_limit}s)"
                    )
                    break
                if seg_age is not None and seg_age > stall_limit:
                    reason = (
                        f"Sem novos segmentos há {seg_age:.0f}s "
                        f"(limite {stall_limit}s) — possível congelamento"
                    )
                    break
                # FFmpeg ativo, mas sem progresso reportado por muito tempo além do esperado.
                with runtime.lock:
                    last_progress = runtime.last_progress_at
                if last_progress is not None and (time.time() - last_progress) > (stall_limit + 10):
                    reason = (
                        f"FFmpeg sem progresso há {time.time() - last_progress:.0f}s "
                        f"(limite {stall_limit + 10}s)"
                    )
                    break
                now = time.time()
                if now - last_reach_check >= 10:
                    last_reach_check = now
                    if not self._reachable(spec):
                        reason = "Câmera inacessível durante a transmissão"
                        break
                if now - stable_since > 30:
                    backoff = 1.0
                if self._sleep(1, runtime):
                    reason = RESTART_REASON
                    break

            if self.stop_event.is_set():
                runtime.note_disconnected()
                proc.stop()
                runtime.clear_proc()
                break

            runtime.set_error(reason or "Reconectando")
            runtime.set_state("reconnecting", reason or "Reconectando")
            runtime.note_disconnected()
            proc.stop()
            runtime.clear_proc()
            if reason != RESTART_REASON:
                runtime.inc_restarts()
            self._sleep(backoff + random.uniform(0, 0.4), runtime)
            backoff = min(backoff * 2, 30)

    def _wait_for_playlist(self, runtime: CameraRuntime, proc: FFmpegProcess) -> str | None:
        """None = playlist pronta; string = motivo pelo qual não ficou pronta."""
        deadline = time.time() + self.hls_cfg.start_grace_seconds
        while time.time() < deadline and not self.stop_event.is_set():
            if self._consume_restart(runtime):
                return RESTART_REASON
            rc = proc.poll()
            if rc is not None:
                return f"FFmpeg encerrou durante a inicialização (código {rc})"
            age = proc.playlist_age()
            if age is not None and age <= 5 and proc.playlist_fresh():
                return None
            if self._sleep(0.5, runtime):
                return RESTART_REASON
        return "FFmpeg não gerou playlist HLS no tempo esperado"
