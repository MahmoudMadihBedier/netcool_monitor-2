# PyInstaller recipe: PORTABLE FOLDER  dist\NetcoolShiftMonitor\NetcoolShiftMonitor.exe
#  - one folder (onedir): nothing is unpacked to %TEMP% at start (often blocked on company PCs)
#  - no UPX, with version information (fewer antivirus false alarms)
#  - the tool's own Chromium inside (if build_exe.bat could install it) -> never touches the user's Edge
# Build on Windows with build_exe.bat
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

WIN = sys.platform.startswith("win")

datas = [
    ("data/CascadedSitesReport_20092026.CSV", "data"),
    ("assets", "assets"),
    ("plugins", "plugins"),      # plugin.json manifests (+ sources, so a dropped-in plugin folder also loads)
]
datas += collect_data_files("playwright", include_py_files=False)   # driver + bundled browser (if installed)

hiddenimports = collect_submodules("keyring.backends")
hiddenimports += collect_submodules("plugins") + collect_submodules("kernel")   # plugins are imported by name at run time
if WIN:
    hiddenimports += ["win32com", "win32com.client", "pythoncom", "pywintypes", "win32timezone", "winsound"]

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "flask", "numpy", "pandas", "matplotlib", "PIL", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NetcoolShiftMonitor",
    icon="assets/app.ico" if WIN else None,
    version="version_info.txt" if WIN else None,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NetcoolShiftMonitor")
