# -*- coding: utf-8 -*-
"""表格网格重建：把「线」还原成「单元格」，再把文本行落进格子里。

核心思想
--------
- 水平线给出「行边界」，垂直线给出「列边界」；
- 某个行带里，只有真正贯穿该行带的竖线才切分单元格 → 天然支持列合并；
- 相邻行带里 x 区间完全一致、且中间没有横线阻隔 → 纵向合并（rowspan）；
- 上下都被横线封住的区域才算单元格，装饰性线条不会造出假表。

矢量路（电子 PDF）与光栅路（扫描件）拿到的都是 Segment，所以这套逻辑两边通用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .ir import Rect, TextLine, TextRun, Table, Cell
from .lines import Segment, grid_components, cluster_positions

# 段落起始标记（1. / 2．/ 一、/ （三）/(1) 等）——出现它就说明上一行是段落末尾而非折行
_LIST_MARKER = re.compile(
    r"^\s*(?:\d{1,2}\s*[．.、)）]|[一二三四五六七八九十]{1,3}\s*[、．.]|"
    r"[（(]\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*[)）])"
)
# 句末标点（判断折行时用）
_SENT_END = "。！？；：:;!?"


@dataclass
class Item:
    """待落格的文本行。"""
    text: str
    bbox: Rect
    runs: List[TextRun] = field(default_factory=list)
    conf: float = 1.0
    source: str = "vector"
    idx: int = -1

    def to_line(self) -> TextLine:
        return TextLine(text=self.text, bbox=self.bbox, runs=list(self.runs),
                        conf=self.conf, source=self.source)


@dataclass
class GridResult:
    tables: List[Table] = field(default_factory=list)
    leftover: List[Item] = field(default_factory=list)   # 没进任何表格的文本行


# ----------------------------------------------------------------------------- 覆盖率判定

def _h_cover(gh: Sequence[Segment], y: float, x0: float, x1: float, tol: float) -> bool:
    """y 位置是否有横线覆盖 [x0,x1]。"""
    for s in gh:
        if abs(s.pos - y) <= tol and s.lo <= x0 + tol and s.hi >= x1 - tol:
            return True
    return False


def _v_cover(gv: Sequence[Segment], x: float, y0: float, y1: float, tol: float) -> bool:
    """x 位置是否有竖线覆盖 [y0,y1]。"""
    for s in gv:
        if abs(s.pos - x) <= tol and s.lo <= y0 + tol and s.hi >= y1 - tol:
            return True
    return False


# ----------------------------------------------------------------------------- 主入口

def build_grid(gh: List[Segment], gv: List[Segment], items: List[Item], *,
               tol: float = 2.2) -> Optional[Tuple[Table, List[Item]]]:
    """由一组互相连接的线段 + 文本行，构建一个 Table；返回 (表, 已消费的 item 下标集合)。"""
    if len(gh) < 2 or len(gv) < 2:
        return None

    ys = cluster_positions([s.pos for s in gh], tol)
    xs = cluster_positions([s.pos for s in gv], tol)
    if len(ys) < 2 or len(xs) < 2:
        return None

    n_bands = len(ys) - 1

    # 每个行带里哪些列边界是「贯穿」的
    present: List[List[int]] = []
    for i in range(n_bands):
        y0, y1 = ys[i], ys[i + 1]
        if y1 - y0 <= 0.5:
            present.append([])
            continue
        present.append([j for j, x in enumerate(xs) if _v_cover(gv, x, y0, y1, tol)])

    # 行带内相邻的贯穿竖线构成一个横向跨度（colspan）
    spans: List[List[Tuple[int, int]]] = []
    for cols in present:
        spans.append([(a, b) for a, b in zip(cols, cols[1:]) if b > a])

    # 纵向合并：上下行带同跨度且中间无横线
    cells: dict[Tuple[int, int, int, int], Cell] = {}
    for i in range(n_bands):
        for (a, b) in spans[i]:
            top, bottom = i, i
            while top - 1 >= 0 and (a, b) in spans[top - 1] and \
                    not _h_cover(gh, ys[top], xs[a], xs[b], tol):
                top -= 1
            while bottom + 1 <= n_bands - 1 and (a, b) in spans[bottom + 1] and \
                    not _h_cover(gh, ys[bottom + 1], xs[a], xs[b], tol):
                bottom += 1
            # 上下都必须被横线封住，否则不是闭合单元格
            if not _h_cover(gh, ys[top], xs[a], xs[b], tol):
                continue
            if not _h_cover(gh, ys[bottom + 1], xs[a], xs[b], tol):
                continue
            key = (top, bottom, a, b)
            if key in cells:
                continue
            cells[key] = Cell(
                row=top, col=a, rowspan=bottom - top + 1, colspan=b - a,
                bbox=Rect(xs[a], ys[top], xs[b], ys[bottom + 1]),
            )

    if not cells:
        return None

    n_rows, n_cols = n_bands, len(xs) - 1
    grid: List[List[Optional[Cell]]] = [[None] * n_cols for _ in range(n_rows)]
    for c in cells.values():
        grid[c.row][c.col] = c
        for r in range(c.row, c.row + c.rowspan):
            for cc in range(c.col, c.col + c.colspan):
                if (r, cc) != (c.row, c.col):
                    grid[r][cc] = None

    table = Table(
        n_rows=n_rows, n_cols=n_cols, grid=grid,
        col_widths=[xs[i + 1] - xs[i] for i in range(n_cols)],
        row_heights=[ys[i + 1] - ys[i] for i in range(n_rows)],
        bbox=Rect(xs[0], ys[0], xs[-1], ys[-1]),
    )

    consumed = assign_items(table, items)
    return table, consumed


def _char_weight(ch: str) -> float:
    """字符相对宽度：中日韩全角算 2，ASCII/半角算 1。

    按字符数等比切是错的——中文数字混排时平均字宽估不准，
    实测会把「统一社会信用代码」的「统」切到左栏去。
    """
    o = ord(ch)
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
            or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F
            or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6):
        return 2.0
    return 1.0


def split_text_by_x(text: str, x0: float, x1: float,
                    spans: List[Tuple[float, float]]) -> List[str]:
    """把一行文字按若干 x 区间切开（按字符宽度加权估位置）。

    spans 必须按 x 升序。返回与 spans 等长的片段列表。
    """
    if not text or x1 <= x0:
        return ["" for _ in spans]
    weights = [_char_weight(ch) for ch in text]
    total_wt = sum(weights) or 1.0
    total_px = x1 - x0

    # 每个字符的 [起, 止] 像素区间
    cum = 0.0
    char_edges: List[Tuple[float, float]] = []
    for w in weights:
        a = x0 + cum / total_wt * total_px
        cum += w
        b = x0 + cum / total_wt * total_px
        char_edges.append((a, b))

    out: List[str] = []
    for (sx0, sx1) in spans:
        buf = []
        for i, (a, b) in enumerate(char_edges):
            center = (a + b) / 2.0
            if sx0 - 0.5 <= center < sx1:
                buf.append(text[i])
        out.append("".join(buf).strip())
    return out


def assign_items(table: Table, items: List[Item], *, tol: float = 1.5) -> List[Item]:
    """把文本行落进单元格；返回未能落格的 item 列表。

    关键点：**跨格的文本行要按列边界切开**，不能整条塞进一个格子。
    光靠"字距"猜栏边界是不够的——实测电子发票里逐字竖排的标签「购买方信息」，
    字号小、字距只有 5.4pt，低于按字宽算的阈值，结果「购」「信」被粘进了右栏。
    既然网格已经算出列边界，就直接用它切，比启发式可靠。
    """
    real = table.cells()
    buckets: dict[int, List[Item]] = {id(c): [] for c in real}
    leftover: List[Item] = []

    for it in items:
        # 命中判据用「宽度够不够一个字」，不用面积占比：
        # 竖排窄栏里的单个字只占整行宽度的百分之几，用占比会被误过滤掉。
        char_w = max(2.0, it.bbox.width / max(1, len(it.text)))
        hits: List[Tuple[Cell, float]] = []
        for c in real:
            ox0, ox1 = max(c.bbox.x0, it.bbox.x0), min(c.bbox.x1, it.bbox.x1)
            oy0, oy1 = max(c.bbox.y0, it.bbox.y0), min(c.bbox.y1, it.bbox.y1)
            if ox1 <= ox0 or oy1 <= oy0:
                continue
            if (ox1 - ox0) < 0.6 * char_w:          # 只是 bbox 的边界毛刺，不算跨格
                continue
            if (oy1 - oy0) < 0.35 * max(1.0, it.bbox.height):
                continue
            area = (ox1 - ox0) * (oy1 - oy0)
            hits.append((c, area / max(1.0, it.bbox.width * it.bbox.height)))
        if not hits:
            leftover.append(it)
            continue

        if len(hits) == 1:
            buckets[id(hits[0][0])].append(it)
            continue

        # 横向跨了多个列 → 就按列边界把文字切开，各归各格。
        # 不做"面积最大者通吃"：发票里「购」只占 9.7% 宽度，通吃会把它并到右栏。
        cols = {round(c.bbox.x0, 1) for c, _ in hits}
        if len(cols) > 1 and len(it.text) > 1:
            spans = [(c.bbox.x0, c.bbox.x1) for c, _ in sorted(hits, key=lambda h: h[0].bbox.x0)]
            parts = split_text_by_x(it.text, it.bbox.x0, it.bbox.x1, spans)
            for (c, _ratio), part in zip(sorted(hits, key=lambda h: h[0].bbox.x0), parts):
                if not part:
                    continue
                runs = [TextRun(text=part,
                                font=(it.runs[0].font if it.runs else ""),
                                size_pt=(it.runs[0].size_pt if it.runs else 12.0),
                                conf=it.conf, source=it.source)]
                buckets[id(c)].append(Item(text=part, conf=it.conf, source=it.source,
                                           runs=runs, bbox=Rect(c.bbox.x0, it.bbox.y0,
                                                                c.bbox.x1, it.bbox.y1)))
            continue

        buckets[id(max(hits, key=lambda h: h[1])[0])].append(it)

    for c in real:
        its = sorted(buckets[id(c)], key=lambda i: (round(i.bbox.cy, 1), i.bbox.x0))
        lines = merge_wrapped_lines([i.to_line() for i in its], c.bbox)
        c.lines = lines
        if its:
            c.ocr_conf = min(i.conf for i in its)
    return leftover


# ----------------------------------------------------------------------------- 折行合并

def _char_width(lines: Sequence[TextLine]) -> float:
    """粗估一个字宽（point）。"""
    vals = []
    for l in lines:
        n = len(l.text.strip())
        if n >= 4 and l.bbox.width > 0:
            vals.append(l.bbox.width / n)
    if not vals:
        return 6.0
    vals.sort()
    return max(3.0, min(20.0, vals[len(vals) // 2]))


def merge_wrapped_lines(lines: List[TextLine], cell: Rect) -> List[TextLine]:
    """把 PDF 里的视觉折行拼回一个段落，同时不把「下一条」粘上来。

    判据：上一行右端顶到格边 + 本行左端贴格左沿 + 本行不是列表项/新条款开头
          + 上一行不是以句末标点结束后另起新段（保险）。
    """
    if len(lines) <= 1:
        return list(lines)
    cw = _char_width(lines)
    out: List[TextLine] = []
    for ln in lines:
        if out:
            prev = out[-1]
            prev_full = prev.bbox.x1 >= cell.x1 - cw * 1.6
            cur_left = ln.bbox.x0 <= cell.x0 + cw * 2.2
            new_para = bool(_LIST_MARKER.match(ln.text))
            if prev_full and cur_left and not new_para:
                prev.text = prev.text + ln.text
                prev.bbox = prev.bbox.union(ln.bbox)
                prev.runs = list(prev.runs) + list(ln.runs)
                prev.conf = min(prev.conf, ln.conf)
                continue
        out.append(TextLine(text=ln.text, bbox=ln.bbox, runs=list(ln.runs),
                            conf=ln.conf, source=ln.source))
    return out


# ----------------------------------------------------------------------------- 组织

def tables_from_lines(hs: List[Segment], vs: List[Segment], items: List[Item], *,
                      tol: float = 2.2, min_segments: int = 4,
                      min_area: float = 4000.0) -> GridResult:
    """一整页：分连通域 → 每域建一张表 → 返回表格 + 未落格文本。"""
    comps = grid_components(hs, vs, min_segments=min_segments, min_area=min_area, tol=max(tol, 3.0))
    rest = list(items)
    tables: List[Table] = []
    for gh, gv in comps:
        res = build_grid(gh, gv, rest, tol=tol)
        if res is None:
            continue
        table, consumed = res
        # 单格「表」多半是标题外的装饰方框，不当作表格，文字退回段落
        if len(table.cells()) < 2:
            continue
        tables.append(table)
        rest = consumed
    tables.sort(key=lambda t: (round(t.bbox.y0, 1), t.bbox.x0))
    rest.sort(key=lambda i: (round(i.bbox.cy, 1), i.bbox.x0))

    # 还有些表格一条线都不画（员工信息表、财务报表、简历）：再试一次空白分栏推断。
    # 这一步有误判风险（双栏文章可能被当成两列表），所以 table_from_whitespace
    # 内部有严格的"单元格文字远窄于列宽"判据，不满足就原样返回 None。
    ws = table_from_whitespace(rest)
    if ws is not None:
        ws_table, ws_rest = ws
        tables.append(ws_table)
        tables.sort(key=lambda t: (round(t.bbox.y0, 1), t.bbox.x0))
        rest = ws_rest

    return GridResult(tables=tables, leftover=rest)


# ----------------------------------------------------------------------------- 无框线表格
#
# 很多表格一条线都不画（员工信息表、财务报表、简历、报价单），只靠空白分栏。
# 这里用「行对齐 + 空白槽」推断结构。最大的风险是把「双栏排版」（论文/说明书）
# 误判成两列表格——那会把连贯的文章切成一堆短格。所以判据里有一条关键的：
# **表格的单元格文字远窄于列宽；而分栏文章里每行文字几乎填满整栏。**

def _group_rows(items: List[Item], *, overlap: float = 0.5) -> List[List[Item]]:
    """按 y 重叠把文本行聚成"表格行"。"""
    rows: List[dict] = []
    for it in sorted(items, key=lambda i: (i.bbox.cy, i.bbox.x0)):
        h = max(0.1, it.bbox.height)
        placed = False
        for r in rows:
            rh = r["y1"] - r["y0"]
            ov = min(r["y1"], it.bbox.y1) - max(r["y0"], it.bbox.y0)
            if ov > overlap * min(h, max(0.1, rh)):
                r["items"].append(it)
                r["y0"] = min(r["y0"], it.bbox.y0)
                r["y1"] = max(r["y1"], it.bbox.y1)
                placed = True
                break
        if not placed:
            rows.append({"y0": it.bbox.y0, "y1": it.bbox.y1, "items": [it]})
    out = []
    for r in sorted(rows, key=lambda r: r["y0"]):
        out.append(sorted(r["items"], key=lambda i: i.bbox.x0))
    return out


def _column_bands(items: List[Item], *, min_gutter: float) -> List[Tuple[float, float]]:
    """把 x 轴扫描一遍，被文字覆盖的位置是"栏"，连续空白的宽槽是"栏间距"。"""
    lo = min(i.bbox.x0 for i in items)
    hi = max(i.bbox.x1 for i in items)
    if hi - lo < 1:
        return []
    step = 0.5
    n = int((hi - lo) / step) + 2
    covered = bytearray(n)
    for it in items:
        a = int((it.bbox.x0 - lo) / step)
        b = int((it.bbox.x1 - lo) / step)
        for k in range(max(0, a), min(n, b + 1)):
            covered[k] = 1
    # 找连续空白段
    gutters: List[Tuple[float, float]] = []
    k = 0
    while k < n:
        if covered[k]:
            k += 1
            continue
        j = k
        while j < n and not covered[j]:
            j += 1
        g0, g1 = lo + k * step, lo + j * step
        # 首尾的空白不算栏间距
        if g0 > lo + 1 and g1 < hi - 1 and (g1 - g0) >= min_gutter:
            gutters.append((g0, g1))
        k = j
    bounds = [lo] + [(g0 + g1) / 2.0 for g0, g1 in gutters] + [hi]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def table_from_whitespace(items: List[Item], *, max_cell_chars: int = 40,
                          min_rows: int = 3, min_cols: int = 2,
                          max_fill: float = 0.62) -> Optional[Tuple[Table, List[Item]]]:
    """尝试把一批文本行还原成无框线表格。返回 (表, 已消费的 item) 或 None。"""
    cand = [i for i in items
            if i.text.strip() and len(i.text.strip()) <= max_cell_chars]
    if len(cand) < min_rows * min_cols:
        return None

    # 标题类（字号明显大于正文）不参与
    sizes = sorted(max((r.size_pt for r in i.runs), default=12.0) for i in cand)
    med_size = sizes[len(sizes) // 2]
    cand = [i for i in cand
            if max((r.size_pt for r in i.runs), default=12.0) <= med_size * 1.25]
    if len(cand) < min_rows * min_cols:
        return None

    rows = _group_rows(cand)
    if len(rows) < min_rows:
        return None

    widths = sorted(max(1.0, i.bbox.width / max(1, len(i.text))) for i in cand)
    char_w = widths[len(widths) // 2]
    bands = _column_bands(cand, min_gutter=max(6.0, char_w * 1.2))
    if len(bands) < min_cols:
        return None

    grid: List[List[Optional[Cell]]] = []
    filled_rows_with_multi = 0
    fill_count = 0
    narrow_count = 0
    for r_i, row in enumerate(rows):
        cells: List[Optional[Cell]] = [None] * len(bands)
        for it in row:
            cx = it.bbox.cx
            for c_i, (bx0, bx1) in enumerate(bands):
                if bx0 <= cx < bx1:
                    cell = Cell(row=r_i, col=c_i, bbox=Rect(bx0, it.bbox.y0, bx1, it.bbox.y1))
                    cell.lines = merge_wrapped_lines([it.to_line()], cell.bbox)
                    cell.ocr_conf = it.conf
                    if cells[c_i] is None:
                        cells[c_i] = cell
                    else:                      # 同一格有多条：拼起来
                        cells[c_i].lines.extend(cell.lines)
                    fill_count += 1
                    if it.bbox.width <= max_fill * (bx1 - bx0):
                        narrow_count += 1
                    break
        grid.append(cells)
        if sum(1 for c in cells if c is not None) >= 2:
            filled_rows_with_multi += 1

    # 校验①：至少两行是"多格行"，否则不成表
    if filled_rows_with_multi < 2:
        return None
    # 校验②：多数单元格文字明显窄于列宽 → 是表格；否则是分栏文章，不能当表格
    if fill_count == 0 or narrow_count / fill_count < 0.6:
        return None
    # 校验③：不能大片空行
    if fill_count < len(rows) * len(bands) * 0.25:
        return None

    table = Table(
        n_rows=len(rows), n_cols=len(bands), grid=grid,
        col_widths=[b1 - b0 for b0, b1 in bands],
        row_heights=[max(8.0, rows[i][0].bbox.height * 1.4) for i in range(len(rows))],
        bbox=Rect(bands[0][0], rows[0][0].bbox.y0, bands[-1][1], rows[-1][0].bbox.y1),
    )
    used = {id(i) for r in rows for i in r}
    rest = [i for i in items if id(i) not in used]
    return table, rest
