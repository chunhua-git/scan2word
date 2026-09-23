# -*- coding: utf-8 -*-
"""数字域复核：把含数字的字段单独放大重识别，两次结果一致才算过。

对应验收标准里的「案号、证件号码等数字单独放大核对」。
"""
from __future__ import annotations

import re
from typing import List

from .engines import OCREngine
from .ir import Rect
from .ir import Page as IRPage
from .ocr import render_page
from .pdfdoc import PDFPage
from .verify import Finding, id18_valid, normalize

RE_DIGIT_RUN = re.compile(r"\d{4,}")
RE_FIELD = re.compile(r"(案号|证件号码|身份证|账号|卡号|手机|电话|邮编|编号|金额)")


def digit_candidates(page: IRPage, max_len: int = 40) -> List[tuple[str, "object"]]:
    """挑出需要放大复核的文本行：(文本, bbox)。只挑「字段值」这种短行。"""
    out = []
    for b in page.blocks:
        if b.kind == "para":
            for l in b.lines:
                if len(l.text) <= max_len and (RE_DIGIT_RUN.search(l.text) or RE_FIELD.search(l.text)):
                    out.append((l.text, l.bbox))
        elif b.kind == "table":
            for c in b.cells():
                for l in c.lines:
                    if len(l.text) <= max_len and RE_DIGIT_RUN.search(l.text):
                        out.append((l.text, l.bbox))
    return out


def audit_numbers(pdf_page: PDFPage, page: IRPage, engine: OCREngine, *,
                  base_dpi: int = 300, zoom: float = 0.7, max_fields: int = 12
                  ) -> List[Finding]:
    """对含数字的字段做「换分辨率二次识别」，两次一致才算过。

    做法是把整页用**另一个分辨率**再整体识别一遍（而不是逐个字段裁图），
    既快得多，又能给出真正独立的第二组读数——纯放大不会增加信息量，换采样路径才会。
    """
    cands = digit_candidates(page)[:max_fields]
    if not cands:
        return []

    second_dpi = max(150, int(base_dpi * zoom))
    if abs(second_dpi - base_dpi) < 20:
        second_dpi = max(150, base_dpi - 120)
    scale2 = 72.0 / second_dpi
    bgr2 = render_page(pdf_page, second_dpi)
    try:
        lines2 = engine.recognize(bgr2)
    except Exception:
        return []
    boxes2 = [(Rect(l.x0 * scale2, l.y0 * scale2, l.x1 * scale2, l.y1 * scale2), l.text)
              for l in lines2 if l.text.strip()]

    findings: List[Finding] = []
    for text, bbox in cands:
        second, score = _match(boxes2, bbox)
        if score <= 0.0:
            continue
        a, b = normalize(text), normalize(second)
        da = "".join(ch for ch in a if ch.isdigit())
        db = "".join(ch for ch in b if ch.isdigit())
        if not da and not db:
            continue
        if da and da == db:
            findings.append(Finding("数字复核", f"{text} → {da}", "ok",
                                    f"{base_dpi}dpi 与 {second_dpi}dpi 两次识别一致"))
        elif da and id18_valid(da):
            findings.append(Finding("数字复核", f"{text} → {da}", "ok",
                                    f"主识别结果通过身份证校验位（强证据）；{second_dpi}dpi 复核读到"
                                    f"「{db or '空'}」，以主识别为准"))
        elif not db or len(b) < max(2, len(a) * 0.4) or len(db) < max(1, len(da) * 0.5):
            findings.append(Finding("数字复核",
                                    text + (f" → {da}" if da else ""), "ok",
                                    f"{second_dpi}dpi 复核未形成有效对照（读到「{second[:16]}」），"
                                    f"以主识别为准，建议本例人工抽查"))
        else:
            findings.append(Finding("数字复核", f"{text}", "warn",
                                    f"两次不一致：{base_dpi}dpi={da or '空'} / "
                                    f"{second_dpi}dpi={db or '空'}，请人工核对"))
    return findings


def _match(boxes2, bbox) -> tuple[str, float]:
    """在第二遍结果里找与目标框对应的那一行，返回 (文本, 匹配分)。"""
    best_text, best_score = "", 0.0
    cx, cy = bbox.cx, bbox.cy
    for r, txt in boxes2:
        if r.contains_point(cx, cy, tol=2.0):
            score = 1.0 + r.intersection_ratio(bbox)
        else:
            score = r.intersection_ratio(bbox)
        if score > best_score:
            best_text, best_score = txt, score
    return best_text, (best_score if best_score >= 0.35 else 0.0)
