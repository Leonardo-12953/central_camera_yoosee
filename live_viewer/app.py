"""LYSec Play | Live Viewer - standalone RTSP -> HLS viewer (Python standard library only)."""
from __future__ import annotations

import argparse
import fcntl
import html
import json
import logging
import mimetypes
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from camera_manager import (
    CameraManager,
    CameraSpec,
    ConfigError,
    load_config,
    load_secrets,
)
from camera_store import (
    add_camera as store_add_camera,
    remove_camera as store_remove_camera,
    rename_camera as store_rename_camera,
    update_camera as store_update_camera,
)
from logging_utils import setup_logging
from vision.detection_manager import DetectionManager

BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger("viewer")


_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~\-/]+$")


def _sanitize_prefix(value: str | None) -> str:
    """Aceita apenas um prefixo de caminho simples (ex.: /ao-vivo)."""
    if not value:
        return ""
    value = value.split(",", 1)[0].strip()
    if not value:
        return ""
    if not value.startswith("/"):
        value = "/" + value
    value = value.rstrip("/")
    if value in {"", "/"}:
        return ""
    if ".." in value or "//" in value or not _PREFIX_RE.match(value):
        return ""
    return value


def _back_link(prefix: str) -> str:
    """Link de volta ao painel do Central quando servido pelo proxy /ao-vivo."""
    prefix = (prefix or "").rstrip("/")
    if not prefix:
        return ""
    parent = prefix.rsplit("/", 1)[0].rstrip("/") + "/"
    if not parent.startswith("/"):
        parent = "/" + parent
    href = html.escape(parent, quote=True)
    return (
        f'<a class="back-link" href="{href}" '
        'title="Voltar ao painel da Central de Câmeras">'
        '<span aria-hidden="true">&larr;</span> Painel central</a>'
    )


_CARD_TEMPLATE_CACHE: str | None = None


def _camera_card_template() -> str:
    """Markup do cartão, editável em templates/camera_card.html."""
    global _CARD_TEMPLATE_CACHE
    if _CARD_TEMPLATE_CACHE is None:
        path = BASE_DIR / "templates" / "camera_card.html"
        _CARD_TEMPLATE_CACHE = path.read_text(encoding="utf-8")
    return _CARD_TEMPLATE_CACHE


