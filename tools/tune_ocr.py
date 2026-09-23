# -*- coding: utf-8 -*-
"""OCR 参数寻优：在真实扫描件上比「识别结果一致性 vs 耗时」。

默认值不是拍脑袋定的。实测结论（300dpi 单页 A4 扫描件）：

    det_limit_side_len=1280  最快（约 4.5s），且与 736 的结果逐字一致
    det_limit_side_len=1600  更慢，还多认出一个错字（「口」）
    736                      在部分机器上反而明显更慢

所以默认取 1280。这个脚本用来在你自己的样本上复现/复核这个结论。

    python tools/tune_ocr.py [样本文件名] [dpi]
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

from rapidocr_onnxruntime import RapidOCR

from core.ocr import render_page
from core.pdfdoc import PDFDocument
from core.verify import compare_text

S = os.path.join(ROOT, "samples")


def pick_sample(arg):
    if arg:
        return arg if os.path.isabs(arg) else os.path.join(S, arg)
    if os.path.isdir(S):
        for fn in sorted(os.listdir(S)):
            if fn.lower().endswith(".pdf"):
                return os.path.join(S, fn)
    return ""


def main() -> int:
    name = pick_sample(sys.argv[1] if len(sys.argv) > 1 else None)
    if not name or not os.path.exists(name):
        print("没找到样本。把 PDF 放进 samples/ 目录，或直接给出文件路径。")
        return 0
    dpi = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    with PDFDocument(name) as doc:
        img = render_page(doc.pages[0], dpi)
    print(f"== {os.path.basename(name)} @{dpi}dpi  {img.shape[1]}x{img.shape[0]}")

    baseline = None
    rows = []
    for side in (736, 960, 1280, 1600):
        for threads in (0, 4):
            kwargs = dict(use_cls=True, det_limit_side_len=side, text_score=0.5)
            if threads:
                kwargs["intra_op_num_threads"] = threads
            engine = RapidOCR(**kwargs)
            engine(img)                      # 预热，别把加载时间算进去
            t0 = time.time()
            res, _ = engine(img)
            dt = time.time() - t0
            text = "\n".join(str(t) for _, t, _ in (res or []))
            confs = [c for _, _, c in (res or [])]
            if baseline is None:
                baseline = text
            diff = compare_text(baseline, text)
            rows.append((side, threads, dt, diff))
            print(f"  side={side:<5} threads={threads:<2} {dt:6.2f}s  "
                  f"行={len(res or []):<3} "
                  f"minconf={min(confs) if confs else 0:.3f} "
                  f"avgconf={sum(confs) / max(1, len(confs)):.4f}  "
                  f"vs(736,0): {diff.summary()[:60]}")

    good = [r for r in rows if r[3].equal]
    if good:
        best = min(good, key=lambda r: r[2])
        print(f"\n结论：与基准逐字一致的最快配置是 side={best[0]}, threads={best[1]}"
              f"（{best[2]:.2f}s）")
    else:
        print("\n注意：各配置结果不一致，需要人工核对哪一组更准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
