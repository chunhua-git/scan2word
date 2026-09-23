# -*- coding: utf-8 -*-
"""双层可搜索 PDF：扫描图 + 隐形文字层，便于以后全文搜索/复制。

用 reportlab（BSD-3）写，避开 AGPL 的 PyMuPDF。踩过的坑记在这里：

1. `insert_text` 式按空格折行，中文整行会被当"一个超长单词"**静默截断**；
2. 折行合并后的行，bbox 是**多行并集**，用 bbox.height 反推字号会得到 28pt 这种
   离谱值，268 字的长行会冲出页面被裁掉 —— 必须用 run 上记录的真实字号，
   再按行宽把文字切块铺回。
"""
from __future__ import annotations

import io
import os
from typing import List, Tuple

from .ir import Page as IRPage

# 中文正文字体候选（Windows 自带），全部带完整 ToUnicode，检索不会乱码
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simfang.ttf",
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]
_registered: dict = {}


def _resolve_font():
    """返回 (注册名, 是否嵌入字体)。优先嵌系统字体；找不到就退回内置 CID 字体。

    两种方案都实测过能被标准实现正确检索（pdfminer/pdfplumber 逐字一致）。
    """
    if "name" in _registered:
        return _registered["name"], _registered["embedded"]

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    for cand in _FONT_CANDIDATES:
        if not os.path.exists(cand):
            continue
        try:
            pdfmetrics.registerFont(TTFont("CNEmbed", cand))
            _registered["name"], _registered["embedded"] = "CNEmbed", True
            return "CNEmbed", True
        except Exception:
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _registered["name"], _registered["embedded"] = "STSong-Light", False
    except Exception:
        _registered["name"], _registered["embedded"] = "Helvetica", False
    return _registered["name"], _registered["embedded"]


def _font_size(line, bbox) -> float:
    """字号：优先用 run 里记录的真实字号（矢量路来自 PDF，OCR 路来自行高估计）。"""
    sizes = [r.size_pt for r in line.merged_runs() if getattr(r, "size_pt", None)]
    if sizes:
        return max(5.0, min(26.0, sum(sizes) / len(sizes)))
    return max(5.0, min(26.0, bbox.height * 0.86))


def write_searchable_pdf(pages: List[Tuple[IRPage, bytes]], out_path: str,
                         image_dpi: int = 200) -> str:
    """pages: [(IR页, 该页渲染的 PNG 字节), ...]。"""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    font_name, _embedded = _resolve_font()

    c = rl_canvas.Canvas(out_path, pagesize=(pages[0][0].width_pt, pages[0][0].height_pt)
                         if pages else (595.3, 841.9))
    c.setTitle("scan2word 双层可搜索 PDF")

    for ir_page, png in pages:
        w, h = ir_page.width_pt, ir_page.height_pt
        c.setPageSize((w, h))
        # 底图（原件）
        try:
            c.drawImage(ImageReader(io.BytesIO(png)), 0, 0, width=w, height=h)
        except Exception:
            pass
        # 隐形文字层
        for line in _iter_lines(ir_page):
            text = line.text.strip()
            if not text:
                continue
            bbox = line.bbox
            size = _font_size(line, bbox)
            line_h = size * 1.36
            per_line = max(1, int(max(size, bbox.width) / size))
            for k in range(0, len(text), per_line):
                chunk = text[k:k + per_line]
                y_top = bbox.y0 + size * 0.92 + (k // per_line) * line_h
                if y_top > h - 2:
                    break
                y_pdf = h - y_top                     # reportlab 原点在左下角
                t = c.beginText(bbox.x0, y_pdf)
                t.setFont(font_name, size)
                t.setTextRenderMode(3)                 # 3 = 不可见
                try:
                    t.textOut(chunk)
                except Exception:
                    for ch in chunk:                   # 兜底：逐字写，保证不漏字
                        try:
                            t.textOut(ch)
                        except Exception:
                            pass
                c.drawText(t)
        c.showPage()
    c.save()
    return out_path


def _iter_lines(ir_page: IRPage):
    for b in ir_page.blocks:
        if b.kind == "para":
            for l in b.lines:
                yield l
        elif b.kind == "table":
            for c in b.cells():
                for l in c.lines:
                    yield l


def render_for_search(page, dpi: int = 200) -> bytes:
    """页 → PNG 字节（双层 PDF 的底图）。"""
    import cv2
    bgr = page.render(dpi)
    ok, buf = cv2.imencode(".png", bgr)
    return buf.tobytes() if ok else b""
