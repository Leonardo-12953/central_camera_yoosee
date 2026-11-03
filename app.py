from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None

FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent
)
RESOURCE_DIR = (
    Path(getattr(sys, "_MEIPASS", APP_DIR)) if FROZEN else APP_DIR
)
TEMPLATES_DIR = RESOURCE_DIR / "templates"


def _load_dotenv(path: Path) -> None:
    """Carrega um .env simples sem sobrescrever variáveis já definidas."""

    try:
        if not path.is_file():
            return

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(key, value)
    except OSError:
        pass


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix=".write-test-", dir=str(path))
    except OSError:
        return False

    os.close(descriptor)
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def _select_data_dir() -> Path:
    """Usa a pasta do app quando possível; senão, uma pasta de dados do usuário."""

    candidates = [APP_DIR]

    if FROZEN:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA", "").strip()
            if appdata:
                candidates.append(Path(appdata) / "CentralCameras")
        else:
            xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
            base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
            candidates.append(base / "central_cameras")

    for candidate in candidates:
        if _is_writable_directory(candidate):
            return candidate

    fallback = Path(tempfile.gettempdir()) / "central_cameras"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


_load_dotenv(APP_DIR / ".env")
DATA_DIR = _select_data_dir()
if DATA_DIR != APP_DIR:
    _load_dotenv(DATA_DIR / ".env")

_config_env = os.environ.get("CAMERAS_CONFIG", "").strip()
if _config_env:
    CONFIG_FILE = Path(_config_env).expanduser()
    if not CONFIG_FILE.is_absolute():
        CONFIG_FILE = (APP_DIR / CONFIG_FILE).resolve()
else:
    CONFIG_FILE = DATA_DIR / "config.json"

CONFIG_LOCK_FILE = CONFIG_FILE.with_name(CONFIG_FILE.name + ".lock")
CONFIG_BACKUP_FILE = CONFIG_FILE.with_name(CONFIG_FILE.name + ".bak")
CLEANUP_LOCK_FILE = CONFIG_FILE.with_name(".cleanup.lock")
SECRET_FILE = DATA_DIR / ".secret_key"

DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650
MAX_ALIAS_LENGTH = 120

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
if not isinstance(getattr(logging, _log_level, None), int):
    _log_level = "INFO"

_log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
logging.basicConfig(
    level=getattr(logging, _log_level),
    format=_log_format,
)
logger = logging.getLogger("central_cameras")

