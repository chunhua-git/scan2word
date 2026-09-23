# -*- coding: utf-8 -*-
"""词表纠错回归测试。

为什么单独有这个文件：这条路径曾经带着一个潜伏的 NameError 上线——
`pipeline._apply_lexicon` 用了 `TextRun` 却没导入，而内置词表里的词恰好
从没触发过纠错，所以一直没暴露；直到词表扩充后才第一次真正执行就崩了。
单元测试的直接覆盖比"等真实样本碰巧触发"可靠得多。

    python tests/test_lexicon.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

from core.ir import Page, Table, Cell, Rect, TextLine, TextRun
from core.lexicon import LEGAL_TERMS, check_court_name, correct_text

# 必须被修正的（都是用户实际反馈过的错字）
MUST_FIX = [
    "栽判文书", "变吏后的地址", "匕子方式送达", "如呆提供的地址", "被执行吏",
]
# 必须原样保留的（改了就是误伤）
MUST_KEEP = [
    "申请执行人", "案外人", "已经确认", "人民法院", "最高人民法院",
    "送达地址（方式）确认书", "领款人收款账号确认书", "广西壮族自治区贵港市港北区人民法院",
]


def make_page(text: str, conf: float = 0.9) -> Page:
    """造一页最小 IR：一个 1×1 表格，格子里放一行文字。"""
    cell = Cell(row=0, col=0, bbox=Rect(0, 0, 200, 30),
                lines=[TextLine(text=text, bbox=Rect(0, 0, 200, 20),
                                runs=[TextRun(text=text, size_pt=12, conf=conf,
                                              source="ocr")], conf=conf, source="ocr")])
    table = Table(n_rows=1, n_cols=1, grid=[[cell]], col_widths=[200.0],
                  row_heights=[30.0], bbox=Rect(0, 0, 200, 30))
    return Page(number=1, width_pt=595, height_pt=842, blocks=[table], route="ocr")


def main() -> int:
    fails = []

    print("1) correct_text 单元用例")
    for s in MUST_FIX:
        out, fixes = correct_text(s)
        ok = bool(fixes)
        print(f"   {'✅' if ok else '❌'} 应修正 {s!r} -> {out!r}")
        if not ok:
            fails.append(f"未修正: {s}")
    for s in MUST_KEEP:
        out, fixes = correct_text(s)
        ok = not fixes
        print(f"   {'✅' if ok else '❌'} 应保留 {s!r} -> {out!r} {fixes}")
        if not ok:
            fails.append(f"误伤: {s}")

    print(f"\n2) 词表规模：{len(LEGAL_TERMS)} 条")

    print("\n3) pipeline._apply_lexicon 端到端（这条路径曾经崩过）")
    try:
        from core.pipeline import _apply_lexicon
        page = make_page("栽判文书与变吏后的地址")
        findings = _apply_lexicon(page)
        got = page.blocks[0].cells()[0].text
        ok = "裁判文书" in got and "变更" in got and findings
        print(f"   {'✅' if ok else '❌'} 修正为 {got!r}，产出 {len(findings)} 条留痕")
        for f in findings:
            print(f"        {f}")
        if not ok:
            fails.append("_apply_lexicon 未按预期修正")
        # run 必须被同步替换，否则写出的 docx 还是错字
        runs_text = "".join(r.text for l in page.blocks[0].cells()[0].lines for r in l.runs)
        ok2 = "栽判" not in runs_text and "变吏" not in runs_text
        print(f"   {'✅' if ok2 else '❌'} runs 已同步更新：{runs_text!r}")
        if not ok2:
            fails.append("runs 未同步，docx 里仍会是错字")
    except Exception as exc:
        import traceback
        print(f"   ❌ 抛异常：{exc}\n{traceback.format_exc()}")
        fails.append(f"_apply_lexicon 异常: {exc}")

    print("\n4) 法院名校验")
    for name, want in (("南宁市青秀区人民法院", True), ("钦州市钦北区人民法院", True),
                       ("某某某人民法院", False), ("北京知识产权法院", True)):
        ok, why = check_court_name(name)
        print(f"   {'✅' if ok == want else '❌'} {name} -> {ok}（{why}）")
        if ok != want:
            fails.append(f"法院名判定不符预期: {name}")

    print()
    if fails:
        print(f"❌ {len(fails)} 项未通过：")
        for f in fails:
            print("   - " + f)
        return 1
    print("✅ 词表纠错全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
