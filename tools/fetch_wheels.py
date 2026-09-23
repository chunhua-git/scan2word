# -*- coding: utf-8 -*-
"""最小 wheel 安装器：本机环境中 pip 写临时目录受限，这里直接下载 wheel 解包到 vendor/。

用法：
    python tools/fetch_wheels.py --target vendor PySide6-Essentials pyinstaller
只解析运行时依赖（跳过 extra 与不满足环境标记的项），选与当前解释器最匹配的 wheel。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
import zipfile

import requests
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
from packaging.markers import default_environment

PYPI = "https://pypi.org/pypi/{name}/json"
UA = {"User-Agent": "scan2word-wheel-fetcher/1.0"}


def best_wheel(name: str, version: str | None = None) -> tuple[str, str] | None:
    """返回 (下载地址, 文件名)；按 sys_tags 优先级挑最合适的 wheel。"""
    meta = requests.get(PYPI.format(name=name), headers=UA, timeout=60).json()
    ver = version or meta["info"]["version"]
    files = meta["releases"].get(ver) or []
    tag_rank = {str(t): i for i, t in enumerate(sys_tags())}
    best, best_rank = None, 1 << 30
    for f in files:
        if not f["filename"].endswith(".whl"):
            continue
        try:
            _, _, _, tags = parse_wheel_filename(f["filename"])
        except Exception:
            continue
        rank = min((tag_rank.get(str(t), 1 << 29) for t in tags), default=1 << 29)
        if rank < best_rank:
            best, best_rank = f, rank
    if best is None or best_rank >= (1 << 29):
        return None
    return best["url"], best["filename"]


def runtime_deps(meta_json: dict) -> list[Requirement]:
    env = default_environment()
    out = []
    for raw in meta_json["info"].get("requires_dist") or []:
        try:
            req = Requirement(raw)
        except Exception:
            continue
        if req.marker is not None:
            try:
                if not req.marker.evaluate(env):
                    continue
            except Exception:
                continue
        out.append(req)
    return out


def already_installed(target: str, name: str) -> bool:
    norm = name.lower().replace("-", "_")
    if os.path.isdir(os.path.join(target, norm)):
        return True
    for d in os.listdir(target) if os.path.isdir(target) else []:
        if d.lower().replace("-", "_").startswith(norm + "-") and d.endswith(".dist-info"):
            return True
    return False


def install(name: str, target: str, seen: set[str]) -> None:
    key = name.lower().replace("_", "-")
    if key in seen:
        return
    seen.add(key)
    if already_installed(target, name):
        print(f"  [skip] {name} 已存在")
        return

    meta = requests.get(PYPI.format(name=name), headers=UA, timeout=60).json()
    pick = best_wheel(name)
    if pick is None:
        print(f"  [warn] {name} 无匹配 wheel，跳过（可能是源码包）")
        return
    url, fname = pick
    print(f"  [get ] {name} -> {fname}")
    data = requests.get(url, headers=UA, timeout=600).content
    t0 = time.time()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(target)
    print(f"         {len(data)/1024/1024:.1f} MB 解包 {time.time()-t0:.1f}s")

    for req in runtime_deps(meta):
        install(req.name, target, seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("packages", nargs="+")
    a = ap.parse_args()
    os.makedirs(a.target, exist_ok=True)
    seen: set[str] = set()
    for p in a.packages:
        print(f"== {p}")
        install(p, a.target, seen)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
