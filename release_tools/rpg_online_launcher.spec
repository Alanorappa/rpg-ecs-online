# rpg_online_launcher.spec - build do LAUNCHER (08/10/2026, ver CLAUDE.md).
#
# Uso:  py -3.10 -m PyInstaller release_tools\rpg_online_launcher.spec --noconfirm
#       (invocado por release_tools/build_launcher.ps1)
#
# Diferente de rpg_online_client.spec: inclui tkinter (UI do launcher) e
# EXCLUI pygame/websockets/asyncpg (o launcher nunca roda gameplay nem
# fala com o banco -- so HTTP pra server/http_api.py, via stdlib urllib).
#
# contents_directory="_internal_launcher" (09/10/2026, ver CLAUDE.md/
# PROBLEMAS_ARQUITETURA.md) -- NUNCA deixar no default "_internal". O
# Launcher e o jogo moram na MESMA pasta final (release_client.ps1
# funde dist\Launcher dentro de dist\RPG_Online) -- com os dois usando
# "_internal" por padrao, o robocopy da fusao SOBRESCREVE arquivo de
# dependencia com nome igual (ex: libcrypto-1_1.dll, usada por ambos)
# com a copia de QUALQUER UM dos dois bundles. Bug real: Launcher
# aberto mantem esse arquivo em uso: pelo Windows -- a auto-atualizacao
# falhava com PermissionError ao tentar reescrever o MESMO arquivo (que
# tambem pertence ao jogo). Pasta separada pro Launcher elimina
# qualquer nome em comum entre os dois bundles -- "_internal" fica
# 100% exclusivo do jogo, nunca mais tocado por este build.

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
    contents_directory="_internal_launcher",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Launcher",
)
