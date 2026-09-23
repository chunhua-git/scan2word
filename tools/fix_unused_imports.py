# -*- coding: utf-8 -*-
"""清理未使用的导入（保守策略）。

只在「这个名字在全文里只出现这一次」时才删——也就是说宁可漏删，也绝不误删。
删除后如果语法不过关会自动回滚。

    python tools/fix_unused_imports.py --dry     # 只看会改什么
    python tools/fix_unused_imports.py           # 实际修改
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TARGET_DIRS = ("core", "app", "tests", "tools", "packaging")
DRY = "--dry" in sys.argv

RE_FROM = re.compile(r"^(\s*)from\s+([\w\.]+)\s+import\s+(.+?)\s*$")
RE_IMPORT = re.compile(r"^(\s*)import\s+([\w\.]+)(\s+as\s+(\w+))?\s*$")


def py_files():
    for sub in TARGET_DIRS:
        base = os.path.join(ROOT, sub)
        for dp, dirs, fs in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in sorted(fs):
                if fn.endswith(".py"):
                    yield os.path.join(dp, fn)


def count_word(src: str, word: str) -> int:
    return len(re.findall(rf"\b{re.escape(word)}\b", src))


def process(path: str) -> list[str]:
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    out = []
    changes = []
    for line in lines:
        body = line.rstrip("\n")
        stripped = body.lstrip()

        # 这些一律不动：
        #   from __future__ import ...  是功能语句，删了会改变注解语义
        #   多行导入（括号跨行）     按行处理会把文件改坏
        skip_line = (stripped.startswith("#")
                     or "__future__" in body
                     or "(" in body and "import" in body)

        m = RE_FROM.match(body)
        if m and not skip_line:
            indent, module, names = m.groups()
            parts = [p.strip() for p in names.split(",")]
            keep, drop = [], []
            for p in parts:
                alias = p.split(" as ")[-1].strip() if " as " in p else p
                # 全文只出现一次 = 只有这条 import 自己
                if count_word(src, alias) <= 1:
                    drop.append(p)
                else:
                    keep.append(p)
            if drop and keep:
                changes.append(f"{os.path.relpath(path, ROOT)}: 从 {module} 移除 {', '.join(drop)}")
                out.append(f"{indent}from {module} import {', '.join(keep)}\n")
                continue
            if drop and not keep:
                changes.append(f"{os.path.relpath(path, ROOT)}: 删除整行 from {module} import ...")
                continue

        m2 = RE_IMPORT.match(body)
        if m2 and not skip_line:
            indent, module, _alias_group, alias = m2.groups()
            name = alias or module.split(".")[0]
            if count_word(src, name) <= 1:
                changes.append(f"{os.path.relpath(path, ROOT)}: 删除整行 import {module}")
                continue
        out.append(line)

    new = "".join(out)
    if new == src:
        return []
    try:
        compile(new, path, "exec")
    except SyntaxError as exc:
        print(f"   ⚠️ 改动会导致语法错误，跳过 {os.path.relpath(path, ROOT)}: {exc}")
        return []
    if not DRY:
        open(path, "w", encoding="utf-8").write(new)
    return changes


def main() -> int:
    all_changes = []
    for p in py_files():
        if os.path.basename(p) == "fix_unused_imports.py":
            continue
        all_changes += process(p)
    if not all_changes:
        print("没有可清理的未使用导入。")
        return 0
    print(("【试运行】会做以下修改：" if DRY else "【已修改】") )
    for c in all_changes:
        print("   " + c)
    print(f"\n共 {len(all_changes)} 处。" + ("（未写入，去掉 --dry 才生效）" if DRY else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