def _camera_card(spec: CameraSpec, base_path: str = "") -> str:
    base_path = (base_path or "").rstrip("/")
    hls = f"{base_path}/streams/{quote(spec.id)}/stream.m3u8"
    values = {
        "{{CAMERA_ID}}": html.escape(spec.id, quote=True),
        "{{CAMERA_NAME}}": html.escape(spec.name),
        "{{HLS_URL}}": html.escape(hls, quote=True),
        "{{RTSP}}": html.escape(spec.safe_url()),
        "{{TRANSPORT}}": html.escape(spec.transport.upper()),
        "{{BASE_PATH}}": html.escape(base_path, quote=True),
    }
    body = _camera_card_template()
    for placeholder, value in values.items():
        body = body.replace(placeholder, value)
    return body


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, manager: CameraManager,
                 camera_specs: list[CameraSpec], base_path: str = "",
                 config_path: Path | None = None, secrets_path: Path | None = None,
                 detection: DetectionManager | None = None,
                 secrets: dict[str, str] | None = None):
        super().__init__(address, handler)
        self.manager = manager
        self.detection = detection
        self.camera_specs = camera_specs
        self.camera_ids = {spec.id for spec in camera_specs}
        self.base_path = (base_path or "").rstrip("/")
        self.config_path = config_path
        self.secrets_path = secrets_path
        self.secrets = dict(secrets or {})

    def register_camera(self, spec: CameraSpec, password: str | None = None) -> None:
        self.camera_ids.add(spec.id)
        self.camera_specs.append(spec)
        if password is not None:
            self.secrets[spec.secret] = password
        if self.detection is not None:
            self.detection.add_camera(spec)

    def update_camera_state(self, spec: CameraSpec, password: str) -> None:
        """Atualiza a lista servida pela UI e a senha em memória."""
        for index, item in enumerate(self.camera_specs):
            if item.id == spec.id:
                self.camera_specs[index] = spec
                break
        self.secrets[spec.secret] = password
        if self.detection is not None:
            self.detection.add_camera(spec)

    def rename_camera(self, camera_id: str, spec: CameraSpec) -> None:
        for index, item in enumerate(self.camera_specs):
            if item.id == camera_id:
                self.camera_specs[index] = spec
                break
        if self.detection is not None:
            self.detection.rename_camera(camera_id, spec)

    def unregister_camera(self, camera_id: str) -> None:
        self.camera_ids.discard(camera_id)
        self.camera_specs[:] = [spec for spec in self.camera_specs if spec.id != camera_id]
        if self.detection is not None:
            self.detection.remove_camera(camera_id)


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "LYSecPlay/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- utilities
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        logging.getLogger("http").debug("%s - %s", self.address_string(), fmt % args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; "
            "worker-src 'self' blob:",
        )

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, payload, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Cache-Control": "no-store"}
        if extra:
            headers.update(extra)
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _html(self, body: str) -> None:
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8",
                   {"Cache-Control": "no-store"})

    # ---------------------------------------------------------------- routes
    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def _forwarded_prefix(self) -> str:
        return _sanitize_prefix(self.headers.get("X-Forwarded-Prefix"))

    def _render_prefix(self) -> str:
        return self._forwarded_prefix() or getattr(self.server, "base_path", "")

    def _strip_base_path(self, path: str) -> str | None:
        configured = getattr(self.server, "base_path", "")
        forwarded = self._forwarded_prefix()
        bases: list[str] = []
        for base in (configured, forwarded):
            if base and base not in bases:
                bases.append(base)
        for base in bases:
            if path == base or path == base + "/":
                return "/"
            if path.startswith(base + "/"):
                return path[len(base):] or "/"
        if configured and not forwarded:
            return None
        return path

    def do_GET(self):  # noqa: N802
        parsed = urlsplit(self.path)
        raw_path = unquote(parsed.path)
        path = self._strip_base_path(raw_path)
        if path is None:
            return self._not_found()
        parts = [p for p in path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["api", "cameras"]:
            return self._get_camera(parts[2])
        try:
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path.startswith("/cam/"):
                cam_id = path[len("/cam/"):].strip("/")
                if cam_id in self.server.camera_ids:
                    return self._serve_index()
                return self._not_found()
            if path == "/api/status":
                cameras = self.server.manager.snapshot()
                prefix = self._render_prefix()
                if prefix:
                    for cam in cameras:
                        cam_id = cam.get("id")
                        if isinstance(cam_id, str) and cam_id:
                            cam["hls_url"] = f"{prefix}/streams/{quote(cam_id)}/stream.m3u8"
                return self._json(200, {"cameras": cameras})
            if path == "/healthz":
                return self._json(200, {
                    "ok": True,
                    "cameras": len(self.server.camera_specs),
                    "states": {s["id"]: s["state"] for s in self.server.manager.snapshot()},
                })
            if path == "/api/detections":
                return self._json(200, self.server.detection.status() if self.server.detection else {"enabled": False, "cameras": []})
            if path.startswith("/api/detections/"):
                cam_id = path[len("/api/detections/"):].strip("/")
                state = self.server.detection.camera_state(cam_id) if self.server.detection else None
                if state is None:
                    return self._json(404, {"ok": False, "error": "câmera não encontrada"})
                return self._json(200, state)
            if path == "/api/events":
                query = parse_qs(parsed.query)
                camera_id = (query.get("camera") or [None])[0]
                try:
                    limit = int((query.get("limit") or ["50"])[0])
                except (TypeError, ValueError):
                    limit = 50
                events = self.server.detection.events(camera_id, limit) if self.server.detection else []
                prefix = self._render_prefix()
                for event in events:
                    snapshot = event.get("snapshot")
                    if snapshot:
                        event["snapshot_url"] = (
                            f"{prefix}/events/{quote(str(event['camera_id']))}/{quote(str(snapshot))}"
                        )
                return self._json(200, {"events": events})
            if path.startswith("/events/"):
                return self._serve_event_snapshot(path)
            if path.startswith("/streams/"):
                return self._serve_stream(path)
            if path.startswith("/static/"):
                return self._serve_static(path)
            return self._not_found()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):  # noqa: N802
        parsed = urlsplit(self.path)
        raw_path = unquote(parsed.path)
        path = self._strip_base_path(raw_path)
        if path is None:
            return self._json(404, {"ok": False, "error": "rota não encontrada"})
        if path == "/api/cameras":
            return self._add_camera()
        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "restart":
            cam_id = parts[2]
            ok = self.server.manager.request_restart(cam_id)
            if not ok:
                return self._json(404, {"ok": False, "error": "câmera não encontrada"})
            return self._json(202, {"ok": True, "camera": cam_id})
        if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "rename":
            return self._rename_camera(parts[2])
        if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "update":
            return self._update_camera(parts[2])
        if len(parts) == 4 and parts[:2] == ["api", "cameras"] and parts[3] == "remove":
            return self._remove_camera(parts[2])
        return self._json(404, {"ok": False, "error": "rota não encontrada"})

    def _request_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for key in ("X-Forwarded-Host", "Host"):
            value = (self.headers.get(key) or "").split(",", 1)[0].strip().lower()
            if value:
                hosts.add(value)
        return hosts

    def _origin_allowed(self) -> bool:
        """Bloqueia POSTs vindos de outro site (quando o navegador envia Origin)."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return parsed.netloc.lower() in self._request_hosts()

    def _reject_json(self, status: int, message: str) -> None:
        # O corpo pode não ter sido lido; fechar a conexão evita que o restante
        # seja interpretado como uma nova requisição HTTP.
        self.close_connection = True
        self._json(status, {"ok": False, "error": message}, {"Connection": "close"})

    def _read_json_body(self) -> dict | None:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._reject_json(415, "Formato de dados não suportado.")
            return None
        if not self._origin_allowed():
            self._reject_json(403, "Requisição de outro site não permitida.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 8192:
            self._reject_json(400, "Dados da câmera inválidos ou muito grandes.")
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "Dados da câmera inválidos."})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "Dados da câmera inválidos."})
            return None
        return payload

    def _get_camera(self, camera_id: str) -> None:
        """Devolve a configuração completa da câmera para o dialog de edição."""
        spec = next(
            (item for item in self.server.camera_specs if item.id == camera_id), None
        )
        if spec is None:
            return self._json(404, {"ok": False, "error": "Câmera não encontrada."})
        data = {
            "id": spec.id,
            "name": spec.name,
            "host": spec.host,
            "port": spec.port,
            "username": spec.username,
            "secret": spec.secret,
            "password": self.server.secrets.get(spec.secret, ""),
            "path": spec.path,
            "transport": spec.transport,
            "enabled": bool(spec.enabled),
            "output_width": spec.output_width,
            "output_height": spec.output_height,
            "video_bitrate": spec.video_bitrate,
            "audio_bitrate": spec.audio_bitrate,
            "input_fps": spec.input_fps,
            "detection_enabled": spec.detection_enabled,
            "source_url": spec.source_url,
        }
        self._json(200, {"ok": True, "camera": data}, {"Cache-Control": "no-store"})

    def _add_camera(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        config_path = getattr(self.server, "config_path", None)
        secrets_path = getattr(self.server, "secrets_path", None)
        if config_path is None or secrets_path is None:
            self._json(500, {"ok": False, "error": "Configuração do servidor indisponível."})
            return

        spec = None
        try:
            spec, password = store_add_camera(
                config_path, secrets_path, payload, set(self.server.camera_ids)
            )
            self.server.manager.add_camera(spec, password)
        except ConfigError as exc:
            if spec is not None:
                try:
                    store_remove_camera(config_path, secrets_path, spec.id)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer o cadastro da câmera")
            self._json(400, {"ok": False, "error": str(exc)})
            return
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha inesperada ao adicionar câmera")
            if spec is not None:
                try:
                    store_remove_camera(config_path, secrets_path, spec.id)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer o cadastro da câmera")
            self._json(500, {"ok": False, "error": "Não foi possível adicionar a câmera. Veja o log."})
            return

        self.server.register_camera(spec, password)
        LOGGER.info("câmera %s (%s) cadastrada pela interface", spec.id, spec.name)
        self._json(201, {
            "ok": True,
            "camera": {
                "id": spec.id,
                "name": spec.name,
                "host": spec.host,
                "port": spec.port,
                "transport": spec.transport,
            },
        })

    def _rename_camera(self, camera_id: str) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        config_path = getattr(self.server, "config_path", None)
        old_spec = next(
            (spec for spec in self.server.camera_specs if spec.id == camera_id), None
        )
        if config_path is None or old_spec is None:
            self._json(404, {"ok": False, "error": "Câmera não encontrada."})
            return

        written = False
        try:
            store_rename_camera(config_path, camera_id, payload.get("name"))
            written = True
            new_spec = self.server.manager.rename_camera(
                camera_id, str(payload.get("name") or "").strip()
            )
            if new_spec is None:
                raise ConfigError("Câmera não encontrada.")
        except ConfigError as exc:
            if written:
                try:
                    store_rename_camera(config_path, camera_id, old_spec.name)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer a renomeação")
            self._json(400, {"ok": False, "error": str(exc)})
            return
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha inesperada ao renomear câmera")
            if written:
                try:
                    store_rename_camera(config_path, camera_id, old_spec.name)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer a renomeação")
            self._json(500, {"ok": False, "error": "Não foi possível renomear a câmera. Veja o log."})
            return

        self.server.rename_camera(camera_id, new_spec)
        LOGGER.info("câmera %s renomeada para %s", camera_id, new_spec.name)
        self._json(200, {"ok": True, "camera": {"id": camera_id, "name": new_spec.name}})

    def _update_camera(self, camera_id: str) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        config_path = getattr(self.server, "config_path", None)
        secrets_path = getattr(self.server, "secrets_path", None)
        old_spec = next(
            (spec for spec in self.server.camera_specs if spec.id == camera_id), None
        )
        if config_path is None or secrets_path is None or old_spec is None:
            return self._json(404, {"ok": False, "error": "Câmera não encontrada."})

        def payload_from(spec: CameraSpec, password: str) -> dict:
            return {
                "name": spec.name,
                "host": spec.host,
                "port": spec.port,
                "username": spec.username,
                "password": password,
                "path": spec.path,
                "transport": spec.transport,
                "enabled": spec.enabled,
                "output_width": spec.output_width,
                "output_height": spec.output_height,
                "video_bitrate": spec.video_bitrate,
                "audio_bitrate": spec.audio_bitrate,
                "input_fps": spec.input_fps,
                "detection_enabled": spec.detection_enabled,
                "source_url": spec.source_url,
            }

        old_payload = payload_from(old_spec, self.server.secrets.get(old_spec.secret, ""))
        written = False
        spec: CameraSpec | None = None
        try:
            spec, password = store_update_camera(
                config_path, secrets_path, camera_id, payload
            )
            written = True
            if not self.server.manager.update_camera(camera_id, spec, password):
                raise ConfigError("Câmera não encontrada no supervisor.")
        except ConfigError as exc:
            if written:
                try:
                    store_update_camera(config_path, secrets_path, camera_id, old_payload)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer a atualização da câmera")
            return self._json(400, {"ok": False, "error": str(exc)})
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha inesperada ao atualizar câmera")
            if written:
                try:
                    store_update_camera(config_path, secrets_path, camera_id, old_payload)
                except ConfigError:
                    LOGGER.exception("não foi possível desfazer a atualização da câmera")
            return self._json(
                500, {"ok": False, "error": "Não foi possível salvar a câmera. Veja o log."}
            )

        self.server.update_camera_state(spec, password)
        LOGGER.info("câmera %s atualizada pela interface; stream reiniciado", camera_id)
        self._json(200, {
            "ok": True,
            "camera": {"id": camera_id, "name": spec.name},
            "restarted": True,
        })

    def _remove_camera(self, camera_id: str) -> None:
        config_path = getattr(self.server, "config_path", None)
        secrets_path = getattr(self.server, "secrets_path", None)
        if config_path is None or secrets_path is None:
            return self._json(500, {"ok": False, "error": "Configuração do servidor indisponível."})
        if camera_id not in self.server.camera_ids:
            return self._json(404, {"ok": False, "error": "Câmera não encontrada."})

        try:
            store_remove_camera(config_path, secrets_path, camera_id)
        except ConfigError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha inesperada ao remover câmera")
            return self._json(500, {"ok": False, "error": "Não foi possível remover a câmera. Veja o log."})

        # A configuração já é a fonte da verdade. Falhas de limpeza não devem
        # impedir a remoção, mas são registradas para diagnóstico.
        try:
            self.server.manager.remove_camera(camera_id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha ao encerrar o stream da câmera %s", camera_id)
        try:
            self.server.unregister_camera(camera_id)
        except Exception:  # noqa: BLE001
            LOGGER.exception("falha ao remover a câmera %s dos estados em memória", camera_id)
        LOGGER.info("câmera %s removida pela interface", camera_id)
        self._json(200, {"ok": True, "camera": camera_id})

    # -------------------------------------------------------------- handlers
    def _serve_index(self) -> None:
        template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
        base_path = self._render_prefix()
        cards = "\n".join(_camera_card(spec, base_path) for spec in self.server.camera_specs)
        body = template.replace("{{CAMERA_CARDS}}", cards)
        body = body.replace("{{BASE_PATH}}", html.escape(base_path, quote=True))
        body = body.replace("{{BACK_LINK}}", _back_link(self._forwarded_prefix()))
        self._html(body)

    def _serve_static(self, path: str) -> None:
        name = path[len("/static/"):]
        target = (BASE_DIR / "static" / name).resolve()
        static_root = (BASE_DIR / "static").resolve()
        if static_root not in target.parents or not target.is_file():
            return self._not_found()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError:
            return self._not_found()
        self._send(200, data, content_type,
                   {"Cache-Control": "public, max-age=3600"})

    def _serve_event_snapshot(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        if len(parts) != 3 or parts[0] != "events" or self.server.detection is None:
            return self._not_found()
        target = self.server.detection.snapshot_path(parts[1], parts[2])
        if target is None:
            return self._not_found()
        try:
            data = target.read_bytes()
        except OSError:
            return self._not_found()
        self._send(200, data, "image/jpeg", {"Cache-Control": "no-store"})

    def _serve_stream(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        if len(parts) != 3 or parts[0] != "streams":
            return self._not_found()
        cam_id, filename = parts[1], parts[2]
        if cam_id not in self.server.camera_ids or not filename or filename.startswith("."):
            return self._not_found()
        if filename != Path(filename).name:
            return self._not_found()
        stream_root = (BASE_DIR / "streams" / cam_id).resolve()
        target = (stream_root / filename).resolve()
        if stream_root not in target.parents or not target.is_file():
            return self._not_found()

        suffix = target.suffix.lower()
        content_type = {
            ".m3u8": "application/vnd.apple.mpegurl",
            ".ts": "video/mp2t",
            ".m4s": "video/iso.segment",
            ".mp4": "video/mp4",
            ".vtt": "text/vtt",
        }.get(suffix, "application/octet-stream")

        try:
            size = target.stat().st_size
            handle = target.open("rb")
        except OSError:
            return self._not_found()

        with handle:
            range_header = self.headers.get("Range")
            start, end = 0, size - 1
            status = 200
            if range_header and range_header.startswith("bytes="):
                try:
                    spec = range_header[len("bytes="):].split(",")[0]
                    first, _, last = spec.partition("-")
                    if first:
                        start = int(first)
                        end = int(last) if last else size - 1
                    elif last:
                        start = max(0, size - int(last))
                    if 0 <= start <= end < size:
                        status = 206
                    else:
                        start, end, status = 0, size - 1, 200
                except ValueError:
                    start, end, status = 0, size - 1, 200

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self._security_headers()
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            handle.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _not_found(self) -> None:
        body = b"404 - nao encontrado\n"
        self._send(404, body, "text/plain; charset=utf-8")


def acquire_single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" não trunca: uma segunda instância não pode apagar o PID da primeira.
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    os.chmod(path, 0o600)
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LYSec Play | Live Viewer - RTSP -> HLS viewer")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"))
    parser.add_argument("--secrets", default=str(BASE_DIR / "secrets.json"))
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    logs_dir = BASE_DIR / "logs"
    config_path = Path(args.config)
    secrets_path = Path(args.secrets)

    bootstrap = logging.getLogger("bootstrap")
    try:
        secrets = load_secrets(secrets_path, bootstrap)
        server_cfg, hls_cfg, detection_cfg, cameras = load_config(config_path)
    except ConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 2

    redact = setup_logging(logs_dir, secrets, args.log_level)

    host = args.host or server_cfg.host
    port = args.port or server_cfg.port

    # O lock precisa vir ANTES de qualquer limpeza de PID: uma segunda
    # instância nunca deve tocar nos processos da instância que já está no ar.
    lock_handle = acquire_single_instance_lock(BASE_DIR / "streams" / ".viewer.lock")
    if lock_handle is None:
        print("já existe uma instância do LYSec Play | Live Viewer em execução.", file=sys.stderr)
        return 1

    try:
        manager = CameraManager(cameras, secrets, hls_cfg, BASE_DIR, LOGGER, redact, server_cfg.base_path)
        detection = DetectionManager(detection_cfg, cameras, BASE_DIR, LOGGER, server_cfg.base_path)
    except ConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        lock_handle.close()
        return 2

    # Bind do HTTP antes de iniciar o FFmpeg: se a porta estiver ocupada,
    # nenhum processo de câmera chega a subir.
    try:
        httpd = ViewerServer(
            (host, port), ViewerHandler, manager, cameras, server_cfg.base_path,
            config_path=config_path, secrets_path=secrets_path, detection=detection,
            secrets=secrets,
        )
    except OSError as exc:
        print(f"não foi possível escutar em {host}:{port}: {exc}", file=sys.stderr)
        lock_handle.close()
        return 1

    manager.start()
    detection.start()

    def shutdown(signum, _frame):
        LOGGER.info("recebido sinal %s; encerrando", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    parent_pid = os.environ.get("LIVE_VIEWER_PARENT_PID", "").strip()
    if parent_pid.isdigit():
        expected_parent = int(parent_pid)

        def watch_parent() -> None:
            while True:
                time.sleep(2.0)
                if os.getppid() != expected_parent:
                    LOGGER.warning(
                        "processo pai (Central) encerrado; encerrando o Live Viewer"
                    )
                    shutdown(signal.SIGTERM, None)
                    return

        threading.Thread(
            target=watch_parent,
            name="viewer-parent-watch",
            daemon=True,
        ).start()

    LOGGER.info("LYSec Play | Live Viewer em http://%s:%s%s", host, port, server_cfg.base_path or "")
    for spec in cameras:
        LOGGER.info("câmera %s: %s -> %s", spec.id, spec.safe_url(),
                    f"{server_cfg.base_path}/streams/{spec.id}/stream.m3u8")
    LOGGER.info("visão computacional: %s (%s)",
                "ativada" if detection_cfg.enabled else "desativada",
                Path(detection_cfg.model).name)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        LOGGER.warning("servidor escutando em %s; exponha apenas em rede confiável", host)

    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        LOGGER.info("encerrando servidor HTTP")
        httpd.server_close()
        detection.stop()
        manager.stop()
        try:
            lock_handle.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
