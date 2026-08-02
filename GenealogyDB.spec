# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


root = Path(SPECPATH)
datas = [
    (str(root / "schema.sql"), "."),
    (str(root / "USER_MANUAL.md"), "."),
    (str(root / "resources" / "default_config.json"), "resources"),
    (str(root / "plugins" / "statistics.py"), "plugins"),
    (str(root / "assets" / "app_icon.svg"), "assets"),
]

analysis = Analysis(
    [str(root / "viewer.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PIL.Image", "PIL.ImageTk"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="GenealogyDB",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / "assets" / "app.ico"),
)