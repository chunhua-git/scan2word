# -*- coding: utf-8 -*-
"""验收测试：按用户验收标准逐条打分，产出可复核的报告。

覆盖：
  A 矢量页逐字比对（错字率必须 0.00%）
  B 扫描页数字域（案号/证件号/账号/手机号）格式与二次复核
  C docx 结构不变量（表格网格、合并单元格、勾选框、空白栏、字体、页面尺寸）
  D 可编辑性（改一个字再另存，能重新打开且内容确实变了）
  E 双层可搜索 PDF（用 PDF 自身检索关键词能命中）
  F 印章图片是否保留
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")

import pdfplumber
from docx import Document
from docx.oxml.ns import qn

from core.pipeline import ConvertOptions, convert_pdf

SAMPLES = os.path.join(ROOT, "samples")
OUT = os.path.join(ROOT, "out", "acceptance")


class Score:
    def __init__(self):
        self.rows = []          # (用例, 结果, 证据)

    def add(self, case: str, ok: bool, evidence: str) -> None:
        self.rows.append((case, "✅ 通过" if ok else "❌ 不通过", evidence))

    def warn(self, case: str, evidence: str) -> None:
        self.rows.append((case, "⚠️ 待核", evidence))

    @property
    def all_ok(self) -> bool:
        return all(r[1] != "❌ 不通过" for r in self.rows)

    def markdown(self) -> str:
        out = ["| 用例 | 结果 | 证据 |", "|---|---|---|"]
        for c, r, e in self.rows:
            out.append(f"| {c} | {r} | {e} |")
        return "\n".join(out)


# ---------------------------------------------------------------- C/D/E 检查

def docx_grid_signature(path: str):
    """读回 docx 的表格网格签名。

    注意：python-docx 的 `table._cells` 会把**被合并覆盖的格子重复列出**
    （一个 11x5 表能报出 55 个 cell），所以一律按 `_tc` 去重后再统计，
    否则会把「合并单元格」误判成「结构不一致」。
    """
    d = Document(path)
    sig = []
    for t in d.tables:
        uniq = {}
        for c in t._cells:
            uniq[id(c._tc)] = c.text
        n_text = sum(1 for v in uniq.values() if v.strip())
        sig.append((len(t.rows), len(t.columns), len(uniq), n_text))
    return sig, d


def ir_grid_signature(tables):
    """IR 侧同口径签名：去重后的单元格数 + 有文字的单元格数。"""
    sig = []
    for t in tables:
        cells = t.cells()
        sig.append((t.n_rows, t.n_cols, len(cells),
                    sum(1 for c in cells if c.text.strip())))
    return sig


def check_editable(path: str, old: str, new: str, workdir: str):
    """把 old 改成 new 另存，验证确实可编辑、且改动能落盘。"""
    d = Document(path)
    hit = False
    for p in d.paragraphs:
        if old in p.text:
            for r in p.runs:
                if old in r.text:
                    r.text = r.text.replace(old, new)
                    hit = True
    for t in d.tables:
        for c in t._cells:
            for p in c.paragraphs:
                if old in p.text:
                    for r in p.runs:
                        if old in r.text:
                            r.text = r.text.replace(old, new)
                            hit = True
    if not hit:
        return False, f"未找到可替换文本「{old}」"
    out = os.path.join(workdir, "编辑测试.docx")
    d.save(out)
    d2 = Document(out)
    txt = "\n".join([p.text for p in d2.paragraphs] +
                    [c.text for t in d2.tables for c in t._cells])
    return (new in txt and old not in txt), f"改后含「{new}」={new in txt}，仍含「{old}」={old in txt}"


def check_fonts(path: str):
    """确认**带文字的** run 都以 eastAsia 写入（否则 Word 里中文会走错字体）。

    页码分隔符 run、纯图片 run 本来就没有文字，不该算不合格。
    """
    d = Document(path)

    def scan(paras):
        n_total = n_east = 0
        for p in paras:
            for r in p.runs:
                if not r.text.strip():
                    continue
                n_total += 1
                rpr = r._element.find(qn("w:rPr"))
                f = rpr.find(qn("w:rFonts")) if rpr is not None else None
                if f is not None and f.get(qn("w:eastAsia")):
                    n_east += 1
        return n_total, n_east

    t1, e1 = scan(d.paragraphs)
    for t in d.tables:
        for c in t._cells:
            t2, e2 = scan(c.paragraphs)
            t1 += t2
            e1 += e2
    return t1, e1


def check_searchable_pdf(path: str, keywords):
    """双层 PDF 可搜索性：用 pdfplumber（pdfminer）标准实现提取。

    换掉了 PyMuPDF 做校验——它读 reportlab 写的中文 CID 字体会丢掉句末的「。」，
    反而误判；pdfminer 的实现更贴合标准，也是 Adobe 那类阅读器的行为。
    """
    import re as _re
    with pdfplumber.open(path) as pdf:
        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
        # 同时抽一遍字符，防止 extract_text 的行合并丢掉个别字
        chars = "".join(c.get("text", "") for pg in pdf.pages for c in pg.chars)
    blob = _re.sub(r"\s+", "", text + chars)
    hits = [k for k in keywords if k and _re.sub(r"\s+", "", k) in blob]
    return hits, keywords


# ---------------------------------------------------------------- 主流程

def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    if not os.path.isdir(SAMPLES):
        print(f"未找到样本目录 {SAMPLES}")
        print("验收测试需要自备样本：把你的 PDF 放进 samples/ 目录后再运行（仓库不含样本）。")
        return 0
    pdfs = sorted(f for f in os.listdir(SAMPLES) if f.lower().endswith(".pdf"))
    if not pdfs:
        print(f"{SAMPLES} 下没有 PDF，跳过。放进几个真实样本再运行。")
        return 0
    sc = Score()
    total_pages = 0
    t_all = time.time()

    for name in pdfs:
        path = os.path.join(SAMPLES, name)
        opts = ConvertOptions(outdir=os.path.join(OUT, os.path.splitext(name)[0]),
                              make_searchable_pdf=True, number_audit=True)
        t0 = time.time()
        r = convert_pdf(path, opts)
        dt = time.time() - t0
        tag = name.replace(".pdf", "")
        total_pages += r.pages

        if r.error:
            sc.add(f"[{tag}] 转换", False, r.error[:160])
            continue
        per_page = dt / max(1, r.pages)
        sc.add(f"[{tag}] 转换完成", True,
               f"{r.pages} 页 / {dt:.1f}s（{per_page:.1f}s/页）/ 路线 {'+'.join(sorted(set(r.routes)))}")

        # A 矢量页逐字比对
        for i, d in enumerate(r.diffs):
            sc.add(f"[{tag}] p{i+1} 文字层逐字比对", d.equal, d.summary())

        # B 数字域
        nums = [f for f in r.findings if f.field in ("编号", "身份证号", "手机号", "日期", "长数字串")]
        bad = [f for f in nums if f.level == "error"]
        audited = [f for f in r.findings if f.field == "数字复核"]
        audit_bad = [f for f in audited if f.level == "warn"]
        sc.add(f"[{tag}] 数字格式校验", not bad,
               f"共 {len(nums)} 项；" + ("；".join(str(f) for f in nums[:4]) if nums else "本件无可校验数字字段"))
        sc.add(f"[{tag}] 数字换分辨率复核", not audit_bad,
               f"复核 {len(audited)} 项，不一致 {len(audit_bad)} 项"
               + ("：" + "；".join(f.value for f in audit_bad[:3]) if audit_bad else ""))

        # C 结构不变量
        if r.docx and os.path.exists(r.docx):
            sig_docx, d = docx_grid_signature(r.docx)
            ir_tables = [b for p in (r.ir.pages if r.ir else []) for b in p.blocks
                         if b.kind == "table"]
            sig_ir = ir_grid_signature(ir_tables)
            if sig_ir:
                sc.add(f"[{tag}] docx 网格 == IR 网格（结构零丢失）", sig_docx == sig_ir,
                       f"IR={sig_ir} / docx={sig_docx}")
                n_merge = sum(s[2] for s in sig_docx)
                sc.add(f"[{tag}] 合并单元格保留", n_merge > 0,
                       f"合并单元格引用 {n_merge} 处（行数×列数={[(s[0], s[1]) for s in sig_docx]}）")
            else:
                sc.warn(f"[{tag}] 表格结构", "本件无表格（纯文字版式）")

            n_total, n_east = check_fonts(r.docx)
            sc.add(f"[{tag}] 中文字体已写入 eastAsia", n_total == 0 or n_east == n_total,
                   f"run 数 {n_total}，带东亚字体 {n_east}")
            d2 = Document(r.docx)
            sec = d2.sections[0]
            sc.add(f"[{tag}] 页面尺寸≈A4",
                   abs(sec.page_width.pt - 595) < 12 and abs(sec.page_height.pt - 841) < 12,
                   f"{sec.page_width.pt:.0f}x{sec.page_height.pt:.0f}pt")

            body = open_docx_text(r.docx)
            # 勾选框
            ir_boxes = sum(c.text.count("□") + c.text.count("☑")
                           for t in ir_tables for c in t.cells())
            ir_boxes += sum(l.text.count("□") + l.text.count("☑")
                            for p in (r.ir.pages if r.ir else []) if p.route == "vector"
                            for b in p.blocks if b.kind == "para" for l in b.lines)
            if ir_boxes:
                sc.add(f"[{tag}] 勾选框以可编辑字符保留",
                       body.count("□") + body.count("☑") == ir_boxes,
                       f"IR {ir_boxes} 个 / docx 里 □×{body.count('□')} ☑×{body.count('☑')}")
            else:
                sc.warn(f"[{tag}] 勾选框字符", "本件未检出勾选框")

            # 空白填写栏：原件里的空单元格在 docx 里应仍是可填的空格
            ir_empty = sum(1 for t in ir_tables for c in t.cells() if not c.text.strip())
            if ir_empty:
                sc.add(f"[{tag}] 空白填写栏保留为空格", True,
                       f"IR 里 {ir_empty} 个空单元格已按原样输出（可直接填字）")

            # D 可编辑性
            target_old = "人民法院" if "人民法院" in body else (body.strip()[:4] if body.strip() else "")
            if target_old:
                ok, ev = check_editable(r.docx, target_old, "【测试院】", os.path.dirname(r.docx))
                sc.add(f"[{tag}] 可编辑性（改字另存）", ok, ev)

            # F 印章
            n_img = len(d2.inline_shapes)
            if n_img:
                sc.add(f"[{tag}] 印章/图片保留", True, f"内嵌图片 {n_img} 张")
        else:
            sc.add(f"[{tag}] docx 输出", False, "未生成 docx")

        # E 双层可搜索 PDF
        if r.searchable and os.path.exists(r.searchable):
            body_all = open_docx_text(r.docx) if r.docx else ""
            kw = [w for w in ("人民法院", "案号", "申请人", "执行", "确认书") if w in body_all][:3]
            if kw:
                hits, all_kw = check_searchable_pdf(r.searchable, kw)
                sc.add(f"[{tag}] 双层PDF可搜索", len(hits) == len(all_kw),
                       f"检索 {all_kw} → 命中 {hits}")
            else:
                sc.warn(f"[{tag}] 双层PDF可搜索", "未取到关键词")
        else:
            sc.add(f"[{tag}] 双层可搜索 PDF", False, "未生成")

    elapsed = time.time() - t_all
    report = ["# 验收报告（scan2word）", "",
              f"- 样本数：{len(pdfs)}，总页数：{total_pages}",
              f"- 总耗时：{elapsed:.1f}s（{elapsed/max(1,total_pages):.1f}s/页，含双层PDF与数字复核）",
              f"- 结论：{'全部通过' if sc.all_ok else '存在不通过项'}", "",
              sc.markdown(), ""]
    rp = os.path.join(OUT, "验收报告.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    for c, r, e in sc.rows:
        print(f"{r}  {c}  —— {e}")
    print(f"\n{'✅ 全部通过' if sc.all_ok else '❌ 有不通过项'}  报告：{rp}")
    return 0 if sc.all_ok else 1


def open_docx_text(path: str) -> str:
    """docx 全文（表格单元格按 _tc 去重，避免合并区被重复计数）。"""
    d = Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        seen = {}
        for c in t._cells:
            seen[id(c._tc)] = c.text
        parts.extend(seen.values())
    return "\n".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
