# -*- coding: utf-8 -*-
"""合成扫描件测试：自己造样本，因此知道标准答案，能算出真实错字率。

为什么不用网上下载的样本：下载来的 PDF 没有 ground truth，只能"看着差不多"，
而这恰恰是验收标准里明确拒绝的做法。自己造样本可以：
  1. 精确知道每个字应该是什么 → 能算出客观错字率
  2. 精准构造难点：无框线表格、双栏排版、竖排标签、密集小字、中英数字混排
  3. 覆盖真实件罕见的边界情况

流程：reportlab 画版式 → pypdfium2 渲成图 → 包成"纯图片 PDF"（模拟扫描件）
      → 走 OCR 路转换 → 与标准答案逐字比对

    python tests/test_synthetic.py
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as rl_canvas
import pypdfium2 as pdfium

from core.pdfdoc import PDFDocument
from core.pipeline import ConvertOptions, convert_pdf
from core.verify import compare_text

WORK = os.path.join(ROOT, "out", "synthetic")
FONT = "STSong-Light"
W, H = A4


def _register_font():
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------- 版式定义
# 每个用例返回 (页数说明, 逐行文字列表) —— 文字列表就是标准答案

def case_ruled_table(c) -> list[str]:
    """有框线表格（对照组，应该完美还原）"""
    rows = [["项目", "规格", "数量", "单价", "金额"],
            ["A4 复印纸", "70g", "10", "22.50", "225.00"],
            ["签字笔", "0.5mm", "24", "3.00", "72.00"],
            ["合计", "", "", "", "297.00"]]
    x0, y0, cw, rh = 60, H - 120, 88, 26
    for r, row in enumerate(rows):
        for i, cell in enumerate(row):
            c.rect(x0 + i * cw, y0 - (r + 1) * rh, cw, rh, stroke=1, fill=0)
            c.drawString(x0 + i * cw + 6, y0 - (r + 1) * rh + 8, cell)
    c.drawString(60, y0 + 14, "办公用品采购清单")
    return ["办公用品采购清单"] + [t for row in rows for t in row if t]


def case_borderless_table(c) -> list[str]:
    """无框线表格：只靠空白分栏，没有任何线条（这是真实的弱点）"""
    rows = [["姓名", "部门", "职务", "入职日期"],
            ["张伟", "技术部", "工程师", "2021-03-15"],
            ["李娜", "财务部", "会计", "2019-07-01"],
            ["王强", "市场部", "经理", "2020-11-20"]]
    cols = [70, 200, 320, 440]
    c.drawString(60, H - 100, "员工信息表")
    y = H - 130
    for row in rows:
        for x, cell in zip(cols, row):
            c.drawString(x, y, cell)
        y -= 24
    return ["员工信息表"] + [t for row in rows for t in row]


def case_two_column(c) -> list[str]:
    """双栏排版（论文/说明书常见）。

    注意：正文行要**铺满整栏**才像真实的双栏排版。如果每行只占栏宽的一半，
    那在结构上确实更接近"两列表格"——这时候人眼也难分辨，
    所以构造用例时必须还原真实排版的这个特征（否则是在测一个不存在的场景）。
    """
    left = [
        "本产品采用模块化设计，各部件可独立更换，日常维护无需专业工具。",
        "安装前请先完整阅读本手册，并确认设备电源已断开、压力已释放。",
        "工作环境温度应保持在五至四十摄氏度，避免阳光直射与强干扰。",
        "首次通电后需完成自检流程，指示灯由红转绿表示传感器工作正常。",
    ]
    right = [
        "维护周期建议为每六个月一次，每次须更换前置滤芯并检查密封圈。",
        "长期停用时应当排空管路内残留介质，断电后存放于干燥通风处。",
        "若出现异常噪音或流量明显下降，请立即停机并联系授权服务点。",
        "本手册所述参数如有变更，恕不另行通知，请以纸质说明为准。",
    ]
    # 8pt × 30 字 ≈ 240pt，正好铺满 240pt 宽的栏而不越界（越界会与右栏重叠）
    c.setFont(FONT, 8)
    c.drawString(60, H - 90, "设备使用与维护说明")
    y = H - 130
    for a, b in zip(left, right):
        c.drawString(60, y, a)
        c.drawString(310, y, b)
        y -= 20
    c.setFont(FONT, 11)
    return ["设备使用与维护说明"] + left + right


def case_vertical_label(c) -> list[str]:
    """竖排窄标签 + 右栏内容（发票/表单里最常见，也是刚修过的那个 bug）

    几何要点：竖排标签高 = 字数 × 行距，行间距必须大于它，否则两个标签会在
    y 方向互相穿插（第一版就是栽在这，测出来一堆假错字）。
    """
    c.drawString(60, H - 90, "费用报销单")
    labels = ["报销人信息", "费用明细"]
    contents = ["姓名：张三    工号：A1024", "交通费：320.00  住宿费：880.00"]
    row_gap = 80                        # > 5 字 × 13pt = 65pt
    for i, (lab, con) in enumerate(zip(labels, contents)):
        top = H - 130 - i * row_gap
        c.rect(60, top - 70, 300, 70)
        for k, ch in enumerate(lab):    # 逐字竖排，行距 13pt（11pt 字的正常行距）
            c.drawString(66, top - 14 - k * 13, ch)
        c.drawString(112, top - 30, con)
    return ["费用报销单"] + [ch for lab in labels for ch in lab] + contents


def case_dense_small(c) -> list[str]:
    """小字号密集文字（扫描件里最容易糊的情况）"""
    lines = [
        "第一条 本合同自双方签字盖章之日起生效，有效期三年。",
        "第二条 甲方应按约定时间支付款项，逾期按日万分之五计息。",
        "第三条 乙方应保证所提供资料真实、准确、完整。",
        "第四条 因不可抗力导致无法履行的，双方互不承担违约责任。",
        "第五条 争议提交合同签订地人民法院诉讼解决。",
    ]
    c.drawString(60, H - 90, "服务合同（节选）")
    y = H - 120
    for line in lines:
        c.setFont(FONT, 8)
        c.drawString(60, y, line)
        y -= 17
        c.setFont(FONT, 11)
    return ["服务合同（节选）"] + lines


def case_mixed_script(c) -> list[str]:
    """中英数字混排 + 单号/金额（考数字准确性）"""
    lines = [
        "订单号：SO-2026-0918-0042",
        "客户：ABC Trading Co., Ltd.",
        "产品：Widget Pro X200 数量：150 pcs",
        "单价：USD 12.80 总额：USD 1,920.00",
        "备注：Please ship before 2026-10-01.",
    ]
    c.drawString(60, H - 90, "销售订单确认")
    y = H - 120
    for line in lines:
        c.drawString(60, y, line)
        y -= 22
    return ["销售订单确认"] + lines


CASES = {
    "有框线表格（对照组）": case_ruled_table,
    "无框线表格": case_borderless_table,
    "双栏排版": case_two_column,
    "竖排窄标签": case_vertical_label,
    "小字号密集文字": case_dense_small,
    "中英数字混排": case_mixed_script,
}


# ----------------------------------------------------------------------------- 生成与转换

def build_pdf(path: str, drawer) -> list[str]:
    """用 reportlab 画一页，返回该页的标准答案文字。"""
    c = rl_canvas.Canvas(path, pagesize=A4)
    c.setFont(FONT, 11)
    truth = drawer(c)
    c.showPage()
    c.save()
    return truth


def to_image_only_pdf(src: str, dst: str, dpi: int = 300) -> None:
    """把 PDF 渲染成图再包回 PDF —— 模拟「纯图片型扫描件」（无文字层）。"""
    pdf = pdfium.PdfDocument(src)
    page = pdf[0]
    arr = page.render(scale=dpi / 72).to_pil()
    buf = io.BytesIO()
    arr.save(buf, format="PNG")
    buf.seek(0)
    pdf.close()

    c = rl_canvas.Canvas(dst, pagesize=A4)
    c.drawImage(ImageReader(buf), 0, 0, width=W, height=H)
    c.showPage()
    c.save()


def run_case(name: str, drawer) -> dict:
    safe = re.sub(r"[^\w]+", "_", name)
    src = os.path.join(WORK, f"{safe}_vector.pdf")
    scan = os.path.join(WORK, f"{safe}_scan.pdf")
    truth_lines = build_pdf(src, drawer)
    to_image_only_pdf(src, scan)

    # 确认真的没有文字层
    with PDFDocument(scan) as d:
        has_layer = d.pages[0].has_text_layer()

    result = convert_pdf(scan, ConvertOptions(outdir=os.path.join(WORK, safe),
                                              force_ocr=True, number_audit=False,
                                              make_searchable_pdf=False))
    truth = "\n".join(truth_lines)
    diff = compare_text(truth, result.ir.text() if result.ir else "")
    n_tables = sum(1 for p in (result.ir.pages if result.ir else [])
                   for b in p.blocks if b.kind == "table")
    n_cells = sum(len(b.cells()) for p in (result.ir.pages if result.ir else [])
                  for b in p.blocks if b.kind == "table")
    return dict(name=name, truth=truth, got=(result.ir.text() if result.ir else ""),
                diff=diff, has_layer=has_layer, tables=n_tables, cells=n_cells,
                elapsed=result.elapsed, error=result.error)


def main() -> int:
    os.makedirs(WORK, exist_ok=True)
    if not _register_font():
        print("无法注册中文字体，跳过。")
        return 0

    print(f"{'用例':<22} {'错字率':>8}  {'正确率':>7}  {'表格/格':>8}  {'耗时':>6}  说明")
    print("-" * 96)
    rows = []
    for name, drawer in CASES.items():
        r = run_case(name, drawer)
        rows.append(r)
        if r["error"]:
            print(f"{name:<22} {'—':>8}  {'—':>7}  {'—':>8}  {'—':>6}  ❌ {r['error'][:40]}")
            continue
        err = r["diff"].err_rate
        mark = ""
        if not r["diff"].equal:
            mark = "缺:" + "".join(c for c, _ in r["diff"].missing[:6]) + \
                   " 多:" + "".join(c for c, _ in r["diff"].extra[:6])
        if r["has_layer"]:
            mark = "⚠️ 生成的样本竟有文字层 " + mark
        print(f"{name:<22} {err:>7.2%}  {1-err:>6.2%}  "
              f"{r['tables']:>3}/{r['cells']:<4}  {r['elapsed']:>5.1f}s  {mark}")

    print("-" * 96)
    bad = [r for r in rows if not r.get("error") and not r["diff"].equal]
    print(f"逐字完全一致：{len(rows) - len(bad)}/{len(rows)}")

    rp = os.path.join(WORK, "合成测试报告.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("# 合成扫描件测试报告\n\n")
        f.write("自己造的样本，因此知道标准答案，错字率是客观数字。\n\n")
        f.write("| 用例 | 错字率 | 正确率 | 还原表格/单元格 | 耗时 | 差异 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows:
            if r.get("error"):
                f.write(f"| {r['name']} | — | — | — | — | ❌ {r['error'][:60]} |\n")
                continue
            d = r["diff"]
            detail = "逐字一致" if d.equal else f"缺 {d.missing[:8]} / 多 {d.extra[:8]}"
            f.write(f"| {r['name']} | {d.err_rate:.2%} | {1-d.err_rate:.2%} | "
                    f"{r['tables']}/{r['cells']} | {r['elapsed']:.1f}s | {detail} |\n")
        f.write("\n## 标准答案 vs 实际输出（逐用例）\n\n")
        for r in rows:
            if r.get("error"):
                continue
            f.write(f"### {r['name']}\n\n```\n【标准答案】\n{r['truth']}\n\n"
                    f"【实际输出】\n{r['got']}\n```\n\n")
    print(f"报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