if os.environ.get("LOG_TO_FILE", "1").lower() not in {"0", "false", "no", "off"}:
    _log_file_env = os.environ.get("LOG_FILE", "").strip()
    _log_path = (
        Path(_log_file_env).expanduser()
        if _log_file_env
        else DATA_DIR / "central_cameras.log"
    )
    if not _log_path.is_absolute():
        _log_path = (APP_DIR / _log_path).resolve()

    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
            _file_handler = RotatingFileHandler(
                _log_path,
                maxBytes=2_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            _file_handler.setFormatter(logging.Formatter(_log_format))
            logger.addHandler(_file_handler)
        logger.info("Log de arquivo: %s", _log_path)
    except OSError as exc:
        logger.warning("Não foi possível gravar o log em %s: %s", _log_path, exc)


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Valor inválido para %s=%r; usando %s", name, raw, default)
        return default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _lock_file(handle: Any, *, exclusive: bool, blocking: bool) -> None:
    if _fcntl is not None:
        flags = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
        if not blocking:
            flags |= _fcntl.LOCK_NB
        _fcntl.flock(handle.fileno(), flags)
        return

    import msvcrt

    handle.seek(0)
    mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
    msvcrt.locking(handle.fileno(), mode, 1)


def _unlock_file(handle: Any) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return

    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _open_lock_file(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")

    if _fcntl is None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())

    return handle


def _load_secret_key() -> str:
    env_secret = os.environ.get("SECRET_KEY", "").strip()
    if env_secret:
        return env_secret

    try:
        if SECRET_FILE.exists():
            stored = SECRET_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored

        generated = secrets.token_hex(32)

        try:
            fd = os.open(
                str(SECRET_FILE),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # Outro processo criou a chave primeiro; reutiliza a dele.
            stored = SECRET_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored

            temporary = SECRET_FILE.with_name(
                SECRET_FILE.name + "." + secrets.token_hex(4) + ".tmp"
            )
            temporary.write_text(generated, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, SECRET_FILE)
            return generated

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        return generated
    except OSError:
        logger.warning(
            "Não foi possível persistir a chave de sessão em %s; "
            "usando chave temporária neste processo.",
            SECRET_FILE,
        )
        return secrets.token_hex(32)


app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
app.secret_key = _load_secret_key()
app.config.update(
    MAX_CONTENT_LENGTH=_env_int("MAX_CONTENT_LENGTH", 1024 * 1024, minimum=1024),
    MAX_FORM_MEMORY_SIZE=_env_int("MAX_FORM_MEMORY_SIZE", 1024 * 1024, minimum=1024),
    MAX_FORM_PARTS=100,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
)

_trusted_hosts = [
    host.strip()
    for host in os.environ.get("TRUSTED_HOSTS", "").split(",")
    if host.strip()
]
if _trusted_hosts:
    app.config["TRUSTED_HOSTS"] = _trusted_hosts

AUTH_USERNAME = os.environ.get("APP_USERNAME", "").strip()
AUTH_PASSWORD = os.environ.get("APP_PASSWORD", "")
AUTH_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", "").strip()
AUTH_ENABLED = bool(AUTH_USERNAME and (AUTH_PASSWORD or AUTH_PASSWORD_HASH))

if AUTH_PASSWORD and AUTH_PASSWORD_HASH:
    logger.warning(
        "APP_PASSWORD e APP_PASSWORD_HASH foram definidos; "
        "o hash será usado e a senha em texto puro será ignorada."
    )

if bool(AUTH_USERNAME) != bool(AUTH_PASSWORD or AUTH_PASSWORD_HASH):
    logger.warning(
        "APP_USERNAME e APP_PASSWORD (ou APP_PASSWORD_HASH) precisam ser "
        "definidos juntos; autenticação desativada."
    )

AUTH_SESSION_VERSION = hashlib.sha256(
    f"{AUTH_USERNAME}\0{AUTH_PASSWORD_HASH or AUTH_PASSWORD}".encode("utf-8")
).hexdigest()
LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5, minimum=1, maximum=100)
LOGIN_ATTEMPT_WINDOW_SECONDS = _env_int(
    "LOGIN_ATTEMPT_WINDOW_SECONDS",
    300,
    minimum=30,
    maximum=86400,
)

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    minutes=_env_int(
        "AUTH_SESSION_LIFETIME_MINUTES",
        720,
        minimum=5,
        maximum=43200,
    )
)

_login_failures: dict[str, list[float]] = {}
_login_failures_lock = threading.Lock()

DEFAULT_CONFIG: dict[str, Any] = {
    "system": {
        "record_root": "",
        "retention_days": DEFAULT_RETENTION_DAYS,
    },
    "cameras": {},
}

DANGEROUS_ROOTS = {
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/media",
    "/mnt",
    "/opt",
    "/proc",
    "/root",
    "/sbin",
    "/sys",
    "/tmp",
    "/usr",
    "/var",
}


def _coerce_retention(value: Any) -> int:
    try:
        retention = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "retention_days inválido (%r); usando %s.",
            value,
            DEFAULT_RETENTION_DAYS,
        )
        return DEFAULT_RETENTION_DAYS

    if retention < MIN_RETENTION_DAYS:
        logger.warning(
            "retention_days=%s é menor que o mínimo; ajustando para %s.",
            retention,
            MIN_RETENTION_DAYS,
        )
        return MIN_RETENTION_DAYS

    if retention > MAX_RETENTION_DAYS:
        logger.warning(
            "retention_days=%s é maior que o máximo; ajustando para %s.",
            retention,
            MAX_RETENTION_DAYS,
        )
        return MAX_RETENTION_DAYS

    return retention


def _normalize_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        logger.warning("Configuração não é um objeto JSON; usando defaults.")
        raw = {}

    system = raw.get("system", {})
    if not isinstance(system, dict):
        logger.warning("Seção 'system' inválida; usando defaults.")
        system = {}

    record_root = system.get("record_root", "")
    if not isinstance(record_root, str):
        logger.warning("record_root inválido; usando vazio.")
        record_root = ""

    cameras_raw = raw.get("cameras", {})
    cameras: dict[str, str] = {}
    if isinstance(cameras_raw, dict):
        for key, value in cameras_raw.items():
            if isinstance(key, str) and isinstance(value, str):
                cameras[key] = value
            else:
                logger.warning("Alias de câmera inválido ignorado: %r=%r", key, value)
    else:
        logger.warning("Seção 'cameras' inválida; usando objeto vazio.")

    return {
        "system": {
            "record_root": record_root.strip(),
            "retention_days": _coerce_retention(
                system.get("retention_days", DEFAULT_RETENTION_DAYS)
            ),
        },
        "cameras": cameras,
    }


