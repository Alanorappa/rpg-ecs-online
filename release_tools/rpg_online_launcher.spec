# rpg_online_launcher.spec - build do LAUNCHER (08/10/2026, ver CLAUDE.md).
#
# Uso:  py -3.10 -m PyInstaller release_tools\rpg_online_launcher.spec --noconfirm
#       (invocado por release_tools/build_launcher.ps1)
#
# Diferente de rpg_online_client.spec: inclui tkinter (UI do launcher) e
# EXCLUI pygame/websockets/asyncpg (o launcher nunca roda gameplay nem
# fala com o banco -- so HTTP pra server/http_api.py, via stdlib urllib).

import os

_PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(_PROJECT_ROOT, "launcher_main.py")],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pygame", "websockets", "asyncpg", "aiohttp",
        "numpy", "pedalboard", "PIL", "Pillow",
        "pytest", "_pytest", "pip", "setuptools", "pkg_resources",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Launcher",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Launcher",
)
