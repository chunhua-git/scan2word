# -*- coding: utf-8 -*-
"""后台转换线程：界面永不卡死，支持批量排队与取消。"""
from __future__ import annotations

import os
import time
import traceback
from dataclasses import replace
from typing import List

from PySide6.QtCore import QThread, Signal

from core.pipeline import ConvertOptions, ConvertResult, convert_pdf


class ConvertWorker(QThread):
    """把一个文件列表依次转换；每页进度、每个文件结果都通过信号回主线程。"""

    page_progress = Signal(int, int, str)      # 当前页, 总页数, 说明
    file_started = Signal(int, int, str)       # 第几个, 总数, 文件名
    file_done = Signal(object)                 # ConvertResult
    finished_all = Signal(list, float)         # [ConvertResult], 总耗时
    crashed = Signal(str)

    def __init__(self, files: List[str], opts: ConvertOptions, parent=None):
        super().__init__(parent)
        self.files = list(files)
        self.opts = opts
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    @property
    def cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:      # pragma: no cover - 线程体
        t0 = time.time()
        results: List[ConvertResult] = []
        try:
            for i, path in enumerate(self.files):
                if self._cancel:
                    break
                self.file_started.emit(i + 1, len(self.files), os.path.basename(path))
                opts = replace(self.opts, progress=self._on_page)
                r = convert_pdf(path, opts)
                results.append(r)
                self.file_done.emit(r)
            self.finished_all.emit(results, time.time() - t0)
        except Exception as exc:
            self.crashed.emit(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    def _on_page(self, cur: int, total: int, msg: str) -> None:
        self.page_progress.emit(cur, total, msg)