@contextmanager
def config_lock(exclusive: bool = True) -> Iterator[None]:
    try:
        lock_handle = _open_lock_file(CONFIG_LOCK_FILE)
    except OSError as exc:
        logger.warning("Não foi possível abrir o lock de configuração: %s", exc)
        yield
        return

    acquired = False
    try:
        try:
            _lock_file(lock_handle, exclusive=exclusive, blocking=True)
            acquired = True
        except OSError as exc:
            logger.warning("Não foi possível obter o lock de configuração: %s", exc)

        yield
    finally:
        if acquired:
            try:
                _unlock_file(lock_handle)
            except OSError:
                pass
        lock_handle.close()


def _read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_config_unlocked() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return _normalize_config(DEFAULT_CONFIG)

    try:
        return _normalize_config(_read_json_file(CONFIG_FILE))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.error("Falha ao ler %s: %s", CONFIG_FILE, exc)

        if CONFIG_BACKUP_FILE.exists():
            try:
                config = _normalize_config(_read_json_file(CONFIG_BACKUP_FILE))
                logger.warning("Usando backup de configuração: %s", CONFIG_BACKUP_FILE)
                return config
            except (OSError, UnicodeError, json.JSONDecodeError) as backup_exc:
                logger.error("Backup de configuração também inválido: %s", backup_exc)

        logger.error("Usando configuração padrão segura.")
        return _normalize_config(DEFAULT_CONFIG)


