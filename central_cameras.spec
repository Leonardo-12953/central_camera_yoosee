# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

project_dir = Path(SPECPATH)

# Templates e assets ficam dentro de _internal (onedir). O runtime não extrai
# nada para %TEMP%, então não sobram pastas _MEI após fechar o programa.
datas = [
    (str(project_dir / "templates"), "templates"),
    (str(project_dir / "static"), "static"),
]

# O Live Viewer faz parte do mesmo pacote. Segredos, streams e logs são
# criados no computador de destino e nunca entram no executável.
viewer_dir = project_dir / "live_viewer"
if viewer_dir.is_dir():
    for path in viewer_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(viewer_dir)
        if relative.parts[0] in {"streams", "logs", "__pycache__", "models", "events"}:
            continue
        if path.name == "secrets.json":
            continue
        datas.append((str(path), str(Path("live_viewer") / relative.parent)))

hiddenimports = collect_submodules("waitress")

a = Analysis(
    [str(project_dir / "app.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CentralCameras",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CentralCameras",
)
