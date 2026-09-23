# -*- coding: utf-8 -*-
"""OCR 路：图片型扫描件 → IR。

流程：渲染(300dpi) → 纠偏 → OCR 文本行 → 光栅线检测 → 表格网格重建
      → 勾选框补字 → 印章裁图 → 段落聚合
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .engines import create_engine, OCREngine, OCRLine
from .grid import Item, tables_from_lines
from .ir import Rect, TextRun, Page, ImageBlock
from .lines import detect_raster_lines
from .pdfdoc import PDFPage
from .textlayer import group_leftover, body_font_size

# 勾选框被 OCR 误读成这些字/符号时，用检测到的方框覆盖它
BOX_LIKE = set("口□ロ〇○O0oО◻◊")

# 常用字号档位：OCR 只能估高，吸附到办公文档常见字号更像原件
COMMON_SIZES = [9.0, 10.5, 12.0, 14.0, 15.0, 16.0, 18.0, 22.0]


@dataclass
class OCRConfig:
    dpi: int = 300
    deskew: bool = True
    detect_checkboxes: bool = True
    detect_stamps: bool = True
    detect_blanks: bool = True
    ignore_zones: List[Rect] = field(default_factory=list)   # 归一化坐标(0~1)
    engine: str = "rapidocr"
    min_len_ratio: float = 0.055


# ----------------------------------------------------------------------------- 图像

def render_page(page: PDFPage, dpi: int) -> np.ndarray:
    """页面 → BGR 图像（pypdfium2）。"""
    return page.render(dpi)


def estimate_skew(gray: np.ndarray, max_deg: float = 5.0) -> float:
    """用长横线估页面倾斜角（度）；正值表示需要逆时针回正。"""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=180,
                            minLineLength=max(200, gray.shape[1] // 4), maxLineGap=12)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):   # OpenCV 4/5 返回形状不同
        dx, dy = int(x2) - int(x1), int(y2) - int(y1)
        if dx == 0 or abs(dy) > abs(dx) * 0.25:
            continue
        a = float(np.degrees(np.arctan2(dy, dx)))
        if abs(a) <= max_deg:
            angles.append(a)
    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew_image(bgr: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.15:
        return bgr
    h, w = bgr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(bgr, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


# ----------------------------------------------------------------------------- 勾选框 / 印章

def detect_checkboxes(gray: np.ndarray, scale: float, ocr_lines: Sequence[OCRLine], *,
                      min_pt: float = 4.5, max_pt: float = 22.0) -> List[Tuple[Rect, bool]]:
    """找空勾选框。

    假阳性（身份证底纹、网格、汉字里的「口/日」）会毁掉整篇文本，所以判据很紧：
      ① 边长 4.5~22pt（真勾选框就这么大）；
      ② 内区几乎空白（或只有很少笔画的勾），太满说明是字或底纹；
      ③ 附近不能有一堆同样大小的方框（底纹/网格的特征）；
      ④ 已被 OCR 认成文字的区域不算。
    """
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    contours, _ = cv2.findContours(bw, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    # 被误读成「口/O/0」的 OCR 行不参与占位判断：
    # 整行就是方框的直接跳过；行首是方框的，把首字那一小块从占位框里挖掉。
    occupied = []
    for l in ocr_lines:
        t = l.text.strip()
        if not t:
            continue
        bx0, by0, bx1, by1 = l.x0, l.y0, l.x1, l.y1
        if set(t) <= BOX_LIKE:
            continue
        if t[0] in BOX_LIKE:
            w = min((by1 - by0) * 1.25, (bx1 - bx0) * 0.6)
            bx0 = bx0 + w
            if bx0 >= bx1:
                continue
        occupied.append((bx0, by0, bx1, by1))

    raw: List[Tuple[Rect, bool]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        w_pt, h_pt = w * scale, h * scale
        if not (min_pt <= w_pt <= max_pt and min_pt <= h_pt <= max_pt):
            continue
        if not (0.70 <= w / max(1, h) <= 1.45):
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(c, 0.05 * peri, True)
        if len(approx) != 4:
            continue
        if any(not (x + w < bx0 or x > bx1 or y + h < by0 or y > by1)
               for bx0, by0, bx1, by1 in occupied):
            continue
        t = max(2, int(min(w, h) * 0.13))
        inner = bw[y + t:y + h - t, x + t:x + w - t]
        if inner.size == 0:
            continue
        density = float(inner.mean()) / 255.0
        if density > 0.30:                       # 内区太满 → 是字/底纹
            continue
        raw.append((Rect(x * scale, y * scale, (x + w) * scale, (y + h) * scale),
                    density > 0.10))

    # 隔离度：真勾选框是孤立的；身份证底纹/网格会成片出现
    keep: List[Tuple[Rect, bool]] = []
    for i, (r, ck) in enumerate(raw):
        size = max(r.width, r.height)
        neighbours = sum(
            1 for j, (o, _) in enumerate(raw)
            if j != i and abs(o.cx - r.cx) < size * 3.0 and abs(o.cy - r.cy) < size * 3.0
        )
        if neighbours > 4:
            continue
        keep.append((r, ck))

    # 去重（内外轮廓会重复报）
    dedup: List[Tuple[Rect, bool]] = []
    for r, ck in sorted(keep, key=lambda t: -t[0].width * t[0].height):
        if any(abs(r.cx - d.cx) < 3 and abs(r.cy - d.cy) < 3 for d, _ in dedup):
            continue
        dedup.append((r, ck))
    return dedup


def detect_stamps(bgr: np.ndarray, scale: float, *, min_side_pt: float = 60.0,
                  max_stamps: int = 6) -> List[Tuple[Rect, bytes]]:
    """红色印章检测：按 HSV 抓红色块，裁出 PNG（保留原色）。

    真公章直径通常 4cm 上下，所以要求成块区域至少 2.1cm 见方、红色占比够高，
    避免把身份证国徽、红色印刷字当成印章一路往正文里插。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 55, 55), (12, 255, 255)) | \
        cv2.inRange(hsv, (155, 55, 55), (180, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out: List[Tuple[Rect, bytes]] = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 2500 or w < 20 or h < 20:
            continue
        rect = Rect(x * scale, y * scale, (x + w) * scale, (y + h) * scale)
        if min(rect.width, rect.height) < min_side_pt:
            continue
        if not (0.5 <= rect.width / max(1.0, rect.height) <= 2.0):
            continue
        if area / float(max(1, w * h)) < 0.10:      # 红色像素太稀 → 不是印章
            continue
        pad = int(min(w, h) * 0.06) + 4
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(bgr.shape[1], x + w + pad), min(bgr.shape[0], y + h + pad)
        crop = bgr[y0:y1, x0:x1]
        ok, buf = cv2.imencode(".png", crop)
        if not ok:
            continue
        out.append((rect, buf.tobytes()))
    out.sort(key=lambda t: -t[0].width * t[0].height)
    return out[:max_stamps]


# ----------------------------------------------------------------------------- 工具

def _snap_size(pt: float) -> float:
    return min(COMMON_SIZES, key=lambda s: abs(s - pt))


def _in_ignore_zone(r: Rect, zones: Sequence[Rect], page_w: float, page_h: float) -> bool:
    for z in zones:
        zz = Rect(z.x0 * page_w, z.y0 * page_h, z.x1 * page_w, z.y1 * page_h)
        if zz.intersection_ratio(r) > 0.5:
            return True
    return False


def lines_to_items(lines: Sequence[OCRLine], scale: float, page_w: float, page_h: float,
                   ignore: Sequence[Rect] = ()) -> List[Item]:
    """OCR 行 → Item（像素坐标换算成 point，字号由行高估计）。"""
    items: List[Item] = []
    for ln in lines:
        txt = ln.text.strip()
        if not txt:
            continue
        r = Rect(ln.x0 * scale, ln.y0 * scale, ln.x1 * scale, ln.y1 * scale)
        if ignore and _in_ignore_zone(r, ignore, page_w, page_h):
            continue
        size = _snap_size(max(7.0, min(30.0, r.height * 0.85)))
        items.append(Item(
            text=txt, bbox=r, conf=ln.conf, source="ocr",
            runs=[TextRun(text=txt, size_pt=size, conf=ln.conf, source="ocr")],
        ))
    return items


def attach_checkboxes(items: List[Item], boxes: Sequence[Tuple[Rect, bool]]
                      ) -> Tuple[List[Item], int]:
    """把勾选框拼到同一行的文字前面（□申请执行人 / ☑被执行人）。

    **必须**找到同一行左侧的文字宿主才采用：孤零零一个方框多半是底纹误检，
    宁可不补，也不能往正文里塞垃圾字符。返回 (items, 采用个数)。
    """
    extra: List[Item] = []
    used = 0
    for rect, checked in boxes:
        glyph = "☑" if checked else "□"
        host = None
        for it in items:
            same_row = not (rect.cy < it.bbox.y0 - 3 or rect.cy > it.bbox.y1 + 3)
            gap = it.bbox.x0 - rect.x1
            if same_row and -6 <= gap <= 26:          # 紧贴在文字左边
                if host is None or gap < (host.bbox.x0 - rect.x1):
                    host = it
        if host is None:
            continue                                   # 没宿主 → 丢弃
        used += 1
        head = host.text[:2]
        if head and head[0] in BOX_LIKE and host.bbox.x0 <= rect.x0 + 6:
            host.text = glyph + host.text[1:]          # 覆盖误读字符
            if host.runs:
                host.runs[0].text = glyph + host.runs[0].text[1:]
        else:
            host.text = glyph + host.text
            if host.runs:
                host.runs[0].text = glyph + host.runs[0].text
        host.bbox = host.bbox.union(rect)
    return items + extra, used


# ----------------------------------------------------------------------------- 空白填写栏

def blank_fill_segments(hs: Sequence[Segment], vs: Sequence[Segment]
                        ) -> List[Segment]:
    """挑出「不属于任何表格」的短横线——它们多半是填写栏的下划线。"""
    out: List[Segment] = []
    for s in hs:
        if not (12.0 <= s.length <= 260.0):
            continue
        # 与竖线相交 → 是表格框线，不是填写栏
        crosses = any(abs(v.pos - x) <= 2.5 for v in vs for x in (s.lo, s.hi)) or \
            any(v.lo - 2.5 <= s.pos <= v.hi + 2.5 and s.lo - 2.5 <= v.pos <= s.hi + 2.5 for v in vs)
        if crosses:
            continue
        out.append(s)
    return out


def insert_blank_fills(items: List[Item], hs: Sequence[Segment], vs: Sequence[Segment]
                       ) -> List[Item]:
    """把填写栏下划线还原成下划线字符，插回原文字位置。

    这样「（20＿＿）桂0802民初＿＿号」这类空白栏在 Word 里还是空白栏，
    而不是被 OCR 悄悄吃掉。
    """
    blanks = blank_fill_segments(hs, vs)
    if not blanks:
        return items
    extra: List[Item] = []
    for seg in blanks:
        host = None
        for it in items:
            if it.bbox.y0 - 2 <= seg.pos <= it.bbox.y1 + 2:
                if host is None or abs(it.bbox.cy - seg.pos) < abs(host.bbox.cy - seg.pos):
                    host = it
        if host is None:
            continue
        cw = max(4.0, host.bbox.width / max(1, len(host.text)))
        n = max(1, int(round(seg.length / cw)))
        marks = "＿" * n
        inside = host.bbox.x0 + cw * 0.4 <= seg.lo and seg.hi <= host.bbox.x1 - cw * 0.2
        if inside:
            # 落在文字中间：按 x 位置插进对应字符之间
            pos = int(round((seg.lo - host.bbox.x0) / cw))
            pos = max(0, min(len(host.text), pos))
            host.text = host.text[:pos] + marks + host.text[pos:]
            if host.runs:
                host.runs[0].text = host.text
                host.runs = [host.runs[0]]
        elif seg.lo >= host.bbox.x1 - cw:
            host.text = host.text + marks
            if host.runs:
                host.runs[0].text = host.text
                host.runs = [host.runs[0]]
        elif seg.hi <= host.bbox.x0 + cw:
            host.text = marks + host.text
            if host.runs:
                host.runs[0].text = host.text
                host.runs = [host.runs[0]]
        else:
            extra.append(Item(
                text=marks, bbox=Rect(seg.lo, seg.pos - 3, seg.hi, seg.pos + 3),
                conf=1.0, source="ocr",
                runs=[TextRun(text=marks, size_pt=_snap_size(max(8.0, host.bbox.height * 0.85)),
                              source="ocr")],
            ))
    return items + extra


# ----------------------------------------------------------------------------- 主入口

def build_page(page: PDFPage, number: int, cfg: Optional[OCRConfig] = None,
               engine: Optional[OCREngine] = None) -> Page:
    """OCR 路构建一页 IR。"""
    cfg = cfg or OCRConfig()
    engine = engine or create_engine(cfg.engine)
    scale = 72.0 / cfg.dpi

    bgr = render_page(page, cfg.dpi)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    page_w, page_h = page.width_pt, page.height_pt

    out = Page(number=number, width_pt=page_w, height_pt=page_h,
               route="ocr", dpi=cfg.dpi)
    if cfg.deskew:
        ang = estimate_skew(gray)
        if abs(ang) >= 0.15:
            bgr = deskew_image(bgr, ang)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            out.warn.append(f"检测到倾斜 {ang:.2f}°，已自动纠偏")

    ocr_lines = engine.recognize(bgr)
    items = lines_to_items(ocr_lines, scale, page_w, page_h, cfg.ignore_zones)

    if cfg.detect_checkboxes:
        boxes = detect_checkboxes(gray, scale, ocr_lines)
        if boxes:
            items, used = attach_checkboxes(items, boxes)
            if used:
                out.warn.append(f"补出 {used} 个勾选框（候选 {len(boxes)} 个，其余无文字宿主已丢弃）")

    hs, vs = detect_raster_lines(gray, scale, min_len_ratio=cfg.min_len_ratio)
    if cfg.detect_blanks:
        items = insert_blank_fills(items, hs, vs)
    res = tables_from_lines(hs, vs, items)

    stamps: List[Tuple[Rect, bytes]] = []
    if cfg.detect_stamps:
        stamps = detect_stamps(bgr, scale)

    body = body_font_size(items)
    blocks = list(res.tables) + group_leftover(res.leftover, Rect(0, 0, page_w, page_h),
                                               body_size=body)

    # 印章归位：落在格子里就放进格子，否则作为独立图片块
    for rect, png in stamps:
        img = ImageBlock(png=png, bbox=rect, role="stamp", label="印章")
        placed = False
        for t in res.tables:
            for c in t.cells():
                if c.bbox.intersection_ratio(rect) > 0.25:
                    c.images.append(img)
                    c.notes.append("内含印章图片（保留原位置）")
                    placed = True
                    break
            if placed:
                break
        if not placed:
            blocks.append(img)

    blocks.sort(key=lambda b: (round(b.bbox.y0, 1), b.bbox.x0))
    out.blocks = blocks

    try:
        out.render_png = _encode_preview(bgr)
    except Exception:
        pass
    return out


def _encode_preview(bgr: np.ndarray, max_w: int = 1000) -> bytes:
    h, w = bgr.shape[:2]
    if w > max_w:
        s = max_w / w
        bgr = cv2.resize(bgr, (max_w, int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", bgr)
    return buf.tobytes() if ok else b""
