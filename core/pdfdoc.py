# -*- coding: utf-8 -*-
"""PDF 访问抽象层（许可证干净版）。

为什么要有这一层
----------------
PyMuPDF 虽然好用，但它是 **AGPL-3.0**：把工具打包发给别人 = 分发副本，
必须连带以 AGPL 提供全部源码（含本项目的胶水代码），与"可自由改、可闭源分发"
的硬约束冲突。所以这里换成全宽松许可的组合：

    渲图            pypdfium2      Apache-2.0 / BSD-3-Clause
    文字层 + 坐标   pdfplumber     MIT（底层 pdfminer.six）
    矢量线条        pdfplumber     MIT
    写双层 PDF      reportlab      BSD-3-Clause

**已实测**：pdfplumber 读中文（含 CID 子集字体）文字层与 PyMuPDF 逐字一致；
矢量线条还原出的表格网格也完全相同（11×5、26 格）。所以换掉没有质量损失。

整个项目只通过这个模块碰 PDF，将来要再换引擎也只改这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from .ir import Rect
from .lines import Segment, dedup_segments


# ----------------------------------------------------------------------------- 数据结构

@dataclass
class Span:
    """一段同格式文字（同字体同字号）。"""
    text: str
    font: str
    size_pt: float
    bbox: Rect


@dataclass
class RawLine:
    """文字层里的一行。"""
    text: str
    bbox: Rect
    spans: List[Span] = field(default_factory=list)


def _strip_subset(fontname: str) -> str:
    """去掉子集前缀：'ABSEKN+FangSong' → 'FangSong'。"""
    if not fontname:
        return ""
    if "+" in fontname:
        return fontname.split("+", 1)[1]
    return fontname


# ----------------------------------------------------------------------------- 页

class PDFPage:
    """一页 PDF：渲图 / 读文字 / 读矢量线，全部走宽松许可的库。"""

    def __init__(self, doc: "PDFDocument", index: int):
        self._doc = doc
        self._index = index
        self._pl = doc._plumber_pages[index]        # pdfplumber 的页对象
        w, h = self._pl.width, self._pl.height
        self.number = index + 1
        self.width_pt = float(w)
        self.height_pt = float(h)
        self._render_cache: Dict[int, np.ndarray] = {}

    # -- 渲图 ---------------------------------------------------------------
    def render(self, dpi: int) -> np.ndarray:
        """渲染成 BGR 图像（pypdfium2，输出即 BGR）。"""
        if dpi in self._render_cache:
            return self._render_cache[dpi]
        page = self._doc._pdfium[self._index]
        bitmap = page.render(scale=dpi / 72.0)
        arr = np.asarray(bitmap.to_numpy())
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        arr = np.ascontiguousarray(arr)
        self._render_cache[dpi] = arr
        return arr

    # -- 文字层 -------------------------------------------------------------
    def has_text_layer(self, min_chars: int = 30) -> bool:
        return len(self.text().strip()) >= min_chars

    def text(self) -> str:
        """整页文字（用于逐字比对；顺序不保证与视觉一致，比对时按字符多重集）。"""
        try:
            return self._pl.extract_text() or ""
        except Exception:
            return "".join(l.text for l in self.raw_lines())

    def raw_lines(self) -> List[RawLine]:
        """文字层按行切分，保留字体/字号/坐标。

        **不能用 pdfplumber 的 `extract_text_lines()`**：它只按 y 带分组，
        会把「左栏竖排栏目名」和「右栏字段名」（同一条水平线上、但隔着栏线）
        并成一个 item，落到表格里就串格了（实测钦北模板：`领款人基本信息`
        整列变空、`领款人领款人主体资格类别` 粘成一串）。

        这里自己做两件事：
          ① 按 y 重叠聚成视觉行；
          ② 行内按**字距**再切开——间隔明显大于字宽的地方就是换栏了。
        """
        try:
            chars = [c for c in self._pl.chars
                     if c.get("text") and c.get("upright", True)]
        except Exception:
            chars = []
        if not chars:
            return []

        # ① 聚成视觉行
        buckets: List[dict] = []
        for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
            h = max(0.1, c["bottom"] - c["top"])
            target = None
            for b in buckets:
                ov = min(b["bottom"], c["bottom"]) - max(b["top"], c["top"])
                if ov > 0.5 * min(h, b["bottom"] - b["top"]):
                    target = b
                    break
            if target is None:
                buckets.append({"top": c["top"], "bottom": c["bottom"], "chars": [c]})
            else:
                target["chars"].append(c)
                target["top"] = min(target["top"], c["top"])
                target["bottom"] = max(target["bottom"], c["bottom"])

        out: List[RawLine] = []
        for b in buckets:
            row = sorted(b["chars"], key=lambda c: c["x0"])
            if not row:
                continue
            # ② 行内按字距切分（换栏处切开）
            median_w = sorted(max(1.0, c["x1"] - c["x0"]) for c in row)[len(row) // 2]
            gap_limit = max(3.0, median_w * 1.25)
            groups: List[List[dict]] = [[row[0]]]
            for prev, cur in zip(row, row[1:]):
                if cur["x0"] - prev["x1"] > gap_limit:
                    groups.append([])
                groups[-1].append(cur)

            for g in groups:
                text = "".join(c["text"] for c in g)
                if not text.strip():
                    continue
                out.append(RawLine(text=text, bbox=self._bbox(g), spans=self._spans(g)))
        out.sort(key=lambda l: (round(l.bbox.y0, 1), l.bbox.x0))
        return out

    @staticmethod
    def _bbox(chars: List[dict]) -> Rect:
        return Rect(min(c["x0"] for c in chars), min(c["top"] for c in chars),
                    max(c["x1"] for c in chars), max(c["bottom"] for c in chars))

    @staticmethod
    def _spans(chars: List[dict]) -> List[Span]:
        """把一段连续字符按 (字体, 字号) 切成 span。"""
        spans: List[Span] = []
        buf: List[dict] = []

        def flush():
            if not buf:
                return
            txt = "".join(c["text"] for c in buf)
            if txt:
                spans.append(Span(
                    text=txt,
                    font=_strip_subset(buf[0].get("fontname", "")),
                    size_pt=float(buf[0].get("size", 12.0)),
                    bbox=PDFPage._bbox(buf),
                ))
            buf.clear()

        for c in chars:
            if buf:
                same = (_strip_subset(c.get("fontname", "")) == _strip_subset(buf[0].get("fontname", ""))
                        and abs(float(c.get("size", 0)) - float(buf[0].get("size", 0))) < 0.6)
                if not same:
                    flush()
            buf.append(c)
        flush()
        return spans

    # -- 矢量线条 -----------------------------------------------------------
    def vector_lines(self) -> Tuple[List[Segment], List[Segment]]:
        """页面上的直线段（表格框线/填写栏下划线）。"""
        hs: List[Segment] = []
        vs: List[Segment] = []
        try:
            edges = self._pl.edges
        except Exception:
            edges = []
        for e in edges:
            try:
                if e.get("orientation") == "h":
                    hs.append(Segment("h", (e["top"] + e["bottom"]) / 2, e["x0"], e["x1"]))
                elif e.get("orientation") == "v":
                    vs.append(Segment("v", (e["x0"] + e["x1"]) / 2, e["top"], e["bottom"]))
            except Exception:
                continue
        return dedup_segments(hs), dedup_segments(vs)

    # -- 图片对象（用于判断扫描件） ------------------------------------------
    def image_count(self) -> int:
        try:
            return len(self._pl.images)
        except Exception:
            return 0


# ----------------------------------------------------------------------------- 文档

class PDFDocument:
    """一个 PDF 文件。用法：with PDFDocument(path) as doc: doc.pages[i].render(300)"""

    def __init__(self, path: str):
        import pdfplumber
        import pypdfium2 as pdfium

        self.path = path
        self._plumber = pdfplumber.open(path)
        self._pdfium = pdfium.PdfDocument(path)
        self._plumber_pages = self._plumber.pages
        self.page_count = len(self._plumber_pages)
        self.pages: List[PDFPage] = [PDFPage(self, i) for i in range(self.page_count)]

    # -- 兼容旧调用习惯 -----------------------------------------------------
    def __len__(self) -> int:
        return self.page_count

    def __getitem__(self, i: int) -> PDFPage:
        return self.pages[i]

    def __iter__(self):
        return iter(self.pages)

    def close(self) -> None:
        for closer in (getattr(self._plumber, "close", None),
                       getattr(self._pdfium, "close", None)):
            try:
                if closer:
                    closer()
            except Exception:
                pass

    def __enter__(self) -> "PDFDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
