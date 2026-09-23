# -*- coding: utf-8 -*-
"""转换流水线：PDF → (IR) → .docx / 双层PDF / 质检报告。

双路自动分流：
  页面有文字层 → 矢量路（零 OCR 误差，结构按真实线条还原）
  页面是图片   → OCR 路（300dpi + RapidOCR + 光栅线还原）
同一份 PDF 里两种页面混排也能处理。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .engines import create_engine, OCREngine
from .ir import Document as IRDoc, Page as IRPage, Rect, TextRun
from .pdfdoc import PDFDocument
from . import docx_writer, ocr as ocr_mod, textlayer
from .number_audit import audit_numbers
from .searchable_pdf import write_searchable_pdf, render_for_search
from .verify import (Finding, TextDiff, check_numbers, compare_text,
                     confusable_warnings, latin_confusable_warnings)


@dataclass
class ConvertOptions:
    dpi: int = 300
    engine: str = "rapidocr"
    force_ocr: bool = False              # 忽略文字层，强制 OCR
    prefer_text_layer: bool = True
    make_docx: bool = True
    make_txt: bool = True
    make_searchable_pdf: bool = False
    number_audit: bool = True
    detect_stamps: bool = True
    detect_checkboxes: bool = True
    deskew: bool = True
    ignore_zones: List[Rect] = field(default_factory=list)
    outdir: Optional[str] = None
    progress: Optional[Callable[[int, int, str], None]] = None


@dataclass
class ConvertResult:
    src: str = ""
    docx: Optional[str] = None
    txt: Optional[str] = None
    searchable: Optional[str] = None
    report: Optional[str] = None
    pages: int = 0
    routes: List[str] = field(default_factory=list)
    elapsed: float = 0.0
    findings: List[Finding] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    low_conf: List[tuple] = field(default_factory=list)
    diffs: List[TextDiff] = field(default_factory=list)
    ir: Optional[IRDoc] = None          # 中间模型（供验收比对与界面预览）
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.docx is not None or self.txt is not None)

    def summary(self) -> str:
        if self.error:
            return f"❌ {os.path.basename(self.src)}：{self.error}"
        errs = [f for f in self.findings if f.level == "error"]
        warns = [f for f in self.findings if f.level == "warn"]
        bits = [f"{self.pages} 页", f"{self.elapsed:.0f}s",
                "路线=" + "+".join(sorted(set(self.routes)))]
        if errs:
            bits.append(f"{len(errs)} 项错误")
        if warns:
            bits.append(f"{len(warns)} 项待核")
        if self.low_conf:
            bits.append(f"{len(self.low_conf)} 行低置信")
        return "，".join(bits)


# ----------------------------------------------------------------------------- 单文件

def _out_paths(src: str, outdir: Optional[str]) -> tuple[str, str]:
    base = os.path.splitext(os.path.basename(src))[0]
    folder = outdir or os.path.dirname(os.path.abspath(src))
    os.makedirs(folder, exist_ok=True)
    return folder, base


def _progress(opts: ConvertOptions, cur: int, total: int, msg: str) -> None:
    if opts.progress:
        try:
            opts.progress(cur, total, msg)
        except Exception:
            pass


def convert_pdf(src: str, opts: Optional[ConvertOptions] = None) -> ConvertResult:
    """把一个 PDF 转成 docx（+ 可选双层 PDF / 报告）。"""
    opts = opts or ConvertOptions()
    t0 = time.time()
    res = ConvertResult(src=src)
    folder, base = _out_paths(src, opts.outdir)

    try:
        pdf = PDFDocument(src)
    except Exception as exc:
        res.error = f"打不开文件：{exc}"
        return res

    engine: Optional[OCREngine] = None
    ir = IRDoc(source_path=src, meta={"pages": pdf.page_count})
    previews: List[tuple] = []

    try:
        total = pdf.page_count
        for i, pypage in enumerate(pdf):
            _progress(opts, i, total, f"第 {i + 1}/{total} 页")
            use_vector = (opts.prefer_text_layer and not opts.force_ocr
                          and textlayer.has_text_layer(pypage))
            if use_vector:
                page = textlayer.build_page(pypage, i + 1, ignore_zones=opts.ignore_zones)
                # 矢量路自检：与 PDF 原文逐字比对，错字率必须为 0
                d = compare_text(pypage.text(), page.text())
                res.diffs.append(d)
                if not d.equal:
                    page.warn.append("与 PDF 原文比对存在差异：" + d.summary())
            else:
                if engine is None:
                    engine = create_engine(opts.engine)
                cfg = ocr_mod.OCRConfig(
                    dpi=opts.dpi, engine=opts.engine,
                    deskew=opts.deskew,
                    detect_checkboxes=opts.detect_checkboxes,
                    detect_stamps=opts.detect_stamps,
                    ignore_zones=opts.ignore_zones,
                )
                page = ocr_mod.build_page(pypage, i + 1, cfg, engine=engine)
                line_confs = []
                for l in _iter_lines(page):
                    line_confs.append((l.text, l.conf))
                    if l.conf < 0.90:
                        res.low_conf.append((i + 1, l.text, round(l.conf, 3)))
                res.findings.extend(confusable_warnings(line_confs))
                res.findings.extend(latin_confusable_warnings(page.text()))
                # 法律词表纠错 + 法院名校验（只对 OCR 页做；矢量页必须保持逐字一致）
                res.findings.extend(_apply_lexicon(page))

            # 数字域：格式校验 + 换分辨率二次识别
            res.findings.extend(check_numbers(page.text()))
            if opts.number_audit and engine is not None and page.route == "ocr":
                try:
                    res.findings.extend(audit_numbers(pypage, page, engine, base_dpi=opts.dpi))
                except Exception as exc:
                    page.warn.append(f"数字复核跳过：{exc}")

            res.warnings.extend(f"p{page.number}: {w}" for w in page.warn)
            ir.pages.append(page)
            res.routes.append(page.route)

            if opts.make_searchable_pdf:
                previews.append((page, render_for_search(pypage, 200)))

        res.pages = len(ir.pages)
        res.ir = ir

        if opts.make_docx:
            res.docx = docx_writer.build_docx(ir, os.path.join(folder, base + ".docx"))
        if opts.make_txt:
            res.txt = os.path.join(folder, base + ".txt")
            with open(res.txt, "w", encoding="utf-8") as f:
                f.write(_plain_text(ir))
        if opts.make_searchable_pdf and previews:
            res.searchable = write_searchable_pdf(previews, os.path.join(folder, base + "_可搜索.pdf"))
    except Exception as exc:  # pragma: no cover
        import traceback
        res.error = f"{type(exc).__name__}: {exc}"
        res.warnings.append(traceback.format_exc())
    finally:
        pdf.close()

    res.elapsed = time.time() - t0
    try:
        res.report = write_report(res, ir, folder, base)
    except Exception:
        pass
    _progress(opts, res.pages, res.pages, "完成")
    return res


def _iter_lines(page: IRPage):
    for b in page.blocks:
        if b.kind == "para":
            for l in b.lines:
                yield l
        elif b.kind == "table":
            for c in b.cells():
                for l in c.lines:
                    yield l


def _apply_lexicon(page: IRPage) -> List[Finding]:
    """按法律词表修正形近字，并校验法院名是否完整。

    只改「替换后能命中真词」的位置，且每处修改都写进质检报告，可追溯。
    """
    from .lexicon import correct_text, check_court_name, RE_COURT

    findings: List[Finding] = []
    for line in _iter_lines(page):
        original = line.text
        fixed, corrections = correct_text(original)
        if corrections:
            line.text = fixed
            if line.runs:
                base = line.runs[0]
                line.runs = [TextRun(text=fixed, font=base.font, size_pt=base.size_pt,
                                     bold=base.bold, conf=base.conf, source="lexicon")]
            for c in corrections:
                findings.append(Finding("词表纠正", f"{c.before} → {c.after}", "ok",
                                        f"依据法律词表自动修正形近字；上下文「{c.context}」"))
        # 机构名整行校验（只对以「人民法院」结尾的行生效，非对应文档不会触发）
        text = (line.text or "").strip()
        if RE_COURT.match(text) and len(text) <= 40:
            ok, why = check_court_name(text)
            if not ok:
                findings.append(Finding("机构名", text, "warn",
                                        f"{why}，请人工核对这一行"))
    return findings


def _plain_text(ir: IRDoc) -> str:
    parts = []
    for page in ir.pages:
        parts.append(f"========== 第 {page.number} 页（{'矢量还原' if page.route == 'vector' else 'OCR'}）==========")
        for b in page.blocks:
            if b.kind == "para":
                parts.append(b.text)
            elif b.kind == "table":
                for c in b.cells():
                    if c.text.strip():
                        parts.append(f"[{c.row},{c.col}] {c.text}")
            elif b.kind == "image":
                parts.append(f"<{b.label or '图片'} @ {b.bbox.as_tuple()}>")
        parts.append("")
    return "\n".join(parts)


# ----------------------------------------------------------------------------- 报告

def write_report(res: ConvertResult, ir: IRDoc, folder: str, base: str) -> str:
    path = os.path.join(folder, base + ".质检报告.md")
    L: List[str] = []
    L.append(f"# 转换质检报告：{os.path.basename(res.src)}")
    L.append("")
    L.append(f"- 源文件：`{res.src}`")
    L.append(f"- 页数：{res.pages}")
    L.append(f"- 路线：{'、'.join(f'第{i+1}页={r}' for i, r in enumerate(res.routes))}")
    L.append(f"- 耗时：{res.elapsed:.1f}s（{res.elapsed / max(1, res.pages):.1f}s/页）")
    L.append(f"- 输出：{os.path.basename(res.docx) if res.docx else '—'}"
             + (f"、{os.path.basename(res.searchable)}" if res.searchable else ""))
    L.append("")

    # 矢量路逐字比对
    vec = [(i, d) for i, d in enumerate(res.diffs)]
    if vec:
        L.append("## 文字层页面：与 PDF 原文逐字比对")
        for i, d in vec:
            L.append(f"- 第 {i + 1} 页：{d.summary()}")
        L.append("")

    # 数字
    num = [f for f in res.findings if f.field in ("编号", "身份证号", "手机号", "日期", "长数字串", "数字复核")]
    if num:
        L.append("## 数字域核对（编号 / 证件号 / 账号 / 手机号 / 日期）")
        for f in num:
            L.append(f"- {f}")
        L.append("")

    # 词表纠正
    fixes = [f for f in res.findings if f.field == "词表纠正"]
    if fixes:
        L.append("## 词表纠正（已自动修正，逐条留痕）")
        for f in fixes:
            L.append(f"- {f.value} —— {f.note}")
        L.append("")

    # 结构统计
    st = ir.stats()
    L.append("## 结构还原")
    L.append(f"- 表格数：{st['tables']}，单元格数：{st['cells']}，图片（印章等）：{st['images']}")
    for p in ir.pages:
        n_t = sum(1 for b in p.blocks if b.kind == "table")
        n_p = sum(1 for b in p.blocks if b.kind == "para")
        L.append(f"- 第 {p.number} 页：{n_t} 个表格、{n_p} 个段落"
                 + ("（矢量还原，零 OCR 误差）" if p.route == "vector" else f"（OCR {p.dpi}dpi）"))
    L.append("")

    if res.low_conf:
        L.append("## 需人工重点核对的行（置信度 < 0.90）")
        for pg, txt, conf in res.low_conf[:200]:
            L.append(f"- p{pg} conf={conf}: {txt}")
        L.append("")

    warns = [f for f in res.findings if f.level == "warn"]
    if warns:
        L.append("## 待核项")
        for f in warns:
            L.append(f"- {f}")
        L.append("")

    if res.warnings:
        L.append("## 处理过程记录")
        for w in res.warnings:
            L.append(f"- {w}")
        L.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


# ----------------------------------------------------------------------------- 批量

def convert_many(paths: List[str], opts: Optional[ConvertOptions] = None,
                 on_file: Optional[Callable[[int, int, ConvertResult], None]] = None
                 ) -> List[ConvertResult]:
    opts = opts or ConvertOptions()
    out: List[ConvertResult] = []
    for i, p in enumerate(paths):
        r = convert_pdf(p, opts)
        out.append(r)
        if on_file:
            try:
                on_file(i, len(paths), r)
            except Exception:
                pass
    return out


def collect_pdfs(paths: List[str]) -> List[str]:
    """支持拖入文件夹。"""
    out: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for fn in sorted(files):
                    if fn.lower().endswith(".pdf"):
                        out.append(os.path.join(root, fn))
        elif p.lower().endswith(".pdf"):
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(ap)
    return uniq
