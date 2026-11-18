"""Validação e persistência de câmeras adicionadas pela interface web.

A senha nunca vai para config.json: ela fica apenas em secrets.json (modo 600)
e o config guarda somente o nome da chave do segredo.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

from camera_manager import CameraSpec, ConfigError

_WRITE_LOCK = threading.RLock()

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~%?=&+\-/]*$")
_BITRATE_RE = re.compile(r"^[1-9][0-9]{0,4}[kKmM]$")
_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _text(payload: dict, key: str, label: str, maximum: int,
          default: str = "", required: bool = False) -> str:
    value = payload.get(key, default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ConfigError(f"{label} deve ser um texto.")
    if not required:
        value = value.strip()
    if required and not value.strip():
        raise ConfigError(f"Informe {label}.")
    if len(value) > maximum:
        raise ConfigError(f"{label} é muito longo (máximo de {maximum} caracteres).")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError(f"{label} contém caracteres inválidos.")
    return value


def _integer(payload: dict, key: str, label: str, default: int,
             minimum: int, maximum: int) -> int:
    value = payload.get(key, default)
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise ConfigError(f"{label} deve ser um número inteiro.")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{label} deve ser um número inteiro.")
    if not minimum <= number <= maximum:
        raise ConfigError(f"{label} deve estar entre {minimum} e {maximum}.")
    return number


def _secret_key(camera_id: str) -> str:
    base = "camera_" + re.sub(r"[^a-z0-9]+", "_", camera_id.lower()).strip("_") + "_password"
    key = base
    suffix = 2
    # Um segredo exportado por variável de ambiente tem prioridade sobre o
    # arquivo; evitar esse nome impede que a senha digitada seja ignorada.
    while f"YV_SECRET_{key.upper()}" in os.environ:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _new_camera_id(host: str, existing: set[str]) -> str:
    slug = _ID_SAFE_RE.sub("-", host).strip("-")[:40] or "camera"
    base = f"cam-{slug}"
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _read_json(path: Path, label: str, optional: bool = False) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if optional:
            return {}
        raise ConfigError(f"{label} não encontrado.")
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Não foi possível ler {label}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} está em um formato inválido.")
    return raw


def _atomic_write_json(path: Path, data: dict, mode: int) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def remove_camera(config_path: Path, secrets_path: Path,
                  camera_id: str, secret: str) -> None:
    """Desfaz um cadastro que não chegou a iniciar o supervisor da câmera."""
    with _WRITE_LOCK:
        config_raw = _read_json(config_path, "config.json")
        secrets_raw = _read_json(secrets_path, "secrets.json", optional=True)
        cameras = config_raw.get("cameras")
        if isinstance(cameras, list):
            config_raw["cameras"] = [
                item for item in cameras
                if not (isinstance(item, dict) and item.get("id") == camera_id)
            ]
        secrets_raw.pop(secret, None)

        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass
        _atomic_write_json(secrets_path, secrets_raw, 0o600)
        _atomic_write_json(config_path, config_raw, config_mode)


def rename_camera(config_path: Path, camera_id: str, new_name: str) -> None:
    """Altera apenas o nome amigável de uma câmera já cadastrada."""
    value = str(new_name).strip() if isinstance(new_name, str) else new_name
    name = _text({"name": value}, "name", "Nome da câmera", 80, required=True)
    with _WRITE_LOCK:
        config_raw = _read_json(config_path, "config.json")
        cameras = config_raw.get("cameras")
        if not isinstance(cameras, list):
            raise ConfigError("config.json não contém uma lista de câmeras.")
        found = False
        for item in cameras:
            if isinstance(item, dict) and item.get("id") == camera_id:
                item["name"] = name
                found = True
                break
        if not found:
            raise ConfigError("Câmera não encontrada.")
        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass
        _atomic_write_json(config_path, config_raw, config_mode)


def add_camera(config_path: Path, secrets_path: Path, payload: dict,
               existing_ids: set[str]) -> tuple[CameraSpec, str]:
    """Grava uma nova câmera e devolve (spec, senha) para iniciá-la já."""
    if not isinstance(payload, dict):
        raise ConfigError("Dados da câmera inválidos.")

    name = _text(payload, "name", "Nome da câmera", 80, required=True)
    host = _text(payload, "host", "IP ou endereço da câmera", 253, required=True)
    if not _HOST_RE.fullmatch(host):
        raise ConfigError(
            "IP ou endereço inválido. Use um IPv4 ou um nome como 'camera.local'."
        )
    port = _integer(payload, "port", "Porta RTSP", 554, 1, 65535)
    username = _text(payload, "username", "Usuário", 64, default="admin", required=True)
    if not _USER_RE.fullmatch(username):
        raise ConfigError("Usuário inválido (use letras, números, ponto, _ ou -).")
    password = _text(payload, "password", "Senha", 200, required=True)

    path = _text(payload, "path", "Caminho RTSP", 200, default="/onvif1", required=True)
    if not _PATH_RE.fullmatch(path):
        raise ConfigError("Caminho RTSP inválido (deve começar com '/' e sem espaços).")

    transport = str(payload.get("transport", "udp") or "udp").strip().lower()
    if transport not in {"udp", "tcp"}:
        raise ConfigError("Transporte deve ser UDP ou TCP.")

    output_width = _integer(payload, "output_width", "Largura", 1280, 320, 4096)
    output_height = _integer(payload, "output_height", "Altura", 0, 0, 4096)
    input_fps = _integer(payload, "input_fps", "FPS", 15, 1, 60)
    video_bitrate = _text(payload, "video_bitrate", "Bitrate de vídeo", 16, default="2500k")
    audio_bitrate = _text(payload, "audio_bitrate", "Bitrate de áudio", 16, default="64k")
    for label, value in (("Bitrate de vídeo", video_bitrate), ("Bitrate de áudio", audio_bitrate)):
        if not value or not _BITRATE_RE.fullmatch(value):
            raise ConfigError(f"{label} inválido (exemplo: 2500k).")

    with _WRITE_LOCK:
        config_raw = _read_json(config_path, "config.json")
        secrets_raw = _read_json(secrets_path, "secrets.json", optional=True)

        cameras = config_raw.get("cameras")
        if not isinstance(cameras, list):
            raise ConfigError("config.json não contém uma lista de câmeras.")

        ids = set(existing_ids)
        ids.update(
            str(item.get("id", ""))
            for item in cameras
            if isinstance(item, dict)
        )
        camera_id = _new_camera_id(host, ids)
        secret = _secret_key(camera_id)

        cameras.append({
            "id": camera_id,
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "secret": secret,
            "path": path,
            "transport": transport,
            "enabled": True,
            "output_width": output_width,
            "output_height": output_height,
            "video_bitrate": video_bitrate,
            "audio_bitrate": audio_bitrate,
            "input_fps": input_fps,
        })
        config_raw["cameras"] = cameras
        secrets_raw[secret] = password

        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass

        # Grava primeiro o segredo: se falhar o config, não ficamos com uma
        # câmera apontando para uma senha inexistente.
        _atomic_write_json(secrets_path, secrets_raw, 0o600)
        _atomic_write_json(config_path, config_raw, config_mode)

    spec = CameraSpec(
        id=camera_id,
        name=name,
        host=host,
        port=port,
        username=username,
        secret=secret,
        path=path,
        transport=transport,
        enabled=True,
        output_width=output_width,
        output_height=output_height,
        video_bitrate=video_bitrate,
        audio_bitrate=audio_bitrate,
        input_fps=input_fps,
    )
    return spec, password
