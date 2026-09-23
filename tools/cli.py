# -*- coding: utf-8 -*-
"""开发用命令行入口（成品是图形界面；这个脚本只用来跑测试与批量验收）。

    python tools/cli.py 文件或文件夹 [更多...] --outdir out
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from core.pipeline import ConvertOptions, collect_pdfs, convert_pdf


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描件 PDF → 可编辑 Word（离线）")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--engine", default="rapidocr")
    ap.add_argument("--force-ocr", action="store_true", help="忽略文字层，强制 OCR")
    ap.add_argument("--searchable-pdf", action="store_true", help="同时输出双层可搜索 PDF")
    ap.add_argument("--no-number-audit", action="store_true")
    args = ap.parse_args()

    pdfs = collect_pdfs(args.inputs)
    if not pdfs:
        print("没有找到 PDF")
        return 1

    opts = ConvertOptions(
        dpi=args.dpi, engine=args.engine, force_ocr=args.force_ocr,
        make_searchable_pdf=args.searchable_pdf,
        number_audit=not args.no_number_audit,
        outdir=args.outdir,
        progress=lambda c, t, m: print(f"  [{c}/{t}] {m}", flush=True),
    )
    for p in pdfs:
        print(f"\n===== {os.path.basename(p)}")
        r = convert_pdf(p, opts)
        print("  ", r.summary())
        if r.docx:
            print("   docx:", r.docx)
        if r.report:
            print("   报告:", r.report)
        for f in r.findings:
            if f.level != "ok":
                print("   ", f)
        for w in r.warnings[:5]:
            print("    !", w.splitlines()[0][:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
