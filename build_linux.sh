#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python 3 não encontrado. Instale o Python 3 e tente novamente." >&2
    exit 1
fi

if [ ! -d .build-venv ]; then
    "$PYTHON_BIN" -m venv .build-venv
fi

# shellcheck disable=SC1091
source .build-venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean central_cameras.spec

PACKAGE_DIR="dist/CentralCameras-linux"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

# Copia o pacote onedir completo (binário + _internal).
cp -a dist/CentralCameras/. "$PACKAGE_DIR/"
cp LEIA-ME.txt .env.example config.example.json "$PACKAGE_DIR/"
chmod +x "$PACKAGE_DIR/CentralCameras"

# Cria o zip preservando as permissões (inclusive +x do executável).
python - <<'PY'
import zipfile
from pathlib import Path

base = Path("dist")
source = base / "CentralCameras-linux"
archive = base / "CentralCameras-linux.zip"

if archive.exists():
    archive.unlink()

with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in sorted(source.rglob("*")):
        if path.is_file():
            info = zipfile.ZipInfo.from_file(path, path.relative_to(base).as_posix())
            bundle.writestr(info, path.read_bytes())
PY

echo
echo "Pronto: dist/CentralCameras-linux.zip"
