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
from urllib.parse import urlsplit

from camera_manager import CameraSpec, ConfigError

_WRITE_LOCK = threading.RLock()

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~%?=&+\-/]*$")
_BITRATE_RE = re.compile(r"^[1-9][0-9]{0,4}[kKmM]$")
_ID_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")
_ID_VALID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


def remove_camera(config_path: Path, secrets_path: Path, camera_id: str) -> str:
    """Remove uma câmera do config e apaga a senha associada.

    Devolve o nome da chave de segredo removida (vazio se a câmera não tinha
    segredo próprio). A chave só é apagada quando nenhuma outra câmera ainda a
    referencia.
    """
    if not _ID_VALID_RE.fullmatch(camera_id):
        raise ConfigError("Identificador da câmera inválido.")
    with _WRITE_LOCK:
        config_raw = _read_json(config_path, "config.json")
        cameras = config_raw.get("cameras")
        if not isinstance(cameras, list):
            raise ConfigError("config.json não contém uma lista de câmeras.")
        removed = next(
            (item for item in cameras
             if isinstance(item, dict) and item.get("id") == camera_id),
            None,
        )
        if removed is None:
            raise ConfigError("Câmera não encontrada.")

        secret = str(removed.get("secret") or "").strip()
        remaining = [
            item for item in cameras
            if not (isinstance(item, dict) and item.get("id") == camera_id)
        ]
        config_raw["cameras"] = remaining

        secrets_raw = _read_json(secrets_path, "secrets.json", optional=True)
        secret_in_use = any(
            isinstance(item, dict) and str(item.get("secret") or "").strip() == secret
            for item in remaining
        )
        if secret and not secret_in_use:
            secrets_raw.pop(secret, None)

        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass
        # Config primeiro: se falhar a gravação dos segredos, sobra no máximo
        # uma chave órfã (inofensiva), nunca uma câmera sem senha.
        _atomic_write_json(config_path, config_raw, config_mode)
        _atomic_write_json(secrets_path, secrets_raw, 0o600)
        return secret


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


def _bool_setting(payload: dict, key: str, label: str, default):
    value = payload.get(key, default)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{label} deve ser verdadeiro ou falso.")


def _validate_camera_fields(payload: dict, password_required: bool) -> dict:
    """Valida os campos editáveis; senha em branco = manter a atual."""
    if not isinstance(payload, dict):
        raise ConfigError("Dados da câmera inválidos.")

    source_type = str(payload.get("source_type") or "").strip().lower()
    if source_type not in {"", "rtsp", "public"}:
        raise ConfigError("Tipo de câmera inválido.")
    source_url = _text(payload, "source_url", "URL da fonte", 500)
    if source_type == "public" and not source_url:
        raise ConfigError("Informe o link da transmissão pública.")
    if source_url:
        parsed = urlsplit(source_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or any(char.isspace() for char in source_url)):
            raise ConfigError(
                "URL da fonte inválida (use http:// ou https://, sem espaços)."
            )
    # Câmera pública não usa RTSP: os campos abaixo viram apenas cadastro.
    is_public = source_type == "public" or bool(source_url)

    name = _text(payload, "name", "Nome da câmera", 80, required=True)
    host = _text(
        payload, "host", "IP ou endereço da câmera", 253,
        default="youtube.com" if is_public else "", required=not is_public,
    )
    if not _HOST_RE.fullmatch(host):
        raise ConfigError(
            "IP ou endereço inválido. Use um IPv4 ou um nome como 'camera.local'."
        )
    port = _integer(payload, "port", "Porta RTSP", 443 if is_public else 554, 1, 65535)
    username = _text(
        payload, "username", "Usuário", 64,
        default="publico" if is_public else "admin", required=True,
    )
    if not _USER_RE.fullmatch(username):
        raise ConfigError("Usuário inválido (use letras, números, ponto, _ ou -).")

    raw_password = payload.get("password", "")
    if raw_password is None:
        raw_password = ""
    if not isinstance(raw_password, str):
        raise ConfigError("Senha deve ser um texto.")
    if len(raw_password) > 200:
        raise ConfigError("Senha é muito longa (máximo de 200 caracteres).")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_password):
        raise ConfigError("Senha contém caracteres inválidos.")
    # Não se faz strip na senha: espaços podem fazer parte dela.
    if not password_required:
        password = None if not raw_password.strip() else raw_password
    elif not raw_password.strip():
        if is_public:
            password = "publico"
        else:
            raise ConfigError("Informe a senha.")
    else:
        password = raw_password

    path = _text(
        payload, "path", "Caminho RTSP", 200,
        default="/" if is_public else "/onvif1", required=True,
    )
    if not _PATH_RE.fullmatch(path):
        raise ConfigError("Caminho RTSP inválido (deve começar com '/' e sem espaços).")

    transport = str(
        payload.get("transport", "tcp" if is_public else "udp")
        or ("tcp" if is_public else "udp")
    ).strip().lower()
    if transport not in {"udp", "tcp"}:
        raise ConfigError("Transporte deve ser UDP ou TCP.")

    enabled = _bool_setting(payload, "enabled", "Câmera ativada", True)
    if enabled is None:
        enabled = True
    detection_enabled = _bool_setting(
        payload, "detection_enabled", "Detecção por IA", None
    )

    output_width = _integer(payload, "output_width", "Largura", 1280, 320, 4096)
    output_height = _integer(payload, "output_height", "Altura", 0, 0, 4096)
    input_fps = _integer(payload, "input_fps", "FPS", 15, 1, 60)
    video_bitrate = _text(payload, "video_bitrate", "Bitrate de vídeo", 16, default="2500k")
    audio_bitrate = _text(payload, "audio_bitrate", "Bitrate de áudio", 16, default="64k")
    for label, value in (("Bitrate de vídeo", video_bitrate), ("Bitrate de áudio", audio_bitrate)):
        if not value or not _BITRATE_RE.fullmatch(value):
            raise ConfigError(f"{label} inválido (exemplo: 2500k).")

    return {
        "name": name,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "path": path,
        "transport": transport,
        "enabled": enabled,
        "output_width": output_width,
        "output_height": output_height,
        "video_bitrate": video_bitrate,
        "audio_bitrate": audio_bitrate,
        "input_fps": input_fps,
        "detection_enabled": detection_enabled,
        "source_url": source_url,
    }


