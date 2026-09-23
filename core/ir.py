# -*- coding: utf-8 -*-
"""中间文档模型（IR）：PDF 解析结果与 docx 写出之间的唯一契约。

设计目标
--------
1. 双路引擎（矢量路 / OCR 路）产出同一种 IR，写出器只认 IR；
2. IR 里保留足够信息用于「逐字比对验收」（每个字符的来源坐标、置信度）；
3. 引擎可替换：换 OCR / 版面引擎只需重新填 IR。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple


# ----------------------------------------------------------------------------- 基础

@dataclass
class Rect:
    """以 PDF point 为单位的矩形（原点左上，与 PyMuPDF 一致）。"""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def contains_point(self, x: float, y: float, tol: float = 0.0) -> bool:
        return (self.x0 - tol) <= x <= (self.x1 + tol) and (self.y0 - tol) <= y <= (self.y1 + tol)

    def intersection_ratio(self, other: "Rect") -> float:
        """self 与 other 的交集面积占 other 面积的比例（0~1）。"""
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        inter = (ix1 - ix0) * (iy1 - iy0)
        area = other.width * other.height
        return inter / area if area > 0 else 0.0

    def union(self, other: "Rect") -> "Rect":
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0),
                    max(self.x1, other.x1), max(self.y1, other.y1))

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (float(round(self.x0, 2)), float(round(self.y0, 2)),
                float(round(self.x1, 2)), float(round(self.y1, 2)))


# ----------------------------------------------------------------------------- 文本

@dataclass
class TextRun:
    """一段同格式文本。"""
    text: str
    font: str = ""
    size_pt: float = 12.0
    bold: bool = False
    conf: float = 1.0          # OCR 置信度；矢量路恒为 1.0
    source: str = "vector"     # vector | ocr | corrected | lexicon


@dataclass
class TextLine:
    """一行文本（同基线/同 y 带）。"""
    text: str
    bbox: Rect
    runs: List[TextRun] = field(default_factory=list)
    conf: float = 1.0
    source: str = "vector"

    def merged_runs(self) -> List[TextRun]:
        if self.runs:
            return self.runs
        return [TextRun(text=self.text, conf=self.conf, source=self.source)]


# ----------------------------------------------------------------------------- 块

class Block:
    """块基类：段落 / 表格 / 图片。"""
    bbox: Rect
    kind: str = "block"


@dataclass
class Para(Block):
    """段落（含标题）。"""
    lines: List[TextLine] = field(default_factory=list)
    align: str = "left"            # left | center | right
    style: str = "body"            # title | subtitle | body | note
    bbox: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    kind: str = "para"

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


@dataclass
class Cell:
    """表格单元格。row/col 为左上角在网格中的下标。"""
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    lines: List[TextLine] = field(default_factory=list)
    bbox: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    align: str = "left"
    valign: str = "top"            # top | center | bottom
    bold: bool = False
    is_header: bool = False
    images: List["ImageBlock"] = field(default_factory=list)
    ocr_conf: float = 1.0
    notes: List[str] = field(default_factory=list)   # 校验告警（如案号格式不符）

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


@dataclass
class Table(Block):
    """表格。grid[r][c] 为 None 表示该格被合并覆盖。"""
    n_rows: int = 0
    n_cols: int = 0
    grid: List[List[Optional[Cell]]] = field(default_factory=list)
    col_widths: List[float] = field(default_factory=list)   # point
    row_heights: List[float] = field(default_factory=list)  # point
    bbox: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    kind: str = "table"

    def cells(self) -> List[Cell]:
        out = []
        for r in self.grid:
            for c in r:
                if c is not None:
                    out.append(c)
        return out


@dataclass
class ImageBlock(Block):
    """图片块（印章 / 插图 / 无法结构化区域）。"""
    png: bytes = b""
    bbox: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    kind: str = "image"
    role: str = "stamp"            # stamp | figure | region
    label: str = ""


# ----------------------------------------------------------------------------- 页 / 文档

@dataclass
class Page:
    number: int                    # 1 基
    width_pt: float
    height_pt: float
    blocks: List[Block] = field(default_factory=list)
    route: str = "vector"          # vector | ocr
    dpi: int = 0                   # OCR 路渲染 dpi
    render_png: bytes = b""        # 页面渲染图（供预览 / 忽略区域标注用）
    warn: List[str] = field(default_factory=list)

    def text(self) -> str:
        parts = []
        for b in self.blocks:
            if b.kind == "para":
                parts.append(b.text)
            elif b.kind == "table":
                for c in b.cells():
                    if c.text:
                        parts.append(c.text)
        return "\n".join(parts)


@dataclass
class Document:
    source_path: str = ""
    pages: List[Page] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        return "\n".join(p.text() for p in self.pages)

    def stats(self) -> Dict[str, Any]:
        n_cells = n_tables = n_imgs = 0
        confs: List[float] = []
        for p in self.pages:
            for b in p.blocks:
                if b.kind == "table":
                    n_tables += 1
                    for c in b.cells():
                        n_cells += 1
                        confs.append(c.ocr_conf)
                elif b.kind == "image":
                    n_imgs += 1
        lo = [c for c in confs if c < 0.9]
        return dict(pages=len(self.pages), tables=n_tables, cells=n_cells,
                    images=n_imgs, routes=[p.route for p in self.pages],
                    low_conf_cells=len(lo))