def _atomic_write_config_unlocked(config: dict[str, Any]) -> None:
    config = _normalize_config(config)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(CONFIG_FILE.stat().st_mode)
    except OSError:
        pass

    if CONFIG_FILE.exists():
        try:
            _read_json_file(CONFIG_FILE)
            shutil.copy2(CONFIG_FILE, CONFIG_BACKUP_FILE)
            os.chmod(CONFIG_BACKUP_FILE, 0o600)
        except (OSError, UnicodeError, json.JSONDecodeError):
            logger.warning("Configuração atual inválida; backup não foi atualizado.")

    fd, temporary_name = tempfile.mkstemp(
        prefix=CONFIG_FILE.name + ".",
        suffix=".tmp",
        dir=str(CONFIG_FILE.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(temporary_name, existing_mode or 0o600)
        os.replace(temporary_name, CONFIG_FILE)

        try:
            directory_fd = os.open(
                str(CONFIG_FILE.parent),
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def load_config() -> dict[str, Any]:
    with config_lock(exclusive=False):
        return _read_config_unlocked()


def update_config(mutator: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
    with config_lock(exclusive=True):
        config = _read_config_unlocked()
        result = mutator(config)
        _atomic_write_config_unlocked(config)
        return config, result


def _is_dangerous_root(path: Path) -> bool:
    if path.parent == path:
        return True

    resolved = str(path)
    if resolved in DANGEROUS_ROOTS:
        return True

    try:
        home = str(Path.home().resolve())
    except (OSError, RuntimeError):
        home = ""

    return bool(home and resolved == home)


def check_record_root(raw_root: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw_root, str) or not raw_root.strip():
        return None, "Caminho das gravações não configurado."

    value = raw_root.strip()
    path = Path(value).expanduser()

    if not path.is_absolute():
        return None, "O caminho das gravações precisa ser absoluto."

    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "O caminho das gravações não existe ou não pode ser acessado."

    if not resolved.is_dir():
        return None, "O caminho das gravações não é um diretório."

    if _is_dangerous_root(resolved):
        return None, "Por segurança, este caminho não pode ser usado como raiz de gravações."

    if not os.access(resolved, os.R_OK | os.X_OK):
        return None, "Sem permissão de leitura/execução no caminho das gravações."

    return str(resolved), None


def validate_record_root(raw_root: Any) -> str:
    root, error = check_record_root(raw_root)
    if error or root is None:
        raise ValueError(error or "Caminho das gravações inválido.")

    if not os.access(root, os.W_OK):
        logger.warning(
            "O caminho %s não tem permissão de escrita; a limpeza pode falhar.",
            root,
        )

    return root


def parse_retention(value: Any) -> int:
    if isinstance(value, str):
        value = value.strip()

    try:
        retention = int(value)
    except (TypeError, ValueError):
        raise ValueError("Dias de retenção deve ser um número inteiro.") from None

    if retention < MIN_RETENTION_DAYS or retention > MAX_RETENTION_DAYS:
        raise ValueError(
            f"Dias de retenção deve estar entre {MIN_RETENTION_DAYS} e {MAX_RETENTION_DAYS}."
        )

    return retention


def _is_bad_component(name: Any) -> bool:
    if not isinstance(name, str) or name in {"", ".", ".."}:
        return True
    if "\x00" in name or "/" in name or "\\" in name or ":" in name:
        return True
    if name.endswith((" ", ".")):
        return True
    if any(ord(character) < 32 for character in name):
        return True

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    return name.split(".", 1)[0].upper() in reserved


def safe_child_path(root: str, name: str) -> str:
    if _is_bad_component(name):
        raise ValueError("Nome de caminho inválido.")

    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, name))

    try:
        common = os.path.commonpath([root_real, candidate])
    except ValueError:
        raise ValueError("Caminho fora do diretório permitido.") from None

    if common != root_real:
        raise ValueError("Caminho fora do diretório permitido.")

    return candidate


def safe_video_path(root: str, folder_name: str, filename: str) -> tuple[str, str]:
    if not filename.lower().endswith(".mp4"):
        raise ValueError("Extensão de vídeo não permitida.")

    folder_path = safe_child_path(root, folder_name)
    if not os.path.isdir(folder_path):
        raise FileNotFoundError("Câmera não encontrada.")

    raw_file_path = os.path.join(folder_path, filename)
    if os.path.islink(raw_file_path):
        raise ValueError("Symlinks de vídeo não são permitidos.")

    file_path = safe_child_path(folder_path, filename)
    if not os.path.isfile(file_path):
        raise FileNotFoundError("Vídeo não encontrado.")

    return folder_path, file_path


def _entry_is_safe_dir(entry: os.DirEntry[str], root: str) -> bool:
    try:
        if entry.is_symlink():
            target = os.path.realpath(entry.path)
            if os.path.commonpath([root, target]) != root:
                return False
        return entry.is_dir(follow_symlinks=True)
    except (OSError, ValueError):
        return False


def parse_filename(filename: str) -> str:
    """Converte YYYYMMDD_HHMMSS para DD/MM/YYYY - HH:MM:SS."""

    match = re.match(r"^(\d{8})_(\d{6})", os.path.basename(filename))
    if not match:
        return filename

    try:
        parsed = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return filename

    return parsed.strftime("%d/%m/%Y - %H:%M:%S")


@contextmanager
def cleanup_lock() -> Iterator[bool]:
    try:
        lock_handle = _open_lock_file(CLEANUP_LOCK_FILE)
    except OSError as exc:
        logger.warning("Não foi possível abrir o lock de limpeza: %s", exc)
        yield False
        return

    acquired = False
    try:
        try:
            _lock_file(lock_handle, exclusive=True, blocking=False)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                _unlock_file(lock_handle)
            except OSError:
                pass
        lock_handle.close()


def limpar_gravacoes_antigas() -> int:
    active_grace = _env_int("ACTIVE_FILE_GRACE_SECONDS", 600, minimum=0)

    try:
        with cleanup_lock() as acquired:
            if not acquired:
                logger.info("Outra limpeza já está em execução; ignorando esta rodada.")
                return 0

            config = load_config()
            root, root_error = check_record_root(config["system"]["record_root"])
            if root is None:
                logger.warning(
                    "Limpeza ignorada: %s",
                    root_error or "raiz de gravações não configurada",
                )
                return 0

            retention_days = config["system"]["retention_days"]
            cutoff = time.time() - (retention_days * 86400)
            removed = 0
            errors = 0
            skipped = 0

            def on_walk_error(exc: OSError) -> None:
                nonlocal errors
                errors += 1
                logger.warning("Erro ao percorrer diretório na limpeza: %s", exc)

            for current_dir, dirnames, filenames in os.walk(
                root,
                topdown=True,
                onerror=on_walk_error,
                followlinks=False,
            ):
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not os.path.islink(os.path.join(current_dir, name))
                ]

                for name in filenames:
                    if not name.lower().endswith(".mp4"):
                        continue

                    file_path = os.path.join(current_dir, name)

                    try:
                        file_stat = os.lstat(file_path)
                        if stat.S_ISLNK(file_stat.st_mode):
                            skipped += 1
                            continue
                        if not stat.S_ISREG(file_stat.st_mode):
                            skipped += 1
                            continue
                        if time.time() - file_stat.st_mtime < active_grace:
                            skipped += 1
                            continue
                        if file_stat.st_mtime >= cutoff:
                            continue

                        os.remove(file_path)
                        removed += 1
                        logger.debug(
                            "Gravação removida (%s dias de retenção): %s",
                            retention_days,
                            file_path,
                        )
                    except OSError as exc:
                        errors += 1
                        logger.warning("Falha ao remover %s: %s", file_path, exc)

            logger.info(
                "Limpeza concluída em %s: %d removidos, %d erros, %d ignorados, "
                "%d dias de retenção.",
                root,
                removed,
                errors,
                skipped,
                retention_days,
            )
            return removed
    except Exception:
        logger.exception("Falha inesperada durante a limpeza de gravações.")
        return 0


def start_cleanup_scheduler() -> None:
    interval = _env_int("CLEANUP_INTERVAL_SECONDS", 3600, minimum=60)

    def cleanup_loop() -> None:
        time.sleep(5)
        while True:
            try:
                limpar_gravacoes_antigas()
            except Exception:
                logger.exception("Erro no agendador de limpeza.")
            time.sleep(interval)

    thread = threading.Thread(
        target=cleanup_loop,
        name="central-cameras-cleanup",
        daemon=True,
    )
    thread.start()
    logger.info("Agendador de limpeza iniciado (intervalo de %s segundos).", interval)


def _secure_compare(value: str, expected: str) -> bool:
    return hmac.compare_digest(
        value.encode("utf-8", "surrogatepass"),
        expected.encode("utf-8", "surrogatepass"),
    )


def _safe_next_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if (
        not candidate
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
        or re.search(r"%2f|%5c", candidate, re.IGNORECASE)
    ):
        return None

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None

    if parsed.path.rstrip("/") in {"", "/login", "/logout"}:
        return None

    return candidate[:2048]


def _check_credentials(username: str, password: str) -> bool:
    user_ok = _secure_compare(username, AUTH_USERNAME)

    if AUTH_PASSWORD_HASH:
        try:
            password_ok = check_password_hash(AUTH_PASSWORD_HASH, password)
        except (TypeError, ValueError):
            logger.error("APP_PASSWORD_HASH inválido; login bloqueado.")
            password_ok = False
    else:
        password_ok = _secure_compare(password, AUTH_PASSWORD)

    return user_ok and password_ok


def _login_retry_after(ip: str) -> int:
    now = time.monotonic()
    window = float(LOGIN_ATTEMPT_WINDOW_SECONDS)

    with _login_failures_lock:
        attempts = [
            timestamp
            for timestamp in _login_failures.get(ip, [])
            if now - timestamp < window
        ]
        if attempts:
            _login_failures[ip] = attempts
        else:
            _login_failures.pop(ip, None)

        if len(attempts) < LOGIN_MAX_ATTEMPTS:
            return 0

        return max(1, math.ceil(window - (now - attempts[0])))


def _record_login_failure(ip: str) -> None:
    now = time.monotonic()
    window = float(LOGIN_ATTEMPT_WINDOW_SECONDS)

    with _login_failures_lock:
        attempts = [
            timestamp
            for timestamp in _login_failures.get(ip, [])
            if now - timestamp < window
        ]
        attempts.append(now)
        _login_failures[ip] = attempts

        if len(_login_failures) > 2048:
            for key, values in list(_login_failures.items()):
                if not values or now - values[-1] >= window:
                    _login_failures.pop(key, None)

            if len(_login_failures) > 2048:
                excess = len(_login_failures) - 2048
                oldest = sorted(
                    _login_failures.items(),
                    key=lambda item: item[1][-1] if item[1] else 0,
                )
                for key, _ in oldest[:excess]:
                    _login_failures.pop(key, None)


def _clear_login_failures(ip: str) -> None:
    with _login_failures_lock:
        _login_failures.pop(ip, None)


def _is_authenticated() -> bool:
    return bool(
        AUTH_ENABLED
        and session.get("_auth_user") == AUTH_USERNAME
        and session.get("_auth_version") == AUTH_SESSION_VERSION
    )


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


@app.context_processor
def inject_auth_state() -> dict[str, Any]:
    return {
        "auth_enabled": AUTH_ENABLED,
        "auth_user": session.get("_auth_user"),
    }


@app.before_request
def require_authentication() -> Response | None:
    if not AUTH_ENABLED or request.endpoint in {"login", "static"}:
        return None

    if _is_authenticated():
        return None

    if request.method in {"GET", "HEAD"}:
        return redirect(
            url_for("login", next=request.full_path.rstrip("?"))
        )

    flash("Faça login para continuar.", "warning")
    return redirect(url_for("login"))


@app.before_request
def verify_csrf() -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return

    expected = session.get("_csrf_token", "")
    provided = request.form.get("csrf_token", "")

    if not expected or not provided or not _secure_compare(provided, expected):
        abort(400, description="Token CSRF inválido ou expirado. Recarregue a página.")


@app.after_request
def add_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "media-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    return response


def _render_error(status_code: int, message: str) -> tuple[str, int]:
    return render_template("error.html", code=status_code, message=message), status_code


@app.errorhandler(400)
def handle_bad_request(error: Any) -> tuple[str, int]:
    return _render_error(400, getattr(error, "description", "Requisição inválida."))


@app.errorhandler(401)
def handle_unauthorized(error: Any) -> tuple[str, int]:
    return _render_error(401, "Autenticação necessária.")


@app.errorhandler(403)
def handle_forbidden(error: Any) -> tuple[str, int]:
    return _render_error(403, "Acesso negado.")


@app.errorhandler(404)
def handle_not_found(error: Any) -> tuple[str, int]:
    return _render_error(404, "Recurso não encontrado.")


@app.errorhandler(413)
def handle_too_large(error: Any) -> tuple[str, int]:
    return _render_error(413, "Requisição muito grande.")


@app.errorhandler(500)
def handle_internal_error(error: Any) -> tuple[str, int]:
    logger.error("Erro interno não tratado.", exc_info=True)
    return _render_error(500, "Erro interno do servidor.")


@app.errorhandler(429)
def handle_rate_limited(error: Any) -> tuple[str, int]:
    return _render_error(
        429,
        getattr(
            error,
            "description",
            "Muitas tentativas. Aguarde alguns instantes e tente novamente.",
        ),
    )


@app.errorhandler(503)
def handle_unavailable(error: Any) -> tuple[str, int]:
    return _render_error(503, "Serviço temporariamente indisponível.")


@app.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    if not AUTH_ENABLED:
        return redirect(url_for("index"))

    next_url = _safe_next_url(request.values.get("next"))

    if _is_authenticated():
        return redirect(next_url or url_for("index"))

    error_message = None
    username = ""

    if request.method == "POST":
        client_ip = request.remote_addr or "desconhecido"
        retry_after = _login_retry_after(client_ip)

        if retry_after:
            abort(
                429,
                description=(
                    "Muitas tentativas de login. "
                    f"Tente novamente em {retry_after} segundo(s)."
                ),
            )

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if _check_credentials(username, password):
            _clear_login_failures(client_ip)
            session.clear()
            session.permanent = True
            session["_auth_user"] = AUTH_USERNAME
            session["_auth_version"] = AUTH_SESSION_VERSION
            session["_csrf_token"] = secrets.token_urlsafe(32)
            logger.info("Login bem-sucedido de %s.", client_ip)
            return redirect(next_url or url_for("index"))

        _record_login_failure(client_ip)
        logger.warning("Tentativa de login inválida de %s.", client_ip)
        error_message = "Usuário ou senha inválidos."
        return (
            render_template(
                "login.html",
                error_message=error_message,
                next_url=next_url or "",
                username=username[:120],
            ),
            401,
        )

    return render_template(
        "login.html",
        error_message=None,
        next_url=next_url or "",
        username="",
    )


@app.route("/logout", methods=["POST"])
def logout() -> Response:
    user = session.get("_auth_user")
    session.clear()

    if user:
        logger.info("Logout de %s.", user)

    flash("Sessão encerrada com sucesso.", "success")
    return redirect(url_for("login") if AUTH_ENABLED else url_for("index"))


@app.route("/")
def index() -> str:
    config = load_config()
    system = config["system"]
    aliases = config["cameras"]

    record_root, root_error = check_record_root(system["record_root"])
    root_warning = None
    if record_root and not os.access(record_root, os.W_OK):
        root_warning = "Sem permissão de escrita no diretório de gravações; a limpeza pode falhar."

    cameras = []
    if record_root:
        try:
            with os.scandir(record_root) as entries:
                folders = [
                    entry
                    for entry in entries
                    if _entry_is_safe_dir(entry, record_root)
                ]

            for entry in sorted(
                folders,
                key=lambda item: aliases.get(item.name, item.name).lower(),
            ):
                cameras.append(
                    {
                        "original_name": entry.name,
                        "friendly_name": aliases.get(entry.name, entry.name),
                    }
                )
        except OSError as exc:
            logger.warning("Falha ao listar o diretório de gravações %s: %s", record_root, exc)
            root_error = "Não foi possível ler o diretório de gravações."

    return render_template(
        "index.html",
        cameras=cameras,
        retention_days=system["retention_days"],
        record_root=system["record_root"],
        root_error=root_error,
        root_warning=root_warning,
        root_configured=bool(system["record_root"].strip()),
    )


@app.route("/camera/<folder_name>")
def view_camera(folder_name: str) -> str:
    config = load_config()
    system = config["system"]
    aliases = config["cameras"]

    record_root, root_error = check_record_root(system["record_root"])
    if record_root is None:
        abort(503, description=root_error or "Diretório de gravações indisponível.")

    try:
        folder_path = safe_child_path(record_root, folder_name)
    except ValueError:
        abort(404)

    if not os.path.isdir(folder_path):
        abort(404)

    try:
        with os.scandir(folder_path) as entries:
            filenames = [
                entry.name
                for entry in entries
                if entry.is_file(follow_symlinks=False)
                and entry.name.lower().endswith(".mp4")
            ]
    except OSError as exc:
        logger.warning("Falha ao listar a câmera %s: %s", folder_path, exc)
        abort(503, description="Não foi possível ler as gravações desta câmera.")

    videos = [
        {
            "filename": filename,
            "datetime": parse_filename(filename),
        }
        for filename in sorted(filenames, reverse=True)
    ]

    search = request.args.get("search", "").lower()

    if search:
        videos = [
            video
            for video in videos
            if search in video["filename"].lower()
            or search in video["datetime"].lower()
        ]

    page = request.args.get("page", 1, type=int)
    per_page = 12
    total_videos = len(videos)
    total_pages = math.ceil(total_videos / per_page) if total_videos > 0 else 1
    page = max(1, min(page, total_pages))

    start = (page - 1) * per_page
    end = start + per_page

    return render_template(
        "camera.html",
        folder_name=folder_name,
        friendly_folder=aliases.get(folder_name, folder_name),
        videos=videos[start:end],
        page=page,
        total_pages=total_pages,
        search=search,
    )


@app.route("/video/<folder_name>/<filename>")
def stream_video(folder_name: str, filename: str) -> Response:
    config = load_config()
    record_root, _ = check_record_root(config["system"]["record_root"])
    if record_root is None:
        abort(404)

    try:
        _, file_path = safe_video_path(record_root, folder_name, filename)
    except (ValueError, FileNotFoundError, OSError):
        abort(404)

    try:
        return send_file(
            file_path,
            mimetype="video/mp4",
            conditional=True,
            etag=True,
            max_age=0,
        )
    except OSError:
        abort(404)


@app.route("/config", methods=["GET", "POST"])
def config_page() -> str | Response:
    config = load_config()
    system = config["system"]

    if request.method == "POST":
        raw_root = request.form.get("record_root", "") or ""
        raw_retention = request.form.get("retention_days", "") or ""

        try:
            record_root = validate_record_root(raw_root)
            retention_days = parse_retention(raw_retention)
        except ValueError as exc:
            return (
                render_template(
                    "config.html",
                    config={
                        "record_root": raw_root,
                        "retention_days": raw_retention,
                    },
                    error_message=str(exc),
                    record_root_error=None,
                    record_root_warning=None,
                ),
                400,
            )

        def mutate(current: dict[str, Any]) -> None:
            current["system"]["record_root"] = record_root
            current["system"]["retention_days"] = retention_days

        update_config(mutate)
        logger.info(
            "Configuração atualizada: record_root=%s, retention_days=%s",
            record_root,
            retention_days,
        )
        flash("Configuração salva com sucesso.", "success")
        return redirect(url_for("index"))

    record_root, root_error = check_record_root(system["record_root"])
    root_warning = None
    if record_root and not os.access(record_root, os.W_OK):
        root_warning = "Sem permissão de escrita no diretório de gravações; a limpeza pode falhar."

    return render_template(
        "config.html",
        config=system,
        record_root_error=root_error,
        record_root_warning=root_warning,
        error_message=None,
    )


@app.route("/rename_folder", methods=["POST"])
def rename_folder() -> Response:
    folder_name = (request.form.get("folder_name") or "").strip()
    new_friendly = (request.form.get("new_name") or "").strip()

    if _is_bad_component(folder_name):
        abort(400, description="Nome de câmera inválido.")

    if not new_friendly:
        abort(400, description="O novo nome amigável não pode ficar vazio.")

    if len(new_friendly) > MAX_ALIAS_LENGTH:
        abort(400, description="O novo nome amigável é muito longo.")

    config = load_config()
    record_root, root_error = check_record_root(config["system"]["record_root"])
    if record_root is None:
        abort(503, description=root_error or "Diretório de gravações indisponível.")

    try:
        folder_path = safe_child_path(record_root, folder_name)
    except ValueError:
        abort(400, description="Nome de câmera inválido.")

    if not os.path.isdir(folder_path):
        abort(404, description="Câmera não encontrada.")

    def mutate(current: dict[str, Any]) -> None:
        current["cameras"][folder_name] = new_friendly

    update_config(mutate)
    logger.info("Alias atualizado: %s -> %s", folder_name, new_friendly)
    flash("Nome da câmera atualizado.", "success")
    return redirect(url_for("index"))


def _find_available_port(host: str, preferred: int, attempts: int = 20) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    for port in range(preferred, min(65535, preferred + attempts) + 1):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port

    raise OSError(
        f"Nenhuma porta livre encontrada entre {preferred} e "
        f"{min(65535, preferred + attempts)}."
    )


def _browser_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}/"


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        logger.warning("Não foi possível abrir o navegador automaticamente.")


