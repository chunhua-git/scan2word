# -*- coding: utf-8 -*-
"""引擎注册表：换引擎只改这里（或 GUI 里选）。"""
from __future__ import annotations

from typing import Dict, Type

from .base import OCREngine, OCRLine
from .rapidocr_engine import RapidOCREngine, get_shared_engine
from .ppstructure_engine import PPStructureEngine

ENGINES: Dict[str, Type[OCREngine]] = {
    "rapidocr": RapidOCREngine,
    "ppstructure": PPStructureEngine,
}

DEFAULT_ENGINE = "rapidocr"


def list_engines() -> list[dict]:
    out = []
    for key, cls in ENGINES.items():
        inst_info = {"key": key, "display_name": getattr(cls, "display_name", key)}
        if key == "ppstructure":
            inst_info["available"] = PPStructureEngine.available()
        else:
            inst_info["available"] = True
        out.append(inst_info)
    return out


def create_engine(name: str = DEFAULT_ENGINE, **kwargs) -> OCREngine:
    if name not in ENGINES:
        raise KeyError(f"未知 OCR 引擎：{name}（可选：{', '.join(ENGINES)}）")
    if name == "rapidocr":
        allowed = {k: v for k, v in kwargs.items()
                   if k in ("use_cls", "num_threads", "det_limit_side_len", "text_score")}
        return get_shared_engine(**allowed)
    return ENGINES[name](**kwargs)


__all__ = ["OCREngine", "OCRLine", "RapidOCREngine", "PPStructureEngine",
           "ENGINES", "DEFAULT_ENGINE", "list_engines", "create_engine"]
