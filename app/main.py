# -*- coding: utf-8 -*-
"""主窗口：拖拽 PDF → 批量转成可编辑 Word（纯离线）。"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

# 源码运行时用 vendor 里的 PySide6；打包后随包走
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.append(_VENDOR)

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSpinBox, QStatusBar, QVBoxLayout, QWidget)

from core.engines import list_engines
from core.ir import Rect
from core.pdfdoc import PDFDocument
from core.pipeline import ConvertOptions, ConvertResult, collect_pdfs
from app.worker import ConvertWorker
from app.ignore_dialog import IgnoreZoneDialog

APP_TITLE = "扫描件 PDF → 可编辑 Word（离线·免费）"
STATUS_ROLE = Qt.UserRole + 1


class DropList(QListWidget):
    """支持拖入文件/文件夹的列表。"""

    def __init__(self, on_add, parent=None):
        super().__init__(parent)
        self.on_add = on_add
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setIconSize(QSize(16, 16))

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dropEvent(self, ev):
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.on_add(paths)
            ev.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1080, 760)
        self.files: List[str] = []
        self.results: List[ConvertResult] = []
        self.worker: Optional[ConvertWorker] = None
        self.ignore_zones: List[Rect] = []
        self._build_ui()
        self._log("把扫描件 PDF（或整个文件夹）拖进上面的列表，然后点「开始转换」。")
        self._log("全程离线：文件不会离开这台电脑。")

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        head = QLabel("扫描件 PDF → 可编辑 Word")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        head.setFont(f)
        sub = QLabel("图片型扫描件、电子版 PDF 都能处理：有文字层的页面直接零误差还原，"
                     "扫描页走离线 OCR。输出 .docx 可直接改字，也可另存为 PDF。")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#555;")
        outer.addWidget(head)
        outer.addWidget(sub)

        # 文件列表
        self.list = DropList(self.add_paths)
        self.list.setMinimumHeight(180)
        outer.addWidget(self.list, 3)

        # 文件操作按钮
        row = QHBoxLayout()
        for text, slot in (("添加文件…", self.pick_files), ("添加文件夹…", self.pick_folder),
                           ("移除选中", self.remove_selected), ("清空", self.clear_files)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.lbl_count = QLabel("0 个文件")
        row.addWidget(self.lbl_count)
        outer.addLayout(row)

        # 选项
        opt = QGroupBox("转换选项")
        grid = QGridLayout(opt)
        grid.addWidget(QLabel("OCR 引擎"), 0, 0)
        self.cmb_engine = QComboBox()
        for e in list_engines():
            label = e["display_name"] + ("" if e.get("available", True) else "（未安装）")
            self.cmb_engine.addItem(label, e["key"])
            if not e.get("available", True):
                idx = self.cmb_engine.count() - 1
                self.cmb_engine.model().item(idx).setEnabled(False)
        grid.addWidget(self.cmb_engine, 0, 1)

        grid.addWidget(QLabel("扫描页渲染 DPI"), 0, 2)
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(150, 600)
        self.spin_dpi.setSingleStep(50)
        self.spin_dpi.setValue(300)
        self.spin_dpi.setToolTip("300 是准确率与速度的平衡点；字特别小可调到 400")
        grid.addWidget(self.spin_dpi, 0, 3)

        self.chk_searchable = QCheckBox("同时输出双层可搜索 PDF")
        self.chk_audit = QCheckBox("数字字段换分辨率复核（证件号/编号/账号）")
        self.chk_audit.setChecked(True)
        self.chk_force = QCheckBox("忽略文字层，强制 OCR")
        self.chk_open = QCheckBox("转换完成后打开输出文件夹")
        self.chk_open.setChecked(True)
        grid.addWidget(self.chk_searchable, 1, 0, 1, 2)
        grid.addWidget(self.chk_audit, 1, 2, 1, 2)
        grid.addWidget(self.chk_force, 2, 0, 1, 2)
        grid.addWidget(self.chk_open, 2, 2, 1, 2)

        grid.addWidget(QLabel("输出文件夹"), 3, 0)
        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText("留空 = 与源文件同目录（文件名保持不变）")
        grid.addWidget(self.edit_out, 3, 1, 1, 2)
        btn_out = QPushButton("选择…")
        btn_out.clicked.connect(self.pick_outdir)
        grid.addWidget(btn_out, 3, 3)

        self.btn_zone = QPushButton("忽略区域（水印/页眉页脚）…")
        self.btn_zone.clicked.connect(self.edit_zones)
        grid.addWidget(self.btn_zone, 4, 0, 1, 2)
        self.lbl_zone = QLabel("未设置")
        self.lbl_zone.setStyleSheet("color:#555;")
        grid.addWidget(self.lbl_zone, 4, 2, 1, 2)
        outer.addWidget(opt)

        # 执行区
        run = QHBoxLayout()
        self.btn_start = QPushButton("开始转换")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        run.addWidget(self.btn_start, 3)
        run.addWidget(self.btn_stop, 1)
        outer.addLayout(run)

        self.bar = QProgressBar()
        self.bar.setFormat("%p%")
        outer.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        outer.addWidget(self.log, 2)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------ 文件
    def add_paths(self, paths: List[str]) -> None:
        found = collect_pdfs(paths)
        added = 0
        for p in found:
            if p not in self.files:
                self.files.append(p)
                it = QListWidgetItem(os.path.basename(p))
                it.setToolTip(p)
                it.setData(STATUS_ROLE, "待转换")
                self.list.addItem(it)
                added += 1
        skipped = len(paths) - len(found)
        if added:
            self._log(f"加入 {added} 个 PDF。")
        if skipped > 0:
            self._log(f"忽略了 {skipped} 个非 PDF 项（本工具只处理 .pdf）。")
        if not found:
            self._log("没找到 PDF。若是文件夹，请确认里面有 .pdf 文件。")
        self._refresh_count()

    def pick_files(self) -> None:
        fs, _ = QFileDialog.getOpenFileNames(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if fs:
            self.add_paths(fs)

    def pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.add_paths([d])

    def pick_outdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if d:
            self.edit_out.setText(d)

    def remove_selected(self) -> None:
        for it in self.list.selectedItems():
            row = self.list.row(it)
            self.list.takeItem(row)
            if 0 <= row < len(self.files):
                self.files.pop(row)
        self._refresh_count()

    def clear_files(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.list.clear()
        self.files.clear()
        self.results.clear()
        self._refresh_count()

    def _refresh_count(self) -> None:
        self.lbl_count.setText(f"{len(self.files)} 个文件")

    # ------------------------------------------------------------------ 忽略区域
    def edit_zones(self) -> None:
        if not self.files:
            QMessageBox.information(self, "提示", "先添加至少一个 PDF，才能预览页面并框选忽略区域。")
            return
        try:
            with PDFDocument(self.files[0]) as doc:
                png = doc.pages[0].render(110)
            import cv2
            ok, buf = cv2.imencode(".png", png)
            png = buf.tobytes() if ok else b""
        except Exception as exc:
            QMessageBox.warning(self, "打不开", f"无法预览：{exc}")
            return
        dlg = IgnoreZoneDialog(png, self.ignore_zones, self)
        if dlg.exec():
            self.ignore_zones = dlg.zones()
            n = len(self.ignore_zones)
            self.lbl_zone.setText(f"已框选 {n} 块" if n else "未设置")
            self._log(f"忽略区域更新为 {n} 块。")

    # ------------------------------------------------------------------ 转换
    def _current_options(self) -> ConvertOptions:
        return ConvertOptions(
            dpi=self.spin_dpi.value(),
            engine=self.cmb_engine.currentData() or "rapidocr",
            force_ocr=self.chk_force.isChecked(),
            make_searchable_pdf=self.chk_searchable.isChecked(),
            number_audit=self.chk_audit.isChecked(),
            ignore_zones=list(self.ignore_zones),
            outdir=self.edit_out.text().strip() or None,
        )

    def start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.files:
            QMessageBox.information(self, "提示", "先拖入或添加 PDF 文件。")
            return
        self.results.clear()
        for i in range(self.list.count()):
            self.list.item(i).setData(STATUS_ROLE, "待转换")
        self.log.clear()
        self._log(f"开始转换 {len(self.files)} 个文件…")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.bar.setValue(0)

        self.worker = ConvertWorker(self.files, self._current_options(), self)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.page_progress.connect(self._on_page)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.finished_all.connect(self._on_all_done)
        self.worker.crashed.connect(self._on_crash)
        self.worker.start()

    def stop(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._log("已请求停止：当前文件转换完就停。")
            self.btn_stop.setEnabled(False)

    def _on_file_started(self, idx: int, total: int, name: str) -> None:
        self.statusBar().showMessage(f"[{idx}/{total}] {name}")
        self._log(f"[{idx}/{total}] {name}")
        if 0 <= idx - 1 < self.list.count():
            self.list.item(idx - 1).setData(STATUS_ROLE, "转换中…")

    def _on_page(self, cur: int, total: int, msg: str) -> None:
        if total > 0:
            self.bar.setValue(int(cur * 100 / total))
        self.statusBar().showMessage(f"{msg}")

    def _on_file_done(self, r: ConvertResult) -> None:
        self.results.append(r)
        row = next((i for i, f in enumerate(self.files) if os.path.abspath(f) == os.path.abspath(r.src)), -1)
        status = "完成" if r.ok else "失败"
        if r.ok:
            warns = [f for f in r.findings if f.level != "ok"]
            if warns or r.low_conf:
                status = f"完成（{len(warns)} 项待核）"
        if 0 <= row < self.list.count():
            it = self.list.item(row)
            it.setData(STATUS_ROLE, status)
            it.setText(f"{os.path.basename(r.src)}   —— {status}")
        self._log("   " + r.summary())
        for f in r.findings:
            if f.level != "ok":
                self._log("   " + str(f))
        if r.docx:
            self._log(f"   输出：{r.docx}")

    def _on_all_done(self, results: list, elapsed: float) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.bar.setValue(100)
        ok = sum(1 for r in results if r.ok)
        need = sum(1 for r in results if r.ok and any(f.level != "ok" for f in r.findings))
        self._log(f"\n全部结束：成功 {ok}/{len(results)}，其中 {need} 个有「待核」项，"
                  f"共 {elapsed:.0f}s。")
        self.statusBar().showMessage(f"完成 {ok}/{len(results)}，用时 {elapsed:.0f}s")
        if self.chk_open.isChecked() and results:
            outdir = self.edit_out.text().strip() or os.path.dirname(os.path.abspath(results[0].src))
            self._open_folder(outdir)

    def _on_crash(self, msg: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._log("❌ 发生异常：\n" + msg)
        QMessageBox.critical(self, "出错", msg[:800])

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            os.startfile(path)          # noqa: S606 - Windows 桌面工具
        except Exception:
            subprocess.Popen(["explorer", path])

    # ------------------------------------------------------------------ 杂项
    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("scan2word")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
