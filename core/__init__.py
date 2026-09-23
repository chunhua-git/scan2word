# -*- coding: utf-8 -*-
"""scan2word 核心包：扫描件 PDF → 可编辑 Word（纯离线、许可证干净）。

依赖全部为宽松许可证（无 AGPL）。源码方式运行时，第三方库放在项目根的
`vendor/` 目录里（PyInstaller 打的绿色包会直接把它们收进包内）。
"""
from __future__ import annotations

import os
import sys

__version__ = "0.2.0"

# 源码运行时把 vendor/ 挂到 sys.path（打包后这些模块已在包内，目录不存在则是空操作）
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.append(_VENDOR)
