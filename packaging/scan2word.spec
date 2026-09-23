# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成一键运行的绿色包（解压即用，无需装 Python）。

    python -m PyInstaller packaging\\scan2word.spec --noconfirm --clean

产物：dist/scan2word/  （整目录拷给同事即可，双击 scan2word.exe）
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))     # noqa: F821  (PyInstaller 注入)
VENDOR = os.path.join(ROOT, "vendor")

# 第三方库不在 site-packages，而是随项目放在 vendor/ 里
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

# 用 collect_all 把带数据文件/二进制/dll 的库整体收进来
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

# RapidOCR 的 onnx 模型必须随包（离线识别靠它，缺了首次运行会尝试联网）
MODELS = os.path.join(VENDOR, "rapidocr_onnxruntime", "models")
if not os.path.isdir(MODELS):        # vendor 里没有就用系统装的
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
        # 用不到的大件，排掉能省几百 MB
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

# ---- 瘦身：删掉确定用不到的二进制（都是懒加载或纯可选功能）----
#   opencv_videoio_ffmpeg*.dll  29 MB  只在 VideoCapture/VideoWriter 时才加载，本工具不碰视频
#   PIL/_avif*.pyd               7 MB  只用于 AVIF 解码，本工具只写 PNG
#   PySide6/translations        几 MB  不用 QTranslator，界面文案都是自己写的
EXCLUDE_BIN = ("opencv_videoio_ffmpeg", "_avif", "qt_help", "qtbase_",
               "opencv_ffmpeg")
EXCLUDE_DIR = ("PySide6/translations", "PySide6\\translations")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    if any(k in name for k in EXCLUDE_BIN):
        return False
    if any(d in name for d in ("PySide6/translations",)):
        return False
    return True


a.binaries = TOC([b for b in a.binaries if _keep(b)])      # noqa: F821
a.datas = TOC([d for d in a.datas if _keep(d)])            # noqa: F821

exe = EXE(                                                 # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="scan2word",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # 图形界面程序，不弹黑窗
    disable_windowed_traceback=False,
)
coll = COLLECT(                                            # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="scan2word",
)
