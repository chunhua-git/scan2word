# -*- coding: utf-8 -*-
"""矢量路自检：打印还原出的表格结构，并与 PDF 原文逐字比对。

用于排查「表格串格」（左栏栏目名和右栏字段名被并成一条）这类问题：
直接看每个格子里到底装了什么，比盯着整篇文本快得多。

    python tests/run_vector.py
需要 samples/ 下有 PDF（仓库不含样本）。
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
from core.textlayer import build_page
from core.verify import compare_text

S = os.path.join(ROOT, "samples")


def show_table(t):
    print(f"  [表 {t.n_rows}行 x {t.n_cols}列] bbox={t.bbox.as_tuple()}")
    for r in range(t.n_rows):
        row = []
        for c in range(t.n_cols):
            cell = t.grid[r][c]
            if cell is None:
                row.append("·")
            else:
                span = f"({cell.rowspan}x{cell.colspan})" if (cell.colspan > 1 or cell.rowspan > 1) else ""
                row.append(f"[{cell.text.replace(chr(10), '/')}]{span}")
        print("    r%-2d " % r + " | ".join(row))


def main() -> int:
    checked = 0
    if not os.path.isdir(S):
        print(f"未找到样本目录 {S}；把 PDF 放进去再运行。")
        return 0
    for name in sorted(os.listdir(S)):
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(S, name)
        with PDFDocument(path) as doc:
            page = doc.pages[0]
            if not page.has_text_layer():
                print(f"\n===== {name}\n  扫描件（无文字层），跳过——矢量路只处理带文字层的页面。")
                continue
            ir = build_page(page, 1)
            print(f"\n===== {name}")
            for b in ir.blocks:
                if b.kind == "table":
                    show_table(b)
                else:
                    print(f"  [段落 {b.style}/{b.align}] {b.text!r}")
            d = compare_text(page.text(), ir.text())
            print(f"\n  -- 逐字比对：{d.summary()}")
            checked += 1
    if checked == 0:
        print("\n没有可检查的矢量页（samples/ 下的 PDF 都是扫描件或目录为空）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