def _pause_if_frozen() -> None:
    if not FROZEN:
        return
    try:
        input("\nPressione Enter para fechar...")
    except (EOFError, OSError):
        pass


def ensure_config_file() -> None:
    """Cria o config.json inicial para o cliente já encontrar o arquivo."""

    if CONFIG_FILE.exists():
        return

    try:
        with config_lock(exclusive=True):
            if not CONFIG_FILE.exists():
                _atomic_write_config_unlocked(DEFAULT_CONFIG)
                logger.info("Configuração inicial criada em %s", CONFIG_FILE)
    except OSError as exc:
        logger.warning("Não foi possível criar a configuração inicial: %s", exc)


def main() -> None:
    if (
        len(sys.argv) > 1
        and sys.argv[1].lower() in {"hash-password", "hash_password"}
    ):
        password = getpass.getpass("Nova senha: ")
        if len(password) < 8:
            print("A senha deve ter pelo menos 8 caracteres.", file=sys.stderr)
            raise SystemExit(1)

        confirmation = getpass.getpass("Confirme a senha: ")
        if not hmac.compare_digest(password, confirmation):
            print("As senhas não coincidem.", file=sys.stderr)
            raise SystemExit(1)

        print(generate_password_hash(password))
        print(
            "\nDefina APP_PASSWORD_HASH com o valor acima "
            "(e APP_USERNAME com o nome de usuário).",
            file=sys.stderr,
        )
        return

    if len(sys.argv) > 1 and sys.argv[1].lower() in {"purge", "cleanup"}:
        removed = limpar_gravacoes_antigas()
        logger.info("Purge manual finalizado: %d arquivo(s) removido(s).", removed)
        return

    debug = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    scheduler_disabled = os.environ.get("DISABLE_CLEANUP_SCHEDULER", "0") == "1"
    running_from_reloader = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    main_process = not debug or running_from_reloader

    logger.info(
        "Central de Câmeras iniciando (%s).",
        "executável" if FROZEN else "código-fonte",
    )
    logger.info("Pasta de dados: %s", DATA_DIR)
    logger.info("Templates: %s", TEMPLATES_DIR)

    if main_process:
        ensure_config_file()

    if not scheduler_disabled and main_process:
        start_cleanup_scheduler()

    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    preferred_port = _env_int("PORT", 5000, minimum=1, maximum=65535)

    try:
        port = _find_available_port(host, preferred_port)
    except OSError as exc:
        logger.error("Não foi possível iniciar o servidor: %s", exc)
        _pause_if_frozen()
        raise SystemExit(1) from None

    if port != preferred_port:
        logger.warning(
            "A porta %s já está em uso; usando a porta %s.",
            preferred_port,
            port,
        )

    url = _browser_url(host, port)
    open_browser = os.environ.get("OPEN_BROWSER")
    if open_browser is None:
        open_browser = "1" if FROZEN else "0"

    if (
        open_browser.lower() in {"1", "true", "yes", "on"}
        and main_process
    ):
        threading.Timer(1.2, _open_browser, args=(url,)).start()

    logger.info("Painel disponível em %s", url)
    logger.info("Para encerrar, feche esta janela ou pressione Ctrl+C.")

    if debug:
        app.run(
            host=host,
            port=port,
            debug=True,
            use_reloader=True,
            threaded=True,
        )
        return

    try:
        from waitress import serve
    except ImportError:
        logger.warning(
            "Waitress não está instalado; usando o servidor embutido do Flask."
        )
        app.run(
            host=host,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
        return

    serve(
        app,
        host=host,
        port=port,
        threads=_env_int("SERVER_THREADS", 8, minimum=2, maximum=64),
        ident="CentralCameras",
    )


if __name__ == "__main__":
    main()
