# -*- coding: utf-8 -*-
"""离线证明：把网络彻底掐死，转换仍必须跑通。

「材料一个字节都不上传」这句话不能靠嘴说。这个测试在**进程级别把 socket 打死**
（连 DNS 都不让解析），再跑一次真实转换：
  - 转换成功  → 说明整条链路确实不碰网络
  - 抛异常    → 说明某处在偷偷联网，必须揪出来

同时校验 OCR 模型在本地（离线识别的基础），并静态扫描源码里有没有联网调用。
"""
from __future__ import annotations

import os
import re
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.append(_VENDOR)
sys.stdout.reconfigure(encoding="utf-8")


class NetworkBlocked(RuntimeError):
    pass


def _block(*_a, **_k):
    raise NetworkBlocked("已断网（离线测试）：任何网络访问都会在这里失败")


def main() -> int:
    ok = True

    # ---- 1) 静态扫描：源码里不应出现联网调用 ----
    patterns = [
        r"\brequests\.", r"\burllib\.request", r"\burlopen\b", r"\bhttpx\b",
        r"\bsocket\.socket\b", r"\bhttp\.client\b", r"\bftplib\b",
        r"\btelemetry\b", r"\bposthog\b", r"\bsentry_sdk\b",
    ]
    hits = []
    for sub in ("core", "app", "tools", "tests"):
        base = os.path.join(ROOT, sub)
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                for p in patterns:
                    for m in re.finditer(p, text):
                        line_no = text[:m.start()].count("\n") + 1
                        hits.append(f"{os.path.relpath(path, ROOT)}:{line_no}: {p}")
    # fetch_wheels.py 是开发期下载依赖用的；本文件自身要用 socket 来"断网"，都不算运行时联网
    skip = {"fetch_wheels.py", "test_offline.py"}
    hits = [h for h in hits if not any(s in h for s in skip)]
    if hits:
        print("⚠️ 源码里发现疑似联网调用：")
        for h in hits:
            print("   ", h)
    else:
        print("✅ 静态扫描：核心与界面代码里没有任何联网调用")
    ok &= not hits

    # ---- 2) OCR 模型必须在本地 ----
    try:
        import rapidocr_onnxruntime
        mdir = os.path.join(os.path.dirname(rapidocr_onnxruntime.__file__), "models")
        onnx = [f for f in os.listdir(mdir) if f.endswith(".onnx")]
        size = sum(os.path.getsize(os.path.join(mdir, f)) for f in onnx) / 1024 / 1024
        print(f"✅ 离线模型就位：{len(onnx)} 个 onnx，共 {size:.1f} MB（{mdir}）")
        ok &= len(onnx) >= 3
    except Exception as exc:
        print(f"❌ 模型检查失败：{exc}")
        ok = False

    # ---- 3) 真正掐网跑一次转换 ----
    saved = {}
    for name in ("socket", "create_connection", "getaddrinfo", "gethostbyname"):
        saved[name] = getattr(socket, name, None)
    socket.socket = _block              # type: ignore[assignment]
    socket.create_connection = _block   # type: ignore[assignment]
    socket.getaddrinfo = _block         # type: ignore[assignment]
    socket.gethostbyname = _block       # type: ignore[assignment]
    try:
        import ssl
        saved["ssl_wrap"] = ssl.SSLContext.wrap_socket
        ssl.SSLContext.wrap_socket = _block   # type: ignore[assignment]
    except Exception:
        pass

    try:
        from core.pipeline import ConvertOptions, convert_pdf
        sample = os.path.join(ROOT, "samples", "d7271293e64e9585b5c065a2e1164066.pdf")
        if not os.path.exists(sample):
            # 仓库不含样本（都是真实当事人材料），退而求其次：用 samples/ 下任意一个 PDF
            sdir = os.path.join(ROOT, "samples")
            cands = [os.path.join(sdir, f) for f in sorted(os.listdir(sdir))] if os.path.isdir(sdir) else []
            cands = [c for c in cands if c.lower().endswith(".pdf")]
            if not cands:
                print("跳过：samples/ 下没有 PDF。离线证明需要一个真实样本，放进 samples/ 再运行。")
                return 0
            sample = cands[0]
        out = os.path.join(ROOT, "out", "offline_test")
        r = convert_pdf(sample, ConvertOptions(outdir=out, make_searchable_pdf=True))
        if r.ok and r.docx and os.path.exists(r.docx):
            print(f"✅ 断网状态下转换成功：{r.summary()}")
            print(f"   输出 {r.docx}")
        else:
            print(f"❌ 断网状态下转换失败：{r.error}")
            ok = False
    except NetworkBlocked as exc:
        print(f"❌ 有代码试图联网：{exc}")
        ok = False
    except Exception as exc:
        import traceback
        print(f"❌ 断网测试异常：{exc}\n{traceback.format_exc()}")
        ok = False
    finally:
        for name, val in saved.items():
            if name == "ssl_wrap":
                try:
                    import ssl
                    ssl.SSLContext.wrap_socket = val   # type: ignore[assignment]
                except Exception:
                    pass
            elif val is not None:
                setattr(socket, name, val)

    print("\n" + ("✅ 离线证明通过：全程不碰网络也能完成转换" if ok else "❌ 离线证明未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
