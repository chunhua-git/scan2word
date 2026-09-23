# -*- coding: utf-8 -*-
"""验收与质检：逐字比对、数字域校验、混淆字告警。

用户验收标准是「正文错字率≈0、案号与证件号单独放大核对」，所以这里必须给出
可量化、可复现的报告，而不是「转换成功」。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Tuple

# Wingdings/Symbol 私用区勾选框 → 正字（比对前统一）
_PRIVATE_BOX = {"\uf0a3": "□", "\uf0a8": "□", "\uf06f": "□", "\uf0a1": "□",
                "\uf0fe": "☑", "\uf0a4": "☑", "\uf0fd": "☒", "\uf0fc": "√",
                "\uf0d8": "√", "\uf0ff": "√"}


def normalize(text: str, keep_punct: bool = True) -> str:
    """归一化：去空白、统一勾选框与全半角，用于逐字比对。"""
    for k, v in _PRIVATE_BOX.items():
        text = text.replace(k, v)
    text = re.sub(r"\s+", "", text)
    # 全角数字/字母 → 半角
    out = []
    for ch in text:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    text = "".join(out)
    if not keep_punct:
        text = re.sub(r"[，。、；：！？（）()【】《》\"'·,.!?;:~—\-]", "", text)
    return text


def normalize_keep_punct(text: str) -> str:
    return normalize(text, keep_punct=True)


# ----------------------------------------------------------------------------- 逐字比对

@dataclass
class TextDiff:
    """逐字比对结果。

    equal       —— 字符多重集一致（= 没有认错字/漏字），这才是「错字率」的口径；
    same_order  —— 顺序也一致（PDF 内部文本流顺序未必等于视觉阅读顺序，仅作参考）。
    """
    equal: bool = True
    same_order: bool = True
    missing: List[Tuple[str, int]] = field(default_factory=list)   # 参考有、输出没有
    extra: List[Tuple[str, int]] = field(default_factory=list)     # 输出有、参考没有
    first_divergence: int = -1
    ref_len: int = 0
    got_len: int = 0

    @property
    def err_rate(self) -> float:
        if not self.ref_len:
            return 0.0
        bad = sum(n for _, n in self.missing) + sum(n for _, n in self.extra)
        return bad / self.ref_len

    def summary(self) -> str:
        if self.equal:
            tail = "" if self.same_order else "（阅读顺序按版面重排，内容一致）"
            return f"逐字一致（{self.ref_len} 字，错字率 0.00%）{tail}"
        parts = [f"不一致：参考 {self.ref_len} 字 / 输出 {self.got_len} 字，错字率 {self.err_rate:.2%}"]
        if self.missing:
            parts.append("缺/错: " + " ".join(f"{c}×{n}" for c, n in self.missing[:12]))
        if self.extra:
            parts.append("多/错: " + " ".join(f"{c}×{n}" for c, n in self.extra[:12]))
        return "；".join(parts)


def compare_text(ref: str, got: str) -> TextDiff:
    """逐字比对：以字符多重集为准（判断有没有认错字），顺序单独记录。"""
    a, b = normalize(ref), normalize(got)
    ca, cb = Counter(a), Counter(b)
    missing = [(c, n) for c, n in (ca - cb).most_common()]
    extra = [(c, n) for c, n in (cb - ca).most_common()]
    d = TextDiff(equal=not missing and not extra, same_order=(a == b),
                 missing=missing, extra=extra, ref_len=len(a), got_len=len(b))
    if a != b:
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                d.first_divergence = i
                break
    return d


# ----------------------------------------------------------------------------- 数字域校验

RE_CASE_NO = re.compile(r"[（(]\s*(\d{4})\s*[)）]\s*([\u4e00-\u9fff]{1,4}\d{0,4})\s*([\u4e00-\u9fff]{1,4})\s*(\d{1,6})\s*号")
RE_ID18 = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
RE_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
RE_ZIP = re.compile(r"(?<!\d)(\d{6})(?!\d)")
RE_BANK = re.compile(r"(?<!\d)(\d{12,21})(?!\d)")
RE_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

_ID_WEIGHT = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"


def id18_valid(s: str) -> bool:
    if len(s) != 18:
        return False
    if not re.fullmatch(r"\d{17}[\dXx]", s):
        return False
    total = sum(int(c) * w for c, w in zip(s[:17], _ID_WEIGHT))
    return _ID_CHECK[total % 11] == s[17].upper()


def luhn_valid(s: str) -> bool:
    total, alt = 0, False
    for ch in reversed(s):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


@dataclass
class Finding:
    field: str
    value: str
    level: str        # ok | warn | error
    note: str = ""

    def __str__(self) -> str:
        icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}[self.level]
        return f"{icon} [{self.field}] {self.value}  {self.note}"


def check_numbers(text: str, field_hint: str = "") -> List[Finding]:
    """对数字类字段做格式/校验位检查——这是「案号、证件号零错」的把关环节。"""
    out: List[Finding] = []

    for m in RE_CASE_NO.finditer(text):
        year, court, kind, num = m.groups()
        lvl, note = "ok", ""
        if not (1949 <= int(year) <= 2100):
            lvl, note = "error", "年份不合理"
        if not num.strip("0"):
            lvl, note = "warn", "编号序号为空或全 0（可能是空白模板）"
        out.append(Finding("编号", m.group(0), lvl, note))

    for m in RE_ID18.finditer(text):
        v = m.group(1)
        out.append(Finding("身份证号", v, "ok" if id18_valid(v) else "warn",
                           "" if id18_valid(v) else "校验位不符，需人工核对"))

    for m in RE_PHONE.finditer(text):
        out.append(Finding("手机号", m.group(1), "ok"))

    for m in RE_DATE.finditer(text):
        y, mo, d = (int(x) for x in m.groups())
        ok = 1 <= mo <= 12 and 1 <= d <= 31
        out.append(Finding("日期", m.group(0), "ok" if ok else "error",
                           "" if ok else "月/日不合法"))

    for m in RE_BANK.finditer(text):
        v = m.group(1)
        if RE_PHONE.fullmatch(v) or RE_ID18.fullmatch(v) or len(v) < 12:
            continue
        out.append(Finding("长数字串", v, "ok" if luhn_valid(v) else "warn",
                           "Luhn 通过" if luhn_valid(v) else "Luhn 未通过（储蓄账号常见，需人工确认）"))
    return out


# ----------------------------------------------------------------------------- 混淆字告警

# 常见 OCR 易混对（PDFgear 那类错字的来源）。只在「低置信度行」里提示，避免刷屏。
CONFUSABLE_GROUPS = [
    "裁栽载哉", "更吏史", "电匕日", "果呆", "己已巳", "未末", "日曰", "人入",
    "拨拔", "侯候", "账帐", "折拆", "辩辨辫", "签鉴", "元无", "干千",
    "土士", "太犬", "木本", "径经", "付吋", "讫迄", "具俱", "义叉", "权杈",
    "九丸", "力刀", "厂广", "汆氽", "赢羸", "崇祟", "睢雎", "毫亳",
]
_CHAR2GROUP = {c: i for i, g in enumerate(CONFUSABLE_GROUPS) for c in g}


def confusable_warnings(lines: List[Tuple[str, float]], conf_threshold: float = 0.95
                        ) -> List[Finding]:
    """只在置信度不足的行里提示形近字，让告警可执行。

    lines: [(文本, 置信度), ...]
    """
    out: List[Finding] = []
    seen: set[str] = set()
    for text, conf in lines:
        if conf >= conf_threshold:
            continue
        for ch in text:
            if ch in _CHAR2GROUP and ch not in seen:
                seen.add(ch)
                out.append(Finding("形近字待核", ch, "warn",
                                   f"出现在低置信行（conf={conf:.2f}），形近字："
                                   + CONFUSABLE_GROUPS[_CHAR2GROUP[ch]]
                                   + "；上下文：" + text[:40]))
    return out


# ----------------------------------------------------------------------------- 字母/数字易混

# 单号、编号、证件号里最常见的字母数字互认
_MIX_CONFUSE = {"0": "O", "O": "0", "1": "I/l", "I": "1", "l": "1",
                "5": "S", "S": "5", "8": "B", "B": "8", "2": "Z", "Z": "2"}
RE_CODE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/.]{7,}")


def latin_confusable_warnings(text: str) -> List[Finding]:
    """检查单号/编号类字符串里「字母数字互认」的风险。

    **只报警不自动改**：`SO-2026` 被认成 `S0-2026` 是真错误，但 `A0` 本来就可能是
    "A 加零"。没有领域知识无法判断该往哪边改，所以交给人工核对。
    为避免刷屏，只挑长度 ≥8、同时含字母和数字、且易混字符**紧邻字母**的串。
    """
    out: List[Finding] = []
    seen: set[str] = set()
    for m in RE_CODE_TOKEN.finditer(text):
        tok = m.group(0)
        if tok in seen:
            continue
        if not (any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok)):
            continue
        risky = set()
        for i, ch in enumerate(tok):
            if ch not in _MIX_CONFUSE:
                continue
            left = tok[i - 1] if i > 0 else ""
            right = tok[i + 1] if i + 1 < len(tok) else ""
            if left.isalpha() or right.isalpha():     # 必须紧邻字母才算可疑
                risky.add(f"{ch}↔{_MIX_CONFUSE[ch]}")
        if risky:
            seen.add(tok)
            out.append(Finding("字母数字易混", tok, "warn",
                               "含易混字符 " + "、".join(sorted(risky))
                               + "（如 0↔O、1↔I/l、5↔S），单号/编号请人工核对"))
    return out
