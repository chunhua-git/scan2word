# -*- coding: utf-8 -*-
"""打包后处理：附带说明/许可证/示例，并做一次可复核的产物统计。

单独用 Python 做这一步，是因为 Windows PowerShell 5.1 会把无 BOM 的 .ps1
按 ANSI 读，脚本里的中文文件名会变乱码——中文路径统一交给 Python 处理。
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "scan2word")
sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    if not os.path.isdir(DIST):
        print(f"❌ 没找到打包产物：{DIST}")
        return 1

    copied = []
    for src, dst_name in ((os.path.join(ROOT, "使用说明.md"), "使用说明.md"),
                          (os.path.join(ROOT, "packaging", "许可证清单.txt"), "许可证清单.txt")):
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DIST, dst_name))
            copied.append(dst_name)

    demo = os.path.join(DIST, "示例输出")
    os.makedirs(demo, exist_ok=True)

    # 给打包脚本准备一个纯 ASCII 名字的样本，避免 .ps1 里出现中文（PS5.1 会乱码）
    probe = os.path.join(ROOT, "probe_vector.pdf")
    try:
        samples = os.path.join(ROOT, "samples")
        for fn in sorted(os.listdir(samples)):
            if fn.lower().endswith(".pdf") and "青秀" in fn:
                shutil.copy2(os.path.join(samples, fn), probe)
                break
    except Exception:
        pass

    files = [os.path.join(r, f) for r, _d, fs in os.walk(DIST) for f in fs]
    total = sum(os.path.getsize(f) for f in files) / 1024 / 1024
    print("附带文件：" + ("、".join(copied) if copied else "（无）"))
    print(f"目录版：{len(files)} 个文件，合计 {total:.0f} MB")
    print(f"入口：{os.path.join(DIST, 'scan2word.exe')}")
    exe = os.path.join(DIST, "scan2word.exe")
    print(f"  scan2word.exe: {'✅ 存在' if os.path.exists(exe) else '❌ 缺失'}"
          + (f"（{os.path.getsize(exe)/1024/1024:.1f} MB）" if os.path.exists(exe) else ""))
    # OCR 模型必须进包，否则离线识别会失效
    models = [f for f in files if f.endswith(".onnx")]
    print(f"  随包 OCR 模型：{len(models)} 个 onnx"
          + (" ✅" if len(models) >= 3 else " ❌ 缺失，离线识别会失败"))

    # ---- 压成一个 zip：发给别人只需要发这一个文件 ----
    zip_path = os.path.join(ROOT, "dist", "scan2word绿色版.zip")
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in files:
                z.write(f, os.path.relpath(f, os.path.dirname(DIST)))
        zsize = os.path.getsize(zip_path) / 1024 / 1024
        print(f"压缩包：{zip_path}（{zsize:.0f} MB，发这一个文件即可）")
    except Exception as exc:
        print(f"⚠️ 压缩失败：{exc}")

    return 0 if os.path.exists(exe) and len(models) >= 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
