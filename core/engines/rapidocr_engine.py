# -*- coding: utf-8 -*-
"""RapidOCR 离线引擎（默认引擎）。

PP-OCRv4 det/rec/cls 三个 onnx 模型随 rapidocr-onnxruntime 包内置，
首次运行完全不需要联网，符合「材料一个字节都不上传」的硬要求。
"""
from __future__ import annotations

import os
import threading
from typing import List

import numpy as np

from .base import OCREngine, OCRLine

_LOCK = threading.Lock()
_ENGINE = None


class RapidOCREngine(OCREngine):
    name = "rapidocr"
    display_name = "RapidOCR（PP-OCRv4，离线）"

    def __init__(self, *, use_cls: bool = True, num_threads: int = 0,
                 det_limit_side_len: int = 1280, text_score: float = 0.5):
        from rapidocr_onnxruntime import RapidOCR

        # det_limit_side_len=1280 是实测最优：本页 300dpi 扫描件上，
        # 1280 最快（4.5s）且与 736 结果逐字一致；1600 更慢还多认出一个错字。
        kwargs = dict(use_cls=use_cls, text_score=text_score,
                      det_limit_side_len=det_limit_side_len)
        # 线程数：0 表示交给 onnxruntime 自己决定；显式指定可避免多进程时争抢
        if num_threads:
            kwargs["intra_op_num_threads"] = num_threads
        self._engine = RapidOCR(**kwargs)
        self.use_cls = use_cls

    def recognize(self, image: np.ndarray) -> List[OCRLine]:
        with _LOCK:                     # onnxruntime session 非线程安全，串行调用
            result, _ = self._engine(image)
        out: List[OCRLine] = []
        for box, text, conf in (result or []):
            pts = [(float(p[0]), float(p[1])) for p in box]
            out.append(OCRLine(text=str(text), box=pts, conf=float(conf)))
        return out

    def warmup(self) -> None:
        dummy = np.full((64, 320, 3), 255, dtype=np.uint8)
        try:
            self.recognize(dummy)
        except Exception:
            pass


def get_shared_engine(**kwargs) -> RapidOCREngine:
    """进程内共享一个引擎实例（模型加载约 1s，别每页都建）。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCREngine(**kwargs)
    return _ENGINE


def models_dir() -> str:
    import rapidocr_onnxruntime
    return os.path.join(os.path.dirname(rapidocr_onnxruntime.__file__), "models")
