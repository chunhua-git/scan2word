# -*- coding: utf-8 -*-
"""单页耗时剖析：找出时间到底花在哪一段。

扫描页的耗时分布不是想当然的（det 输入尺寸、数字复核次数、纠偏都在动），
调性能前先量一遍，别凭感觉优化。

    python tools/profile_page.py [样本文件名] [dpi]
默认取 samples/ 下第一个 PDF、300dpi。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

import cv2

from core import ocr as ocr_mod
from core.engines import create_engine
from core.pdfdoc import PDFDocument

S = os.path.join(ROOT, "samples")


def pick_sample(arg: str | None) -> str:
    if arg:
        p = arg if os.path.isabs(arg) else os.path.join(S, arg)
        return p
    if os.path.isdir(S):
        for fn in sorted(os.listdir(S)):
            if fn.lower().endswith(".pdf"):
                return os.path.join(S, fn)
    return ""


def timed(label, fn):
    t0 = time.time()
    result = fn()
    print(f"  {label:<28} {time.time() - t0:6.2f}s")
    return result


def main() -> int:
    name = pick_sample(sys.argv[1] if len(sys.argv) > 1 else None)
    if not name or not os.path.exists(name):
        print("没找到样本。把 PDF 放进 samples/ 目录，或直接给出文件路径。")
        return 0
    dpi = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    print(f"== {os.path.basename(name)} @{dpi}dpi")
    with PDFDocument(name) as doc:
        page = doc.pages[0]
        engine = timed("引擎加载", lambda: create_engine("rapidocr"))
        bgr = timed("渲染", lambda: ocr_mod.render_page(page, dpi))
        gray = timed("灰度", lambda: cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        scale = 72.0 / dpi
        angle = timed("倾斜估计", lambda: ocr_mod.estimate_skew(gray))
        print(f"       倾斜角 {angle:.3f}°")
        lines = timed("OCR det+rec+cls", lambda: engine.recognize(bgr))
        print(f"       识别行数 {len(lines)}")
        items = timed("行→item", lambda: ocr_mod.lines_to_items(
            lines, scale, page.width_pt, page.height_pt))
        hs, vs = timed("光栅线检测", lambda: ocr_mod.detect_raster_lines(gray, scale))
        print(f"       横线 {len(hs)} 竖线 {len(vs)}")
        boxes = timed("勾选框检测", lambda: ocr_mod.detect_checkboxes(gray, scale, lines))
        print(f"       勾选框 {len(boxes)}")
        blanks = timed("填写栏识别", lambda: ocr_mod.blank_fill_segments(hs, vs))
        print(f"       填写栏线段 {len(blanks)}")
        stamps = timed("印章检测", lambda: ocr_mod.detect_stamps(bgr, scale))
        print(f"       印章 {len(stamps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
