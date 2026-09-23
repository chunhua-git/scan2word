# -*- coding: utf-8 -*-
"""PP-StructureV3 / RapidDoc 适配器（可选升级引擎）。

现状说明（如实记录，不糊弄）
--------------------------
本机没有安装 paddlepaddle / paddleocr，所以这个引擎默认不可用；装上即可切换：

    python -m pip install paddlepaddle paddleocr

它的价值在于「版面分析 + 表格结构识别（SLANet）」比纯线框检测更能吃
无框线表格、多栏混排；代价是 CPU 上单页通常 30~60s，且模型体积大。
因此默认引擎仍是 RapidOCR，本类只在用户显式选择时启用。
"""
from __future__ import annotations

from typing import List

import numpy as np

from .base import OCREngine, OCRLine


class PPStructureEngine(OCREngine):
    name = "ppstructure"
    display_name = "PP-StructureV3 / RapidDoc（可选，需自行安装）"

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._engine = None

    @staticmethod
    def available() -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:
            return False

    def _lazy(self):
        if self._engine is not None:
            return self._engine
        from paddleocr import PPStructureV3
        self._engine = PPStructureV3()
        return self._engine

    def recognize(self, image: np.ndarray) -> List[OCRLine]:
        try:
            engine = self._lazy()
        except Exception as exc:
            raise RuntimeError(
                "未安装 PP-StructureV3（paddleocr+paddlepaddle）。"
                "安装命令：python -m pip install paddlepaddle paddleocr"
            ) from exc

        out: List[OCRLine] = []
        for res in engine.predict(image):
            for item in (res.get("parsing_res_list") or []):
                content = getattr(item, "content", None) or ""
                box = getattr(item, "bbox", None)
                if not content or box is None:
                    continue
                x0, y0, x1, y1 = [float(v) for v in box]
                out.append(OCRLine(
                    text=str(content),
                    box=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                    conf=1.0,
                ))
        return out
