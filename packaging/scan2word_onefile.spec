# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：**单文件版** green package。

    python -m PyInstaller packaging\\scan2word_onefile.spec --noconfirm --clean

产物：dist/scan2word单文件版.exe —— 就一个文件，直接发过去，双击就能用。

与目录版的区别（用来选型）：
  单文件版：只需发 1 个文件；但每次启动都要把内容解压到临时目录，冷启动慢几秒，
            个别杀毒软件会更敏感。
  目录版  ：发 1 个 zip；解压一次之后启动很快。
两份产物的功能完全一样。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))     # noqa: F821
VENDOR = os.path.join(ROOT, "vendor")

sys.path.insert(0, ROOT)
sys.path.insert(0, VENDOR)

datas = []
binaries = []
hiddenimports = [
    "docx", "lxml", "lxml.etree",
    "cv2", "numpy", "onnxruntime",
    "PIL", "pyclipper", "shapely", "yaml", "six",
    "pdfplumber", "pdfminer", "pdfminer.high_level", "pdfminer.cmapdb",
    "pypdfium2", "pypdfium2_raw",
    "reportlab", "reportlab.pdfbase._cidfontdata",
    "rapidocr_onnxruntime",
]

from PyInstaller.utils.hooks import collect_all            # noqa: E402

for pkg in ("rapidocr_onnxruntime", "pypdfium2", "pdfminer", "reportlab",
            "pdfplumber", "docx"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

MODELS = os.path.join(VENDOR, "rapidocr_onnxruntime", "models")
if not os.path.isdir(MODELS):
    try:
        import rapidocr_onnxruntime
        MODELS = os.path.join(os.path.dirname(rapidocr_onnxruntime.__file__), "models")
    except Exception:
        MODELS = ""
if MODELS and os.path.isdir(MODELS):
    datas.append((MODELS, "rapidocr_onnxruntime/models"))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT, VENDOR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "torch", "torchvision",
        "transformers", "gradio", "IPython", "notebook", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
        "PySide6.QtNetworkAuth", "PySide6.QtDesigner", "PySide6.QtHelp",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)                                          # noqa: F821

# 与目录版同样的瘦身策略
EXCLUDE_BIN = ("opencv_videoio_ffmpeg", "_avif", "qt_help", "qtbase_", "opencv_ffmpeg")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    if any(k in name for k in EXCLUDE_BIN):
        return False
    if "PySide6/translations" in name:
        return False
    return True


a.binaries = TOC([b for b in a.binaries if _keep(b)])      # noqa: F821
a.datas = TOC([d for d in a.datas if _keep(d)])            # noqa: F821

# 调试用：设 SCAN2WORD_CONSOLE=1 打包出带控制台的版本，
# 失败时错误直接打在终端上，而不是弹对话框（排查 onefile 解压问题必备）。
CONSOLE = os.environ.get("SCAN2WORD_CONSOLE") == "1"
NAME = "scan2word单文件版_debug" if CONSOLE else "scan2word单文件版"

exe = EXE(                                                 # noqa: F821
    pyz, a.scripts, a.binaries, a.datas, [],
    name=NAME,
    debug="all" if CONSOLE else False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=CONSOLE,
    disable_windowed_traceback=False,
)
