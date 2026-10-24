# PyInstaller spec for the Windows desktop program.
# Build on Windows only (PyInstaller cannot cross-compile):
#   pyinstaller packaging/decision_agent.spec --noconfirm
# The result is dist/DecisionAgent/DecisionAgent.exe (onedir layout).
# No API key, no demo data, and no user information are bundled. Secrets are stored per user
# in the Windows credential store; settings and state live in %APPDATA%\DecisionAgent.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
entry = str(root / "packaging" / "entry.py")
static = root / "src" / "decision_agent" / "desktop" / "static"
icon = static / "app.ico"
version_text = (root / "src" / "decision_agent" / "__init__.py").read_text(encoding="utf-8")
version = re.search(r'__version__ = "([^"]+)"', version_text).group(1)

hidden = ["clr", "clr_loader", "pythonnet"]
for name in (
    "decision_agent",
    "uvicorn",
    "pystray",
    "keyring.backends",
    "sklearn.utils",
    "sklearn.feature_extraction",
    "sklearn.metrics.pairwise",
    "winotify",
    "webview",
):
    try:
        hidden += collect_submodules(name)
    except Exception:
        pass

# The UI files are local: no CDN, no remote fonts or scripts. Only these files are bundled.
datas = [(str(path), "decision_agent/desktop/static") for path in static.iterdir() if path.is_file()]
binaries = []
for package in ("webview", "clr_loader", "pythonnet"):
    try:
        datas += collect_data_files(package)
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

excludes = [
    "streamlit", "matplotlib", "pandas.tests", "tkinter.test", "pytest", "IPython", "playwright",
    "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.cocoa", "webview.platforms.android",
]

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(n) for n in (version.split(".") + ["0", "0", "0"])[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([
                StringTable("041204B0", [
                    StringStruct("CompanyName", "Decision Agent"),
                    StringStruct("FileDescription", "Decision Agent - 결정 변경 점검"),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", "DecisionAgent"),
                    StringStruct("OriginalFilename", "DecisionAgent.exe"),
                    StringStruct("ProductName", "Decision Agent"),
                    StringStruct("ProductVersion", version),
                ])
            ]),
            VarFileInfo([VarStruct("Translation", [0x0412, 1200])]),
        ],
    )

a = Analysis(
    [entry],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DecisionAgent",
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon),
    version=version_info,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DecisionAgent", upx=False)
