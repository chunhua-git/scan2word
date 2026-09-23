# -*- coding: utf-8 -*-
"""后端一致性复核：当前 PDF 后端（pypdfium2 + pdfplumber）与 PyMuPDF 的对比。

存在的意义：本项目早期用 PyMuPDF 做 PDF 访问，但它是 AGPL-3.0，与"可自由改、
可闭源分发"冲突，因此换成了全宽松许可的 pypdfium2 + pdfplumber + reportlab。
换库必须证明「没有质量损失」，这个脚本就是那份证据，任何人可以复跑复核：

    python tools/compare_pdf_backends.py

要跑 PyMuPDF 一侧需要额外装它（不是本项目依赖）：
    python -m pip install pymupdf
没装也能跑，只是只输出当前后端的结果。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

from core.pdfdoc import PDFDocument
from core.verify import compare_text, normalize

SAMPLES = os.path.join(ROOT, "samples")

try:
    import pymupdf
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False


def mupdf_lines(page):
    """PyMuPDF → 与内部一致的线段形式，便于逐条比对。"""
    hs, vs = [], []
    for item in page.get_drawings():
        for sub in item["items"]:
            if sub[0] == "l":
                p1, p2 = sub[1], sub[2]
                if abs(p1.y - p2.y) < 0.8 and abs(p1.x - p2.x) >= 8:
                    hs.append(("h", round((p1.y + p2.y) / 2, 1)))
                elif abs(p1.x - p2.x) < 0.8 and abs(p1.y - p2.y) >= 8:
                    vs.append(("v", round((p1.x + p2.x) / 2, 1)))
            elif sub[0] == "re":
                r = sub[1]
                if r.width >= 8 and r.height <= 3.0:
                    hs.append(("h", round((r.y0 + r.y1) / 2, 1)))
                elif r.height >= 8 and r.width <= 3.0:
                    vs.append(("v", round((r.x0 + r.x1) / 2, 1)))
    return hs, vs


def main() -> int:
    if not os.path.isdir(SAMPLES):
        print(f"没找到 {SAMPLES}；把 PDF 放进去再运行。")
        return 0
    pdfs = sorted(f for f in os.listdir(SAMPLES) if f.lower().endswith(".pdf"))
    if not pdfs:
        print("samples/ 下没有 PDF。")
        return 0

    print(f"PyMuPDF 对照：{'已安装' if HAS_PYMUPDF else '未安装（只输出当前后端结果）'}")
    if not HAS_PYMUPDF:
        print("  想完整对比：python -m pip install pymupdf\n")

    all_ok = True
    for name in pdfs:
        path = os.path.join(SAMPLES, name)
        tag = name if len(name) <= 40 else name[:38] + "…"
        with PDFDocument(path) as doc:
            page = doc.pages[0]
            arr = page.render(300)
            render_note = f"{arr.shape[1]}x{arr.shape[0]}"
            new_text = page.text()
            nh, nv = page.vector_lines()

        if not HAS_PYMUPDF:
            print(f"  {tag:<42} 渲染 {render_note:<11} 文字 {len(normalize(new_text)):>4} 字  "
                  f"线条 {len(nh)}h/{len(nv)}v")
            continue

        mu = pymupdf.open(path)
        mu_page = mu[0]
        old_text = mu_page.get_text()
        oh, ov = mupdf_lines(mu_page)
        mu.close()

        if not normalize(old_text):
            text_note = "无文字层，跳过文字比对"
            text_ok = True
        else:
            diff = compare_text(old_text, new_text)
            text_ok = diff.equal
            text_note = ("逐字一致 " if diff.equal else "有差异 ") + f"({len(normalize(old_text))} 字)"
        line_ok = abs(len(nh) - len(oh)) <= 2 and abs(len(nv) - len(ov)) <= 2
        all_ok &= text_ok and line_ok
        print(f"  {tag:<42} 渲染 {render_note:<11} "
              f"文字 {'✅ ' if text_ok else '❌ '}{text_note:<18} "
              f"线条 {'✅ ' if line_ok else '❌ '}新 {len(nh)}h/{len(nv)}v vs 旧 {len(oh)}h/{len(ov)}v")

    print()
    if HAS_PYMUPDF:
        print("结论：" + ("✅ 与 PyMuPDF 一致，换库无质量损失" if all_ok
                         else "❌ 存在差异，需要人工核对"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
