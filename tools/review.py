# -*- coding: utf-8 -*-
"""交付前代码审查：语法、未用导入、过期引用、文档与代码是否一致。

不是"看着差不多"的检查——每一项都给出具体文件行号。
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

TARGET_DIRS = ("core", "app", "tests", "tools", "packaging")
SKIP = ("__pycache__",)


def py_files():
    for sub in TARGET_DIRS:
        base = os.path.join(ROOT, sub)
        for dp, dirs, fs in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP]
            for fn in sorted(fs):
                if fn.endswith(".py"):
                    yield os.path.join(dp, fn)


def rel(p):
    return os.path.relpath(p, ROOT)


print("=" * 78)
print("1) 语法检查")
bad = 0
for p in py_files():
    try:
        # 用内存 compile，不落盘（cfile=nul 在 Windows 上会失败）
        compile(open(p, encoding="utf-8").read(), p, "exec")
    except SyntaxError as exc:
        print(f"   ❌ {rel(p)}:{exc.lineno}  {exc.msg}")
        bad += 1
print("   ✅ 全部通过" if not bad else f"   {bad} 个文件有语法错误")


print()
print("=" * 78)
print("2) 未使用的导入（AST 静态分析）")
unused_total = 0
for p in py_files():
    try:
        src = open(p, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        continue
    imported = {}          # 名字 -> 行号
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = (a.asname or a.name).split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = node.lineno
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            pass
    # 字符串注解 / docstring 里也可能用到
    for name in list(imported):
        if name in used:
            imported.pop(name)
            continue
        if re.search(rf"\b{re.escape(name)}\b", src.split("\n", imported[name])[-1] if False else src[src.find("\n", 0):]):
            if len(re.findall(rf"\b{re.escape(name)}\b", src)) > 1:
                imported.pop(name)
    if imported:
        unused_total += len(imported)
        for name, line in sorted(imported.items(), key=lambda kv: kv[1]):
            print(f"   {rel(p)}:{line}  未使用: {name}")
print("   ✅ 没有未使用的导入" if not unused_total else f"   共 {unused_total} 处（不影响运行，可清理）")


print()
print("=" * 78)
print("3) 过期引用（指向已删除的东西 / 换掉的库）")
stale_patterns = {
    "pymupdf / fitz": re.compile(r"\b(pymupdf|import fitz|fitz\.)"),
    "已删的探针脚本": re.compile(r"probe_ocr\.py|probe2\.py|probe3\.py"),
    "不存在的样本名": re.compile(r"送达地址确认书_旧版输出_参考"),
    "写死的样本路径": re.compile(r"samples[\\/][^\"'\)\s]*\.pdf"),
}
stale = 0
# compare_pdf_backends.py 是唯一"故意"引用 PyMuPDF 的地方：它的作用就是拿 PyMuPDF
# 做对照，证明换库没有质量损失。那边 PyMuPDF 是显式可选依赖，不是本项目依赖。
ALLOW = {"review.py", "compare_pdf_backends.py"}
for p in py_files():
    if os.path.basename(p) in ALLOW:
        continue
    src = open(p, encoding="utf-8", errors="ignore").read()
    for i, line in enumerate(src.splitlines(), 1):
        for label, rx in stale_patterns.items():
            if rx.search(line):
                # 允许在注释里解释「为什么不用 PyMuPDF」
                stripped = line.strip()
                if label == "pymupdf / fitz" and (stripped.startswith("#") or '"""' in line):
                    continue
                print(f"   [{label}] {rel(p)}:{i}  {line.strip()[:80]}")
                stale += 1
print("   ✅ 没有过期引用" if not stale else f"   共 {stale} 处")


print()
print("=" * 78)
print("4) 文档里承诺的功能，代码里是否真的有")
claims = [
    ("双路分流（文字层走矢量）", "core/textlayer.py", "def build_page"),
    ("OCR 路", "core/ocr.py", "def build_page"),
    ("表格网格重建", "core/grid.py", "def build_grid"),
    ("勾选框检测", "core/ocr.py", "def detect_checkboxes"),
    ("印章检测", "core/ocr.py", "def detect_stamps"),
    ("空白填写栏还原", "core/ocr.py", "def insert_blank_fills"),
    ("数字换分辨率复核", "core/number_audit.py", "def audit_numbers"),
    ("法律词表纠错", "core/lexicon.py", "def correct_text"),
    ("法院名校验", "core/lexicon.py", "def check_court_name"),
    ("身份证校验位", "core/verify.py", "def id18_valid"),
    ("双层可搜索 PDF", "core/searchable_pdf.py", "def write_searchable_pdf"),
    ("忽略区域", "core/textlayer.py", "ignore_zones"),
    ("引擎注册表", "core/engines/__init__.py", "def create_engine"),
    ("PDF 后端抽象", "core/pdfdoc.py", "class PDFDocument"),
    ("拖拽界面", "app/main.py", "def dragEnterEvent"),
    ("批量队列", "core/pipeline.py", "def convert_many"),
]
missing = 0
for label, path, needle in claims:
    full = os.path.join(ROOT, path)
    ok = os.path.exists(full) and needle in open(full, encoding="utf-8", errors="ignore").read()
    print(f"   {'✅' if ok else '❌'} {label:<24} {path}")
    if not ok:
        missing += 1

print()
print("=" * 78)
print(f"结论：语法 {'OK' if not bad else 'FAIL'} / 未用导入 {unused_total} / 过期引用 {stale} / 功能缺失 {missing}")
