# -*- mode: python ; coding: utf-8 -*-
"""Cross-platform one-file PyInstaller definition.

Run this spec on the target operating system; PyInstaller does not cross-compile.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(SPECPATH)
datas = [
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "app" / "ui" / "assets"), "app/ui/assets"),
]
datas += collect_data_files("alembic")

hiddenimports = collect_submodules("app")
hiddenimports += collect_submodules("celery")
hiddenimports += collect_submodules("kombu")
hiddenimports += collect_submodules("billiard")
hiddenimports += [
    "sqlalchemy.dialects.postgresql.psycopg",
    "psycopg_binary",
    "redis",
]

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "sqlite3"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="PesticideShopManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
