# -*- coding: utf-8 -*-
"""线检测：矢量路（PDF 里的真实线条）与光栅路（扫描图上的线）产出同一种 Segment。

表格网格重建（grid.py）只吃 Segment，所以「电子版 PDF」和「扫描件」共用同一套结构还原算法。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Sequence

import numpy as np


@dataclass
class Segment:
    """一条水平或垂直的直线段（PDF point 坐标，原点左上）。"""
    orient: str          # 'h' 水平 | 'v' 垂直
    pos: float           # h: y 值；v: x 值
    lo: float            # h: x0；v: y0
    hi: float            # h: x1；v: y1

    @property
    def length(self) -> float:
        return self.hi - self.lo

    def __repr__(self) -> str:  # pragma: no cover
        return f"Segment({self.orient}, pos={self.pos:.1f}, {self.lo:.1f}..{self.hi:.1f})"


# ----------------------------------------------------------------------------- 矢量路
# 说明：矢量线条的读取已移到 core/pdfdoc.py（走 pdfplumber，许可证干净）。
# 这里只保留「线段 → 表格」的几何算法，矢量路与光栅路共用。



def detect_raster_lines(gray: np.ndarray, scale: float, *,
                        min_len_ratio: float = 0.055,
                        max_thick_px: int = 8,
                        min_len_px: int = 60) -> Tuple[List[Segment], List[Segment]]:
    """在扫描图上检测长直线。scale = point/px，用于换算回 PDF 坐标。

    表格边框在扫描件里通常是 1~3px 的暗线；用形态学开运算按方向抽出来。
    """
    import cv2

    h_img, w_img = gray.shape[:2]
    # 二值化：暗=255（线与字）
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    min_len = max(min_len_px, int(min(w_img, h_img) * min_len_ratio))

    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk)
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len))
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk)

    def segments_from(mask, orient):
        out: List[Segment] = []
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if orient == "h":
                if h > max_thick_px or w < min_len:
                    continue
                out.append(Segment("h", (y + h / 2) * scale, x * scale, (x + w) * scale))
            else:
                if w > max_thick_px or h < min_len:
                    continue
                out.append(Segment("v", (x + w / 2) * scale, y * scale, (y + h) * scale))
        return out

    return dedup_segments(segments_from(hor, "h")), dedup_segments(segments_from(ver, "v"))


# ----------------------------------------------------------------------------- 公用

def dedup_segments(segs: Sequence[Segment], pos_tol: float = 1.5, gap_tol: float = 6.0) -> List[Segment]:
    """合并同位置、相邻/重叠的碎线段（扫描线检测常把一条线断成几截）。"""
    if not segs:
        return []
    out: List[Segment] = []
    for orient in ("h", "v"):
        group = sorted([s for s in segs if s.orient == orient], key=lambda s: (s.pos, s.lo))
        merged: List[Segment] = []
        for s in group:
            placed = False
            for m in merged:
                if abs(m.pos - s.pos) <= pos_tol and s.lo <= m.hi + gap_tol and s.hi >= m.lo - gap_tol:
                    m.pos = (m.pos + s.pos) / 2
                    m.lo = min(m.lo, s.lo)
                    m.hi = max(m.hi, s.hi)
                    placed = True
                    break
            if not placed:
                merged.append(Segment(orient, s.pos, s.lo, s.hi))
        # 合并后可能又有新的可并项，迭代一遍
        changed = True
        while changed:
            changed = False
            keep: List[Segment] = []
            for s in merged:
                hit = None
                for m in keep:
                    if abs(m.pos - s.pos) <= pos_tol and s.lo <= m.hi + gap_tol and s.hi >= m.lo - gap_tol:
                        hit = m
                        break
                if hit is None:
                    keep.append(s)
                else:
                    hit.pos = (hit.pos + s.pos) / 2
                    hit.lo = min(hit.lo, s.lo)
                    hit.hi = max(hit.hi, s.hi)
                    changed = True
            merged = keep
        out.extend(merged)
    return out


def grid_components(hs: List[Segment], vs: List[Segment], *,
                    min_segments: int = 4, min_area: float = 4000.0,
                    tol: float = 3.0) -> List[Tuple[List[Segment], List[Segment]]]:
    """把互相连接的线段分成若干「表格」，滤掉装饰性单线（标题下划线等）。"""
    n_h, n_v = len(hs), len(vs)
    total = n_h + n_v
    if total == 0:
        return []

    parent = list(range(total))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def seg(i) -> Segment:
        return hs[i] if i < n_h else vs[i - n_h]

    # 相交判定：h 的 x 区间覆盖 v 的 x，且 v 的 y 区间覆盖 h 的 y
    for i in range(n_h):
        a = hs[i]
        for j in range(n_v):
            b = vs[j]
            if a.lo - tol <= b.pos <= a.hi + tol and b.lo - tol <= a.pos <= b.hi + tol:
                union(i, n_h + j)

    groups: dict[int, List[int]] = {}
    for i in range(total):
        groups.setdefault(find(i), []).append(i)

    out = []
    for members in groups.values():
        gh = [hs[i] for i in members if i < n_h]
        gv = [vs[i - n_h] for i in members if i >= n_h]
        if len(members) < min_segments or not gh or not gv:
            continue
        x0 = min(s.lo for s in gv)
        x1 = max(s.hi for s in gv)
        y0 = min(s.lo for s in gh)
        y1 = max(s.hi for s in gh)
        if (x1 - x0) * (y1 - y0) < min_area:
            continue
        out.append((gh, gv))
    out.sort(key=lambda p: (min(s.pos for s in p[0]), min(s.pos for s in p[1])))
    return out


def cluster_positions(values: Sequence[float], tol: float) -> List[float]:
    """把近似相等的坐标聚成一类，返回代表值（升序）。"""
    vals = sorted(values)
    groups: List[List[float]] = []
    cur = [vals[0]]
    for v in vals[1:]:
        if v - cur[-1] <= tol:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    return [sum(g) / len(g) for g in groups]


def pt_to_px(pt: float, dpi: int) -> float:
    return pt * dpi / 72.0


def px_to_pt(px: float, dpi: int) -> float:
    return px * 72.0 / dpi
