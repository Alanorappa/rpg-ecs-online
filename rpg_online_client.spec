# rpg_online_client.spec — build do CLIENTE para distribuição (alpha).
#
# Uso:  py -3.10 -m PyInstaller rpg_online_client.spec --noconfirm
#
# Estratégia: ONEDIR enxuto.
#   - Só o grafo de imports do main.py entra (pygame-ce + websockets + stdlib);
#     ferramentas de dev (numpy/pedalboard/Pillow do reverb.py, pytest) ficam
#     FORA por não serem importadas — e excluídas explicitamente por garantia.
#   - assets/ e maps/ NÃO são embutidos: o script de build (build_client.ps1)
#     copia as pastas pra dist/ ao lado do exe. main.py faz chdir pra pasta do
#     exe, então caminhos relativos e paths.resource_path resolvem lá.
#   - config.json é criado no primeiro run ao lado do exe (config.py).

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Dev-only / não usados pelo cliente — manter o bundle enxuto
        "numpy", "pedalboard", "PIL", "Pillow",
        "tkinter", "_tkinter",
        "pytest", "_pytest", "pip", "setuptools", "pkg_resources",
        "pygame.tests", "pygame.examples", "pygame.docs",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RPG_Online",
    debug=False,
    strip=False,
    upx=False,          # UPX costuma disparar antivírus em alphas distribuídos
    console=False,      # sem console; crashes vão pra crash.log (main.py)
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RPG_Online",
)
