# Recette PyInstaller : le moteur en un dossier autonome (« onedir »), ajouté aux paquets
# .deb et AppImage dans `usr/lib/O-jarvis/engine/`. La coque le lance en priorité.
# Construire : scripts/build-engine.sh
import re

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Paquets qui chargent des fichiers, des bibliothèques ou des modules à l'exécution.
for package in (
    "uvicorn",
    "keyring",
    "secretstorage",
    "sqlite_vec",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "openwakeword",
    "piper",
    "playwright",
    "jsonschema_specifications",
    "tokenizers",
    "sounddevice",
):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

# Le SDK MCP sans sa ligne de commande (`mcp.cli` quitte si `typer` est absent).
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
datas += collect_data_files("mcp")

# Les migrations Alembic sont lues comme des scripts : il faut les fichiers .py eux-mêmes.
datas += collect_data_files("ojarvis", include_py_files=True, includes=["db/migrations/**"])
hiddenimports += collect_all("ojarvis")[2]
# O-jarvis Shell : chargé par ojarvis.extension, jamais importé en dur.
hiddenimports += collect_all("ojarvis_shell")[2]

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff"],
    noarchive=False,
)
# Les bibliothèques qui parlent aux services du système (son, D-Bus, systemd, affichage) :
# celles du système cible, jamais une copie de la machine de construction.
SYSTEM_LIBS = (
    "libpulse",
    "libasound.",
    "libjack.",
    "libportaudio.",
    "libdbus-1.",
    "libsystemd.",
    "libapparmor.",
    "libasyncns.",
    "libdb-5.",
    "libX",
    "libxcb",
    # Leurs dépendances (codecs de libsndfile, tirée par libpulse), et la base que le système
    # garantit (glibc, GCC, zlib) : mises à jour avec lui, sans copie périmée à côté.
    "libsndfile.",
    "libFLAC.",
    "libmp3lame.",
    "libmpg123.",
    "libogg.",
    "libopus.",
    "libvorbis.",
    "libvorbisenc.",
    "libmvec.",
    "libstdc++.",
    "libgcc_s.",
    "libz.",
)
# Les copies renommées par les roues Python (« libxcb-ad31f5a3.so ») restent : PyAV, Whisper
# et les autres les chargent par ce nom exact.
HASHED = re.compile(r"-[0-9a-f]{8}\.so")
a.binaries = [
    b
    for b in a.binaries
    if HASHED.search(b[0]) or not b[0].split("/")[-1].startswith(SYSTEM_LIBS)
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ojarvis-engine",
    console=True,
    strip=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ojarvis-engine", strip=False, upx=False)