def _camera_spec(camera_id: str, secret: str, fields: dict) -> CameraSpec:
    return CameraSpec(
        id=camera_id,
        secret=secret,
        name=fields["name"],
        host=fields["host"],
        port=fields["port"],
        username=fields["username"],
        path=fields["path"],
        transport=fields["transport"],
        enabled=fields["enabled"],
        output_width=fields["output_width"],
        output_height=fields["output_height"],
        video_bitrate=fields["video_bitrate"],
        audio_bitrate=fields["audio_bitrate"],
        input_fps=fields["input_fps"],
        detection_enabled=fields["detection_enabled"],
        source_url=fields["source_url"],
    )


def add_camera(config_path: Path, secrets_path: Path, payload: dict,
               existing_ids: set[str]) -> tuple[CameraSpec, str]:
    """Grava uma nova câmera e devolve (spec, senha) para iniciá-la já."""
    fields = _validate_camera_fields(payload, password_required=True)

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
        camera_id = _new_camera_id(fields["host"], ids)
        secret = _secret_key(camera_id)

        entry = {
            "id": camera_id,
            "name": fields["name"],
            "host": fields["host"],
            "port": fields["port"],
            "username": fields["username"],
            "secret": secret,
            "path": fields["path"],
            "transport": fields["transport"],
            "enabled": fields["enabled"],
            "output_width": fields["output_width"],
            "output_height": fields["output_height"],
            "video_bitrate": fields["video_bitrate"],
            "audio_bitrate": fields["audio_bitrate"],
            "input_fps": fields["input_fps"],
        }
        if fields["detection_enabled"] is not None:
            entry["detection_enabled"] = fields["detection_enabled"]
        if fields["source_url"]:
            entry["source_url"] = fields["source_url"]
        cameras.append(entry)
        config_raw["cameras"] = cameras
        secrets_raw[secret] = fields["password"]

        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass

        # Grava primeiro o segredo: se falhar o config, não ficamos com uma
        # câmera apontando para uma senha inexistente.
        _atomic_write_json(secrets_path, secrets_raw, 0o600)
        _atomic_write_json(config_path, config_raw, config_mode)

    return _camera_spec(camera_id, secret, fields), fields["password"]


def update_camera(config_path: Path, secrets_path: Path, camera_id: str,
                  payload: dict) -> tuple[CameraSpec, str]:
    """Atualiza uma câmera existente e devolve (spec, senha) para o runtime.

    O `id` e a chave `secret` são preservados — trocar o id exigiria recriar
    streams, eventos e senhas. A senha fica em branco no payload quando o
    usuário não quer alterá-la.
    """
    if not _ID_VALID_RE.fullmatch(camera_id):
        raise ConfigError("Identificador da câmera inválido.")
    fields = _validate_camera_fields(payload, password_required=False)

    with _WRITE_LOCK:
        config_raw = _read_json(config_path, "config.json")
        cameras = config_raw.get("cameras")
        if not isinstance(cameras, list):
            raise ConfigError("config.json não contém uma lista de câmeras.")
        item = next(
            (entry for entry in cameras
             if isinstance(entry, dict) and entry.get("id") == camera_id),
            None,
        )
        if item is None:
            raise ConfigError("Câmera não encontrada.")

        secret = str(item.get("secret") or "").strip()
        if not secret:
            raise ConfigError("Câmera sem chave de segredo válida.")
        secrets_raw = _read_json(secrets_path, "secrets.json", optional=True)
        password = fields["password"]
        if password is None:
            password = str(secrets_raw.get(secret, "") or "")
        if not password:
            raise ConfigError("Informe a senha da câmera.")

        item.update({
            "name": fields["name"],
            "host": fields["host"],
            "port": fields["port"],
            "username": fields["username"],
            "path": fields["path"],
            "transport": fields["transport"],
            "enabled": fields["enabled"],
            "output_width": fields["output_width"],
            "output_height": fields["output_height"],
            "video_bitrate": fields["video_bitrate"],
            "audio_bitrate": fields["audio_bitrate"],
            "input_fps": fields["input_fps"],
        })
        if fields["detection_enabled"] is None:
            item.pop("detection_enabled", None)
        else:
            item["detection_enabled"] = fields["detection_enabled"]
        if fields["source_url"]:
            item["source_url"] = fields["source_url"]
        else:
            item.pop("source_url", None)
        secrets_raw[secret] = password

        config_mode = 0o644
        try:
            config_mode = stat.S_IMODE(config_path.stat().st_mode)
        except OSError:
            pass
        _atomic_write_json(secrets_path, secrets_raw, 0o600)
        _atomic_write_json(config_path, config_raw, config_mode)

    return _camera_spec(camera_id, secret, fields), password
