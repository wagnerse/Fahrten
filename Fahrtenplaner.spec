# PyInstaller spec — produces a single Fahrtenplaner.exe on Windows.
# Build with: pyinstaller Fahrtenplaner.spec --clean --noconfirm

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

block_cipher = None
project_root = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = []

# Streamlit: dynamic imports + bundled static + metadata.
for pkg in ["streamlit"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Metadata that some libraries probe at runtime.
for pkg in [
    "streamlit",
    "pandas",
    "openpyxl",
    "googlemaps",
    "curl_cffi",
    "beautifulsoup4",
]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# App package + version file.
datas += [
    (str(project_root / "fahrtenplaner"), "fahrtenplaner"),
    (str(project_root / "VERSION"), "."),
]

# A few additional hidden imports that Streamlit's dynamic loader may miss.
hiddenimports += [
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.runtime.caching",
]


a = Analysis(
    ["launcher.py"],
    pathex=[str(project_root), str(project_root / "fahrtenplaner")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Fahrtenplaner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,        # no black cmd window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "fahrtenplaner" / "icon.ico") if (project_root / "fahrtenplaner" / "icon.ico").exists() else None,
)
