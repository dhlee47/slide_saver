"""Build the portable Windows distribution: dist/SlideBuilder/ and SlideBuilder_portable.zip.
Run from the repo root:  python tools/build_portable.py"""
import shutil
import subprocess
import sys

EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtPdf", "PySide6.QtMultimedia", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "pytest", "tkinter"]
cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "SlideBuilder"]
for m in EXCLUDES:
    cmd += ["--exclude-module", m]
subprocess.run(cmd + ["run_slidebuilder.py"], check=True)
with open("dist/SlideBuilder/README.txt", "w") as f:
    f.write("Slide Builder (portable)\n\nUnzip anywhere and double-click SlideBuilder.exe. No install or Python needed "
            "(Windows 10/11, 64-bit).\nSettings and thumbnail cache live in %LOCALAPPDATA%\\SlideBuilder.\n")
shutil.make_archive("SlideBuilder_portable", "zip", "dist", "SlideBuilder")
print("built SlideBuilder_portable.zip")
