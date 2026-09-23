# -*- coding: utf-8 -*-
"""程序入口（源码运行与打包都从这里起）。

双击这个文件的绿色包版本就是 `scan2word.exe`。
"""
from __future__ import annotations

import multiprocessing
import os
import sys

# 源码运行时把项目根与 vendor/ 挂上；打包后这些都在包内
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    multiprocessing.freeze_support()        # 打包后多进程/子进程需要

    # 自检模式：不开界面，跑一次真实转换并打印结果。
    # 用来验证"绿色包解压后真的能干活"，不需要人点鼠标。
    # 注意：打包成无控制台窗口的程序后 stdout 看不到，所以结果同时写文件。
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        rest = sys.argv[i + 1:]
        # 路径含空格时可能被拆成多段：能拼回存在的路径就拼回去
        pdf = rest[0] if rest else ""
        rest = rest[1:]
        if not os.path.exists(pdf):
            for k in range(len(rest), 0, -1):
                cand = " ".join([pdf] + rest[:k])
                if os.path.exists(cand):
                    pdf = cand
                    rest = rest[k:]
                    break
        out = rest[0] if rest else os.path.join(_ROOT, "selftest_out")
        lines = []
        ok = False
        try:
            from core.pipeline import ConvertOptions, convert_pdf
            r = convert_pdf(pdf, ConvertOptions(outdir=out, make_searchable_pdf=True))
            lines.append("自检结果：" + r.summary())
            lines.append("  docx: " + str(r.docx))
            lines.append("  报告: " + str(r.report))
            for f in r.findings:
                if f.level != "ok":
                    lines.append("  " + str(f))
            for w in r.warnings:
                lines.append("  ! " + w.splitlines()[0][:200])
            ok = bool(r.ok and r.docx and os.path.exists(r.docx))
        except Exception as exc:
            import traceback
            lines.append("自检异常：" + traceback.format_exc())
        lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
        report = "\n".join(lines)

        # 结果文件放在 exe 同级目录（打包后 __file__ 在 _internal 里，不好找）
        base = (os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False) else _ROOT)
        for target in (os.path.join(base, "selftest_result.txt"),
                       os.path.join(_ROOT, "selftest_result.txt")):
            try:
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(report)
                break
            except Exception:
                continue
        try:
            print(report)
        except Exception:
            pass
        return 0 if ok else 1

    from app.main import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
