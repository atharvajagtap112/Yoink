# PyInstaller spec — builds a standalone Yoink.exe anyone can run (no Python).
#
#   pip install pyinstaller
#   cd daemon
#   pyinstaller Yoink.spec
#
# Output: dist/Yoink/Yoink.exe (a folder-app — zip dist/Yoink and share it).
# onedir, not onefile: mediapipe unpacks its models fine this way and starts
# fast; onefile re-extracts hundreds of MB to a temp dir on every launch.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# mediapipe ships its hand/palm .tflite + .binarypb as data files, and its
# native pipeline as extra DLLs — both must ride along or Hands() can't load.
datas = collect_data_files("mediapipe")
binaries = collect_dynamic_libs("mediapipe")

hiddenimports = [
    "win32timezone",     # pulled lazily by pywin32 at runtime
    "pkg_resources",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Don't exclude stdlib test packages: mediapipe -> matplotlib -> pyparsing
    # imports unittest at import time, so cutting it crashes on launch.
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Yoink",
    console=False,          # windowed app: no console box behind the window
    icon="Yoink.ico" if __import__("os").path.exists("Yoink.ico") else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Yoink")
