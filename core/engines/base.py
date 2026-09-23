# -*- coding: utf-8 -*-
"""OCR 引擎接口：壳只依赖这里的抽象，换引擎不动业务代码。

- RapidOCREngine   ：默认，纯离线，PP-OCRv4 模型随包内置（已实测可用）
- PPStructureEngine：可选，接 PP-StructureV3 / RapidDoc 做更强的版面与表格还原
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np


@dataclass
class OCRLine:
    """一行识别结果（像素坐标，原点左上）。"""
    text: str
    box: List[Tuple[float, float]]          # 四点框
    conf: float = 1.0
    angle: float = 0.0

    @property
    def x0(self) -> float:
        return min(p[0] for p in self.box)

    @property
    def x1(self) -> float:
        return max(p[0] for p in self.box)

    @property
    def y0(self) -> float:
        return min(p[1] for p in self.box)

    @property
    def y1(self) -> float:
        return max(p[1] for p in self.box)

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class OCREngine(ABC):
    """OCR 引擎抽象基类。"""

    name: str = "base"
    display_name: str = "未命名引擎"

    @abstractmethod
    def recognize(self, image: np.ndarray) -> List[OCRLine]:
        """输入 BGR 图像，输出文本行。"""

    def warmup(self) -> None:                      # pragma: no cover
        """预热（加载模型），可选。"""

    def close(self) -> None:                       # pragma: no cover
        pass

    def info(self) -> dict:
        return {"name": self.name, "display_name": self.display_name}
