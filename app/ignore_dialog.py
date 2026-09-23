# -*- coding: utf-8 -*-
"""忽略区域编辑器：在页面图上框选水印 / 页眉页脚区域，转换时自动排除。"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QImage
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout)

from core.ir import Rect


class ZoneCanvas(QLabel):
    """显示一页图片，鼠标拖拽画框；内部按像素保存，对外换算成 0~1 归一化矩形。"""

    zones_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(520, 640)
        self.setStyleSheet("background:#2b2b2b;")
        self._pix: Optional[QPixmap] = None
        self._zones: List[QRect] = []
        self._drag_start: Optional[QPoint] = None
        self._drag_cur: Optional[QPoint] = None
        self._scale = 1.0
        self._offset = QPoint(0, 0)
        self._image_size = (0, 0)

    # -- 数据 ---------------------------------------------------------------
    def set_image(self, png: bytes) -> None:
        img = QImage.fromData(png, "PNG")
        self._image_size = (img.width(), img.height())
        self._pix = QPixmap.fromImage(img)
        self._zones.clear()
        self._fit()
        self.update()
        self.zones_changed.emit()

    def _fit(self) -> None:
        if not self._pix:
            return
        s = min(self.width() / self._pix.width(), self.height() / self._pix.height())
        self._scale = max(0.05, s)
        self._offset = QPoint(int((self.width() - self._pix.width() * s) / 2),
                              int((self.height() - self._pix.height() * s) / 2))

    def normalized_zones(self) -> List[Rect]:
        if not self._image_size[0]:
            return []
        iw, ih = self._image_size
        return [Rect(z.left() / iw, z.top() / ih, z.right() / iw, z.bottom() / ih)
                for z in self._zones]

    def clear_zones(self) -> None:
        self._zones.clear()
        self.update()
        self.zones_changed.emit()

    def undo_zone(self) -> None:
        if self._zones:
            self._zones.pop()
            self.update()
            self.zones_changed.emit()

    # -- 绘制与交互 ---------------------------------------------------------
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()

    def _to_image_rect(self, a: QPoint, b: QPoint) -> QRect:
        r = QRect(a, b).normalized()
        x0 = int((r.left() - self._offset.x()) / self._scale)
        y0 = int((r.top() - self._offset.y()) / self._scale)
        x1 = int((r.right() - self._offset.x()) / self._scale)
        y1 = int((r.bottom() - self._offset.y()) / self._scale)
        iw, ih = self._image_size
        return QRect(max(0, x0), max(0, y0), min(iw, x1) - max(0, x0),
                     min(ih, y1) - max(0, y0))

    def _to_widget_rect(self, r: QRect) -> QRect:
        return QRect(int(r.left() * self._scale) + self._offset.x(),
                     int(r.top() * self._scale) + self._offset.y(),
                     int(r.width() * self._scale), int(r.height() * self._scale))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._pix:
            self._drag_start = ev.position().toPoint()
            self._drag_cur = self._drag_start
            self.update()

    def mouseMoveEvent(self, ev):
        if self._drag_start is not None:
            self._drag_cur = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag_start is not None and self._drag_cur is not None:
            r = self._to_image_rect(self._drag_start, self._drag_cur)
            if r.width() > 8 and r.height() > 8:
                self._zones.append(r)
                self.zones_changed.emit()
        self._drag_start = self._drag_cur = None
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        if self._pix:
            p.drawPixmap(self._offset, self._pix.scaled(
                int(self._pix.width() * self._scale), int(self._pix.height() * self._scale),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        pen = QPen(QColor(255, 90, 90), 2)
        p.setPen(pen)
        p.setBrush(QColor(255, 90, 90, 60))
        for r in self._zones:
            p.drawRect(self._to_widget_rect(r))
        if self._drag_start and self._drag_cur:
            pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRect(self._drag_start, self._drag_cur).normalized())


class IgnoreZoneDialog(QDialog):
    """框选忽略区域。"""

    def __init__(self, png: bytes, zones: List[Rect], parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑忽略区域（水印 / 页眉页脚 / 无关区域）")
        self.resize(760, 820)

        self.canvas = ZoneCanvas(self)
        if png:
            self.canvas.set_image(png)
        for z in zones:
            iw, ih = self.canvas._image_size
            if iw:
                self.canvas._zones.append(QRect(int(z.x0 * iw), int(z.y0 * ih),
                                                int((z.x1 - z.x0) * iw), int((z.y1 - z.y0) * ih)))

        tip = QLabel("在页面上按住鼠标拖拽即可框选要忽略的区域（例如水印、页码、页眉页脚）。\n"
                     "转换时落在这些区域里的文字会被整块跳过。选错了可以撤销或清空。")
        tip.setWordWrap(True)

        btn_undo = QPushButton("撤销上一个")
        btn_clear = QPushButton("清空")
        btn_ok = QPushButton("确定")
        btn_cancel = QPushButton("取消")
        btn_undo.clicked.connect(self.canvas.undo_zone)
        btn_clear.clicked.connect(self.canvas.clear_zones)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(btn_undo)
        row.addWidget(btn_clear)
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(row)

        self.canvas.zones_changed.connect(self._refresh_title)
        self._refresh_title()

    def _refresh_title(self) -> None:
        self.setWindowTitle(f"编辑忽略区域 —— 已框选 {len(self.canvas._zones)} 块")

    def zones(self) -> List[Rect]:
        return self.canvas.normalized_zones()
