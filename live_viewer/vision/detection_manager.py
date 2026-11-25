"""Detecção de pessoas, recortes laterais e eventos para o Live Viewer.

Ideia aproveitada do ClearCam (apenas a parte de visão computacional):

    HLS local -> frames BGR -> YOLOv9 -> OC-SORT -> filtro por track_id
              -> caixas de pessoas no player + evento com snapshot anotada
              -> YuNet/rosto à esquerda + heurística/placa à direita

Não há segunda conexão RTSP: um FFmpeg auxiliar lê a playlist HLS que o
próprio viewer já produz. O modelo é compartilhado entre câmeras; cada câmera
tem seu próprio rastreador e estado.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from camera_manager import DetectionConfig, CameraSpec
from vision.coco import label as class_label
from vision.crops import FaceCropper, crop_with_padding, find_plate, resize_keep_aspect
from vision.yolov9_onnx import YOLOv9Detector

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TRACK_ID_RE = re.compile(r"_(\d+)$")
PERSON_CLASS_ID = 0
# Usados só internamente para tentar capturar placas; nunca aparecem no player.
VEHICLE_CLASS_IDS = (2, 3, 5, 7)  # car, motorcycle, bus, truck
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


@dataclass
class CameraDetectionState:
    camera_id: str
    enabled: bool = True
    status: str = "idle"
    message: str = "Aguardando stream"
    frame_width: int = 0
    frame_height: int = 0
    detections: list[dict] = field(default_factory=list)
    faces: list[dict] = field(default_factory=list)
    plates: list[dict] = field(default_factory=list)
    updated_at: float | None = None
    events: int = 0

    def as_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "enabled": self.enabled,
            "status": self.status,
            "message": self.message,
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "detections": list(self.detections),
            "detection_count": len(self.detections),
            "faces": list(self.faces),
            "plates": list(self.plates),
            "events": self.events,
            "updated_at": _iso(self.updated_at),
        }


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _track_id_from_name(name: str) -> int | None:
    match = TRACK_ID_RE.search(Path(name).stem)
    return int(match.group(1)) if match else None


class _FrameReader:
    """Lê a playlist HLS e entrega frames BGR em pipe, no FPS configurado."""

    def __init__(self, playlist: Path, fps: float, width: int, height: int,
                 cam_id: str, logger: logging.Logger):
        self.playlist = playlist
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self.logger = logger
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.cmd = [
            FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-re", "-live_start_index", "-1",
            "-i", str(playlist),
            "-vf", f"fps={fps:.3f},scale={width}:{height}",
            "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        self.logger.info("[%s][visão] iniciando leitor HLS: %sx%s @ %sfps",
                         cam_id, width, height, fps)

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    def poll(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def read(self):
        """Devolve um frame numpy ou None quando o pipe fecha."""
        import numpy as np

        if self.proc is None or self.proc.stdout is None:
            return None
        buffer = bytearray()
        while len(buffer) < self.frame_size:
            try:
                chunk = self.proc.stdout.read(self.frame_size - len(buffer))
            except (OSError, ValueError):  # pipe fechado por stop()
                return None
            if not chunk:
                return None
            buffer.extend(chunk)
        return np.frombuffer(bytes(buffer), dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        ).copy()

    def stop(self) -> None:
        # stop() pode ser chamado pelo worker e por remove_camera()/stop() ao
        # mesmo tempo: retira o processo daqui sob lock e trabalha na cópia.
        with self._lock:
            proc = self.proc
            self.proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), 15)
                else:
                    proc.terminate()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(os.getpgid(proc.pid), 9)
                    except (ProcessLookupError, OSError):
                        pass
                else:
                    proc.kill()
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass


def _probe_resolution(playlist: Path, spec: CameraSpec) -> tuple[int, int]:
    """Descobre a resolução do stream sem abrir outra conexão RTSP."""
    if shutil.which("ffprobe") and playlist.exists():
        try:
            result = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 str(playlist)],
                capture_output=True, text=True, timeout=8, check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    width, height = int(parts[0]), int(parts[1])
                    if width >= 160 and height >= 90:
                        return width, height
        except (OSError, subprocess.SubprocessError):
            pass
    width = max(160, int(spec.output_width))
    height = int(spec.output_height) or max(90, int(width * 9 / 16))
    return width, height


class DetectionManager:
    def __init__(self, config: DetectionConfig, cameras: list[CameraSpec],
                 base_dir: Path, logger: logging.Logger, base_path: str = ""):
        self.config = config
        self.cameras = {spec.id: spec for spec in cameras}
        self.base_dir = Path(base_dir)
        self.events_dir = self.base_dir / "events"
        self.models_dir = self.base_dir / "models"
        self.logger = logger
        self.base_path = (base_path or "").rstrip("/")
        self.stop_event = threading.Event()
        self._detector: YOLOv9Detector | None = None
        self._detector_lock = threading.RLock()
        self._detector_error = ""
        self._state_lock = threading.RLock()
        self._states: dict[str, CameraDetectionState] = {}
        self._events_lock = threading.RLock()
        self._memory_events: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._seen_tracks: dict[str, set[int]] = defaultdict(set)
        self._last_event: dict[str, float] = defaultdict(float)
        self._threads: dict[str, threading.Thread] = {}
        self._readers: dict[str, _FrameReader] = {}
        self._readers_lock = threading.RLock()

        queue_size = max(1, int(self.config.crop_queue_size))
        self._faces: dict[str, deque] = defaultdict(lambda: deque(maxlen=queue_size))
        self._plates: dict[str, deque] = defaultdict(lambda: deque(maxlen=queue_size))
        self._seen_faces: dict[str, set[int]] = defaultdict(set)
        self._seen_plates: dict[str, set[int]] = defaultdict(set)
        self._face_attempts: dict[str, dict[int, int]] = defaultdict(dict)
        self._plate_attempts: dict[str, dict[int, int]] = defaultdict(dict)
        self._crop_lock = threading.RLock()
        self._face_cropper: FaceCropper | None = None
        self._face_cropper_error = ""
        # Um event por câmera permite remover apenas uma sem parar as demais.
        self._camera_stop: dict[str, threading.Event] = {
            spec.id: threading.Event() for spec in cameras
        }
        for spec in cameras:
            enabled = self._enabled_for(spec)
            self._states[spec.id] = CameraDetectionState(
                camera_id=spec.id,
                enabled=enabled,
                status="idle" if enabled else "disabled",
                message="Aguardando stream" if enabled else "Detecção desativada",
            )
            self._load_recent_events(spec.id)
            self._load_recent_crops(spec.id)
            with self._events_lock:
                self._states[spec.id].events = len(self._memory_events.get(spec.id, ()))
                self._states[spec.id].faces = list(self._faces.get(spec.id, ()))
                self._states[spec.id].plates = list(self._plates.get(spec.id, ()))

    # ------------------------------------------------------------------ API
    def add_camera(self, spec: CameraSpec) -> None:
        """Registra uma câmera criada pela interface e inicia sua detecção."""
        enabled = self._enabled_for(spec)
        with self._state_lock:
            stop = self._camera_stop.get(spec.id)
            if stop is None:
                stop = threading.Event()
                self._camera_stop[spec.id] = stop
        with self._state_lock:
            known = spec.id in self._states
        if not known:
            self._load_recent_events(spec.id)
            self._load_recent_crops(spec.id)
        with self._events_lock:
            count = len(self._memory_events.get(spec.id, ()))
        with self._state_lock:
            self.cameras[spec.id] = spec
            state = self._states.get(spec.id)
            if state is None:
                state = CameraDetectionState(
                    camera_id=spec.id,
                    enabled=enabled,
                    status="idle" if enabled else "disabled",
                    message="Aguardando stream" if enabled else "Detecção desativada",
                )
                self._states[spec.id] = state
            state.enabled = enabled
            state.events = count
            with self._events_lock:
                state.faces = list(self._faces.get(spec.id, ()))
                state.plates = list(self._plates.get(spec.id, ()))
            if not enabled:
                state.status = "disabled"
                state.message = "Detecção desativada"
            elif self._detector_error:
                state.status = "error"
                state.message = "Modelo de visão indisponível"

        thread = self._threads.get(spec.id)
        if not enabled or (thread is not None and thread.is_alive()):
            return
        with self._detector_lock:
            if self._detector is None:
                return
        thread = threading.Thread(
            target=self._worker, args=(spec, stop),
            name=f"vision-{spec.id}", daemon=True,
        )
        self._threads[spec.id] = thread
        thread.start()

    def rename_camera(self, camera_id: str, spec: CameraSpec) -> None:
        """Atualiza o nome usado nos eventos após uma renomeação pela UI."""
        with self._state_lock:
            if camera_id in self.cameras:
                self.cameras[camera_id] = spec

    def remove_camera(self, camera_id: str) -> bool:
        """Encerra o worker de uma câmera e apaga seus estados e eventos."""
        with self._state_lock:
            known = camera_id in self._states or camera_id in self.cameras
            self.cameras.pop(camera_id, None)
            self._states.pop(camera_id, None)
            stop = self._camera_stop.pop(camera_id, None)
        if stop is not None:
            stop.set()
        with self._readers_lock:
            reader = self._readers.pop(camera_id, None)
        if reader is not None:
            # Desbloqueia o read() em andamento para o worker encerrar logo.
            reader.stop()
        thread = self._threads.pop(camera_id, None)
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=8)
        with self._events_lock:
            self._memory_events.pop(camera_id, None)
            self._seen_tracks.pop(camera_id, None)
            self._last_event.pop(camera_id, None)
            self._faces.pop(camera_id, None)
            self._plates.pop(camera_id, None)
            self._seen_faces.pop(camera_id, None)
            self._seen_plates.pop(camera_id, None)
            self._face_attempts.pop(camera_id, None)
            self._plate_attempts.pop(camera_id, None)
        shutil.rmtree(self.events_dir / camera_id, ignore_errors=True)
        if known:
            self.logger.info("[visão] câmera %s removida da detecção", camera_id)
        return known

    def _stopped(self, stop: threading.Event) -> bool:
        return self.stop_event.is_set() or stop.is_set()

    def _pause(self, stop: threading.Event, seconds: float) -> bool:
        """Espera até `seconds`, acordando se a câmera ou o viewer parar."""
        deadline = time.monotonic() + seconds
        while not self._stopped(stop) and time.monotonic() < deadline:
            self.stop_event.wait(min(0.2, max(0.0, deadline - time.monotonic())))
        return self._stopped(stop)

    def _enabled_for(self, spec: CameraSpec) -> bool:
        if not self.config.enabled:
            return False
        if spec.detection_enabled is not None:
            return bool(spec.detection_enabled)
        return True

    def start(self) -> None:
        if not self.config.enabled:
            self.logger.info("[visão] detecção desativada na configuração")
            return
        try:
            with self._detector_lock:
                name = Path(self.config.model).name or "yolov9t.onnx"
                model_path = (
                    Path(self.config.model).expanduser()
                    if os.path.isabs(self.config.model)
                    else self.models_dir / name
                )
                self._detector = YOLOv9Detector(
                    model_path,
                    input_size=self.config.input_size,
                    confidence=self.config.confidence,
                    iou=self.config.iou,
                    # Pessoas aparecem no player; veículos são detectados só
                    # para tentar recortar a placa.
                    classes=set(self.config.classes) | set(VEHICLE_CLASS_IDS) | {PERSON_CLASS_ID},
                    prefer_cuda=self.config.prefer_cuda,
                    model_url=self.config.model_url,
                    logger=self.logger,
                )
        except Exception as exc:  # noqa: BLE001 - o viewer precisa continuar sem IA
            self._detector_error = str(exc)
            self.logger.warning(
                "[visão] detecção indisponível (%s); o viewer continuará sem IA", exc
            )
            with self._state_lock:
                for state in self._states.values():
                    if state.enabled:
                        state.status = "error"
                        state.message = "Modelo de visão indisponível"
            return

        face_name = Path(self.config.face_model).name or "face_detection_yunet_2023mar.onnx"
        face_path = (
            Path(self.config.face_model).expanduser()
            if os.path.isabs(self.config.face_model)
            else self.models_dir / face_name
        )
        with self._crop_lock:
            self._face_cropper = FaceCropper(
                face_path,
                self.config.face_model_url,
                self.config.face_confidence,
                self.logger,
            )
            self._face_cropper_error = self._face_cropper.error
        if self._face_cropper_error:
            self.logger.warning(
                "[visão] recorte de rosto desativado (%s); placas continuam ativas",
                self._face_cropper_error,
            )

        for spec in self.cameras.values():
            if not self._enabled_for(spec):
                continue
            stop = self._camera_stop.setdefault(spec.id, threading.Event())
            thread = threading.Thread(
                target=self._worker, args=(spec, stop),
                name=f"vision-{spec.id}", daemon=True,
            )
            self._threads[spec.id] = thread
            thread.start()
        self.logger.info("[visão] detecção ativa em %d câmera(s)", len(self._threads))

    def stop(self) -> None:
        self.stop_event.set()
        for event in self._camera_stop.values():
            event.set()
        with self._readers_lock:
            readers = list(self._readers.values())
            self._readers.clear()
        for reader in readers:
            reader.stop()
        for thread in self._threads.values():
            thread.join(timeout=6)
        self.logger.info("[visão] workers encerrados")

    def status(self) -> dict:
        with self._state_lock:
            cameras = [state.as_dict() for state in self._states.values()]
            total_events = sum(state.events for state in self._states.values())
        return {
            "enabled": bool(self.config.enabled),
            "model": Path(self.config.model).name,
            "model_error": self._detector_error or None,
            "face_model_error": self._face_cropper_error or None,
            "classes": list(self.config.classes),
            "crop_queue_size": int(self.config.crop_queue_size),
            "fps": self.config.fps,
            "events": total_events,
            "cameras": cameras,
        }

    def camera_state(self, camera_id: str) -> dict | None:
        with self._state_lock:
            state = self._states.get(camera_id)
            return state.as_dict() if state else None

    def events(self, camera_id: str | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(500, int(limit)))
        if camera_id is not None and camera_id not in self.cameras:
            return []
        ids = [camera_id] if camera_id else list(self.cameras)
        result: list[dict] = []
        with self._events_lock:
            for cam_id in ids:
                result.extend(reversed(list(self._memory_events.get(cam_id, ()))))
        result.sort(key=lambda event: event.get("timestamp", ""), reverse=True)
        return result[:limit]

    def snapshot_path(self, camera_id: str, filename: str) -> Path | None:
        """Resolve um snapshot validando o nome (usado pelo servidor HTTP)."""
        if not ID_RE.fullmatch(camera_id) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.jpg", filename):
            return None
        if ".." in filename:
            return None
        path = (self.events_dir / camera_id / filename).resolve()
        root = (self.events_dir / camera_id).resolve()
        if path.parent != root or not path.is_file():
            return None
        return path

    # -------------------------------------------------------------- internos
    def _load_recent_events(self, camera_id: str) -> None:
        path = self.events_dir / camera_id / "events.jsonl"
        if not path.is_file():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines()[-200:]:
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                label = str(event.get("label", "")).lower()
                try:
                    is_person = int(event.get("class_id", -1)) == PERSON_CLASS_ID
                except (TypeError, ValueError):
                    is_person = label in ("person", "pessoa")
                # Eventos antigos de veículo ficam fora da API; o overlay nunca
                # mais mostra carro/moto/percentual.
                if is_person or label in ("person", "pessoa"):
                    event.pop("confidence", None)
                    event["label"] = "pessoa"
                    self._memory_events[camera_id].append(event)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    def _load_recent_crops(self, camera_id: str) -> None:
        """Restaura as filas de rostos/placas depois de um restart."""
        event_dir = self.events_dir / camera_id
        if not event_dir.is_dir():
            return
        size = max(1, int(self.config.crop_queue_size))
        try:
            for key, prefix in (("faces", "face_"), ("plates", "plate_")):
                queue = self._faces[camera_id] if key == "faces" else self._plates[camera_id]
                files = sorted(
                    event_dir.glob(prefix + "*.jpg"),
                    key=lambda path: path.stat().st_mtime,
                )[-size:]
                for path in files:
                    queue.append({
                        "id": path.stem,
                        "file": path.name,
                        "track_id": _track_id_from_name(path.name),
                        "timestamp": _iso(path.stat().st_mtime),
                    })
        except OSError:
            pass

    def _push_crop(self, cam_id: str, kind: str, prefix: str, image,
                   track_id: int, now: float) -> bool:
        """Salva o recorte e mantém a fila com tamanho máximo configurado."""
        import cv2

        event_dir = self.events_dir / cam_id
        try:
            event_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{prefix}_{stamp}_{int(track_id)}.jpg"
            saved = cv2.imwrite(
                str(event_dir / filename), image, [cv2.IMWRITE_JPEG_QUALITY, 88]
            )
            if not saved:
                raise RuntimeError("cv2.imwrite retornou falso")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[%s][visão] falha ao salvar recorte: %s", cam_id, exc)
            return False

        entry = {
            "id": Path(filename).stem,
            "file": filename,
            "track_id": int(track_id),
            "timestamp": _iso(now),
        }
        with self._events_lock:
            queue = self._faces[cam_id] if kind == "faces" else self._plates[cam_id]
            while len(queue) >= queue.maxlen:
                old = queue.popleft()
                try:
                    (event_dir / str(old.get("file", ""))).unlink()
                except OSError:
                    pass
            queue.append(entry)
            with self._state_lock:
                state = self._states.get(cam_id)
                if state is not None:
                    if kind == "faces":
                        state.faces = list(queue)
                    else:
                        state.plates = list(queue)
        return True

    def _set_state(self, cam_id: str, **changes) -> None:
        with self._state_lock:
            state = self._states.get(cam_id)
            if state is None:
                return
            for key, value in changes.items():
                setattr(state, key, value)
            state.updated_at = time.time()

    def _wait_playlist(self, playlist: Path, stop: threading.Event) -> bool:
        while not self._stopped(stop):
            try:
                if playlist.is_file() and time.time() - playlist.stat().st_mtime < 10:
                    return True
            except OSError:
                pass
            if self._pause(stop, 1.0):
                break
        return False

    def _worker(self, spec: CameraSpec, stop: threading.Event) -> None:
        cam_id = spec.id
        playlist = self.base_dir / "streams" / cam_id / "stream.m3u8"
        from vision.tracker.ocsort import OCSort

        while not self._stopped(stop):
            if not self._wait_playlist(playlist, stop):
                break
            source_w, source_h = _probe_resolution(playlist, spec)
            max_width = max(320, int(self.config.max_width))
            scale = min(1.0, max_width / source_w)
            width = max(160, int(source_w * scale) // 2 * 2)
            height = max(90, int(source_h * scale) // 2 * 2)
            reader = _FrameReader(playlist, self.config.fps, width, height,
                                  cam_id, self.logger)
            with self._readers_lock:
                self._readers[cam_id] = reader
            tracker = OCSort(
                det_thresh=self.config.confidence,
                max_age=max(1, int(self.config.max_age)),
                min_hits=1,
                iou_threshold=0.3,
            )
            self._set_state(
                cam_id, status="running", message="Analisando frames",
                frame_width=width, frame_height=height,
            )
            try:
                reader.start()
                while not self._stopped(stop):
                    frame = reader.read()
                    if frame is None:
                        if reader.poll() is not None or self._stopped(stop):
                            break
                        continue
                    self._process_frame(spec, frame, tracker, width, height, stop)
            except Exception as exc:  # noqa: BLE001 - não derruba o viewer
                self.logger.exception("[%s][visão] falha no worker: %s", cam_id, exc)
                self._set_state(cam_id, status="error", message=str(exc)[:300])
            finally:
                reader.stop()
                with self._readers_lock:
                    if self._readers.get(cam_id) is reader:
                        self._readers.pop(cam_id, None)
            if not self._stopped(stop):
                self._set_state(cam_id, status="reconnecting",
                                message="Aguardando próximo segmento HLS")
                self._pause(stop, 2.0)

        self._set_state(cam_id, status="stopped", message="Detecção encerrada")

    def _track_items(self, tracks, width: int, height: int) -> list[dict]:
        """Converte tracks em caixas normals; `confidence` fica só internamente."""
        items: list[dict] = []
        for track in tracks:
            try:
                class_id = int(track.class_id)
                confidence = float(track.score)
                track_id = int(track.track_id)
                age = int(getattr(track, "tracklet_len", 0) or 0)
            except (TypeError, ValueError):
                continue
            if age < self.config.min_track_len or confidence < self.config.confidence:
                continue
            x, y, w, h = (float(value) for value in track.tlwh)
            if w <= 1 or h <= 1:
                continue
            x1, y1 = max(0.0, x), max(0.0, y)
            x2, y2 = min(float(width), x + w), min(float(height), y + h)
            items.append({
                "x": round(x1, 1), "y": round(y1, 1),
                "w": round(x2 - x1, 1), "h": round(y2 - y1, 1),
                "nx": round(x1 / width, 5), "ny": round(y1 / height, 5),
                "nw": round((x2 - x1) / width, 5),
                "nh": round((y2 - y1) / height, 5),
                "label": class_label(class_id),
                "class_id": class_id,
                "confidence": round(confidence, 3),
                "track_id": track_id,
            })
        items.sort(key=lambda item: item["confidence"], reverse=True)
        return items

    @staticmethod
    def _face_in_box(faces, box):
        """Escolhe o rosto com maior score cujo centro caia na caixa da pessoa."""
        x, y, w, h = (float(value) for value in box[:4])
        best = None
        for face in faces:
            fx, fy, fw, fh, score = face[:5]
            cx, cy = fx + fw / 2.0, fy + fh / 2.0
            if x <= cx <= x + w and y <= cy <= y + h:
                if best is None or score > best[4]:
                    best = face
        return best

    def _capture_crops(self, spec: CameraSpec, frame, people: list[dict],
                       vehicles: list[dict], now: float) -> None:
        """Alimenta as filas laterais sem mexer no overlay do player."""
        cam_id = spec.id
        max_attempts = max(1, int(self.config.crop_attempts))

        if people:
            with self._crop_lock:
                cropper = self._face_cropper
            if cropper is not None:
                faces = cropper.detect(frame)
                attempts = self._face_attempts[cam_id]
                for item in people:
                    track_id = int(item["track_id"])
                    if track_id in self._seen_faces[cam_id]:
                        continue
                    if attempts.get(track_id, 0) >= max_attempts:
                        continue
                    attempts[track_id] = attempts.get(track_id, 0) + 1
                    box = (item["x"], item["y"], item["w"], item["h"])
                    face = self._face_in_box(faces, box)
                    if face is None:
                        continue
                    crop = crop_with_padding(frame, face, 0.35, 0.45)
                    crop = resize_keep_aspect(crop, (160, 200))
                    if crop is not None and self._push_crop(
                            cam_id, "faces", "face", crop, track_id, now):
                        self._seen_faces[cam_id].add(track_id)
                if len(attempts) > 1000:
                    attempts.clear()
                if len(self._seen_faces[cam_id]) > 1000:
                    self._seen_faces[cam_id].clear()

        if vehicles:
            attempts = self._plate_attempts[cam_id]
            for item in vehicles:
                track_id = int(item["track_id"])
                if track_id in self._seen_plates[cam_id]:
                    continue
                if attempts.get(track_id, 0) >= max_attempts:
                    continue
                attempts[track_id] = attempts.get(track_id, 0) + 1
                box = (item["x"], item["y"], item["w"], item["h"])
                plate = find_plate(frame, box)
                if plate is not None and self._push_crop(
                        cam_id, "plates", "plate", plate, track_id, now):
                    self._seen_plates[cam_id].add(track_id)
            if len(attempts) > 1000:
                attempts.clear()
            if len(self._seen_plates[cam_id]) > 1000:
                self._seen_plates[cam_id].clear()

    def _process_frame(self, spec: CameraSpec, frame, tracker, width: int,
                       height: int, stop: threading.Event) -> None:
        cam_id = spec.id
        if self._detector is None or self._stopped(stop):
            return
        with self._detector_lock:
            preds = self._detector.detect(frame)
        tracks = tracker.update(preds, self.config.confidence)
        items = self._track_items(tracks, width, height)

        # O player mostra só pessoas. Veículos continuam sendo rastreados
        # internamente apenas para a tentativa de captura da placa.
        people = [item for item in items if item["class_id"] == PERSON_CLASS_ID]
        vehicles = [item for item in items if item["class_id"] in VEHICLE_CLASS_IDS]
        public = [
            {key: value for key, value in item.items() if key != "confidence"}
            for item in people[:50]
        ]
        now = time.time()
        self._set_state(
            cam_id, detections=public, updated_at=now,
            status="running", message="Analisando frames",
        )
        self._capture_crops(spec, frame, people, vehicles, now)

        if not self.config.snapshot:
            return
        if now - self._last_event[cam_id] < self.config.event_cooldown_seconds:
            return
        seen = self._seen_tracks[cam_id]
        fresh = [item for item in public if item["track_id"] not in seen]
        if not fresh:
            return
        # Dedup por track_id, como no ClearCam: a mesma pessoa não gera
        # centenas de eventos enquanto permanece na imagem.
        for item in fresh:
            seen.add(item["track_id"])
        if len(seen) > 1000:
            seen.clear()
        event = fresh[0]
        self._last_event[cam_id] = now
        self._save_event(spec, frame, event, public, now)

    def _save_event(self, spec: CameraSpec, frame, event: dict,
                    detections: list[dict], now: float) -> None:
        import cv2

        cam_id = spec.id
        event_dir = self.events_dir / cam_id
        event_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{stamp}_{event['track_id']}_{event['class_id']}.jpg"
        annotated = frame.copy()
        for item in detections:
            x1 = int(item["x"])
            y1 = int(item["y"])
            x2 = int(x1 + item["w"])
            y2 = int(y1 + item["h"])
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (57, 208, 163), 2)
        try:
            cv2.imwrite(str(event_dir / filename), annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[%s][visão] falha ao salvar snapshot: %s", cam_id, exc)
            return

        record = {
            "id": f"{stamp}-{cam_id}-{event['track_id']}",
            "camera_id": cam_id,
            "camera_name": spec.name,
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "label": "pessoa",
            "class_id": event["class_id"],
            "track_id": event["track_id"],
            "bbox": {
                "x": event["x"], "y": event["y"],
                "w": event["w"], "h": event["h"],
                "nx": event["nx"], "ny": event["ny"],
                "nw": event["nw"], "nh": event["nh"],
            },
            "snapshot": filename,
            "people": len(detections),
        }
        with self._events_lock:
            self._memory_events[cam_id].append(record)
            try:
                with (event_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                self.logger.warning("[%s][visão] falha ao registrar evento: %s", cam_id, exc)
            with self._state_lock:
                state = self._states.get(cam_id)
                if state is not None:
                    state.events += 1
        self.logger.info("[%s][visão] evento: pessoa #%s", cam_id, record["track_id"])
