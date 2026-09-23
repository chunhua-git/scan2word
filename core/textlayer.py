# -*- coding: utf-8 -*-
"""矢量路：页面自带文字层时，直接读文字 + 矢量线还原结构（零 OCR 误差）。

电子版 PDF 走这条路，输出与原文逐字一致——这是「错字率≈0」最硬的保障。
PDF 访问统一走 core/pdfdoc.py（许可证干净），本模块不直接依赖任何 PDF 库。
"""
from __future__ import annotations

from typing import List

from .ir import Rect, TextLine, TextRun, Page, Para
from .grid import Item, tables_from_lines
from .pdfdoc import PDFPage

# 勾选框：PDF 里普遍用 Wingdings/Symbol 私用区字符画方框
CHECKBOX_MAP = {
    0xF0A3: "□", 0xF0A8: "□", 0xF06F: "□", 0xF0A1: "□",
    0xF0FE: "☑", 0xF0A4: "☑", 0xF0FD: "☒",
    0xF0FC: "√", 0xF0D8: "√", 0xF0FF: "√",
}

TEXT_LAYER_MIN_CHARS = 30


def _map_char(ch: str, font: str) -> str:
    code = ord(ch)
    if code in CHECKBOX_MAP:
        return CHECKBOX_MAP[code]
    if 0xE000 <= code <= 0xF8FF:
        # 其他私用区字符：符号字体里多半是方框/勾，统一按方框处理并避免丢字
        if "wingding" in font.lower() or "symbol" in font.lower():
            return "□"
    return ch


def has_text_layer(page: PDFPage, min_chars: int = TEXT_LAYER_MIN_CHARS) -> bool:
    """判断页面是否带可用文字层（扫描件为 0）。"""
    return page.has_text_layer(min_chars)


def items_from_page(page: PDFPage) -> List[Item]:
    """把文字层抽成 Item 列表（每行一个），保留字体/字号；矢量路置信度恒为 1。"""
    items: List[Item] = []
    for raw in page.raw_lines():
        runs: List[TextRun] = []
        for sp in raw.spans:
            txt = "".join(_map_char(c, sp.font) for c in sp.text)
            if not txt:
                continue
            runs.append(TextRun(
                text=txt, font=sp.font, size_pt=round(sp.size_pt, 1),
                bold="bold" in sp.font.lower(), conf=1.0, source="vector",
            ))
        text = "".join(r.text for r in runs).strip()
        if not text:
            continue
        items.append(Item(text=text, bbox=raw.bbox, runs=runs,
                          conf=1.0, source="vector"))
    return items


def body_font_size(items: List[Item], default: float = 12.0) -> float:
    """按字符数加权的正文基准字号（标题字数少，不会被它带偏）。"""
    counter: dict[float, int] = {}
    for it in items:
        for r in it.runs:
            key = round(r.size_pt or default, 1)
            counter[key] = counter.get(key, 0) + len(r.text)
    if not counter:
        return default
    total = sum(counter.values())
    acc = 0
    for size, n in sorted(counter.items()):
        acc += n
        if acc >= total * 0.5:
            return size
    return default


def group_leftover(items: List[Item], page_rect: Rect, body_size: float | None = None) -> List[Para]:
    """把没进表格的文本行按行距/缩进聚成段落；大字号标题每行独立成段。

    body_size 传「整页正文基准字号」，避免一页只剩标题时把标题误判为正文。
    """
    if not items:
        return []
    items = sorted(items, key=lambda i: (round(i.bbox.cy, 1), i.bbox.x0))
    sizes = [max((r.size_pt for r in i.runs), default=12.0) for i in items]
    med = body_size or (sorted(sizes)[len(sizes) // 2] if sizes else 12.0)

    paras: List[Para] = []
    cur: List[TextLine] = []
    cur_size = None

    def flush():
        nonlocal cur, cur_size
        if cur:
            x0 = min(l.bbox.x0 for l in cur)
            x1 = max(l.bbox.x1 for l in cur)
            style = "title" if (cur_size or 0) >= med * 1.3 else "body"
            mid = (x0 + x1) / 2
            offset = abs(mid - page_rect.cx)
            thresh = page_rect.width * (0.07 if style == "title" else 0.015)
            paras.append(Para(lines=cur, align="center" if offset < thresh else "left",
                              style=style,
                              bbox=Rect(x0, cur[0].bbox.y0, x1, cur[-1].bbox.y1)))
        cur = []
        cur_size = None

    prev = None
    for it in items:
        ln = it.to_line()
        size = max((r.size_pt for r in it.runs), default=med)
        big = size >= med * 1.3
        if prev is not None:
            gap = ln.bbox.y0 - prev.bbox.y1
            new = (gap > max(6.0, size * 0.9)
                   or abs(size - (cur_size or size)) > 1.5
                   or ln.bbox.x0 - prev.bbox.x0 > 24
                   or (prev.bbox.x0 - ln.bbox.x0) > 12
                   or big)                      # 大字号（标题）每行独立成段
            if new:
                flush()
        cur.append(ln)
        cur_size = size if cur_size is None else cur_size
        prev = ln
    flush()
    return paras


def _in_zone(r: Rect, zones, page_w: float, page_h: float) -> bool:
    """归一化坐标(0~1)的忽略区域命中判定。"""
    for z in zones:
        zz = Rect(z.x0 * page_w, z.y0 * page_h, z.x1 * page_w, z.y1 * page_h)
        if zz.intersection_ratio(r) > 0.5:
            return True
    return False


def build_page(page: PDFPage, number: int, *, dpi: int = 0, ignore_zones=()) -> Page:
    """矢量路构建一页 IR。

    ignore_zones 与 OCR 路同义：落在框里的文字整块跳过（水印/页眉页脚/无关区域）。
    """
    rect = Rect(0, 0, page.width_pt, page.height_pt)
    hs, vs = page.vector_lines()
    items = items_from_page(page)
    if ignore_zones:
        items = [i for i in items
                 if not _in_zone(i.bbox, ignore_zones, rect.width, rect.height)]
    body = body_font_size(items)
    res = tables_from_lines(hs, vs, items)
    out = Page(number=number, width_pt=rect.width, height_pt=rect.height,
               route="vector", dpi=dpi)
    blocks = list(res.tables) + group_leftover(res.leftover, rect, body_size=body)
    blocks.sort(key=lambda b: (round(b.bbox.y0, 1), b.bbox.x0))
    out.blocks = blocks
    if ignore_zones:
        out.warn.append(f"已按忽略区域排除 {len(ignore_zones)} 块")
    return out
