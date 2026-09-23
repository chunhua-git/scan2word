# -*- coding: utf-8 -*-
"""法律词表纠错：专治「裁判→栽判、变更→变吏、电子→匕子、如果→如呆」这类错字。

设计原则（宁可漏改，绝不乱改）
------------------------------
1. **只改"改完能落到真词上"的**：某处出现形近字时，替换后的 2~4 字窗口必须命中
   法律词表；否则原样保留。所以不会把本来正确的字改坏。
2. **每处修改都留痕**，写进质检报告的「词表纠正」小节，可追溯。
3. 词表是纯文本、可随时增补（`core/data/法律词表.txt`），不需要改代码。

为什么不用通用纠错/语言模型：法院文书是封闭领域，通用模型会把「案外人」改成
「案外人」以外的词、把「执恢」当成错字。词表法在封闭领域更准也更可控。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 形近字候选：OCR 常把左边的字认成右边。一个错字可能有多个正确候选
# （如「吏」既可能是「更」也可能是「人」），全部试一遍，由词表决定用哪个。
CONFUSABLE: Dict[str, List[str]] = {
    "栽": ["裁"], "吏": ["更", "人"], "匕": ["电", "比"], "呆": ["果"],
    "己": ["已"], "巳": ["已"], "曰": ["日"], "帐": ["账"], "拆": ["折"],
    "鉴": ["签"], "无": ["元"], "千": ["干"], "士": ["土"], "犬": ["太"],
    "本": ["木"], "径": ["经"], "俱": ["具"], "叉": ["义"], "杈": ["权"],
    "未": ["末"], "末": ["未"], "入": ["人"], "人": ["入"], "丸": ["九"],
    "刀": ["力"], "广": ["厂"], "汆": ["氽"], "羸": ["赢"], "祟": ["崇"],
    "雎": ["睢"], "亳": ["毫"], "辩": ["辨"], "辨": ["辩"], "侯": ["候"],
    "拔": ["拨"], "拨": ["拔"], "载": ["裁"], "哉": ["裁"], "史": ["吏"],
    "果": ["呆"], "皖": ["院"], "阮": ["院"],
}

# 兜底词表：即使 data/法律词表.txt 丢了也能工作
_FALLBACK_TERMS = """
人民法院 最高人民法院 高级人民法院 中级人民法院 基层人民法院
裁判文书 裁判 裁定 判决 调解 立案 开庭 庭审 上诉 二审 一审 再审 再审审查
执行程序 强制执行 执行案款 执行异议 恢复执行 终结本次执行程序 财产报告令
送达 送达地址 送达方式 送达日期 送达回证 受送达人 指定签收人 确认送达地址
电子送达 电子方式 直接送达 邮寄送达 公告送达 留置送达 委托送达
当事人 申请执行人 被执行人 案外人 申请人 被申请人 代理人 委托代理人
诉讼文书 执行文书 法律后果 告知事项 告知 事项 签名或签章 签名 签章
领款人 收款账号 收款凭证 开户行 开户银行 银行账户 账号 卡号 户名 支行
借记卡 存折 一类账户 转账 款项 凭证 案号 案由 纠纷 合同纠纷 借款合同
民间借贷 房屋买卖 房屋租赁 租赁 租金 安置 预留 房款 拍卖 变卖 查封
优先购买权 优先受偿 变更 变更登记 电子 如果 因此 并且 或者 以及 确实
证件类型 证件号码 身份证 统一社会信用代码 手机号码 联系电话 其他联系方式
传真号码 电子邮件地址 微信号 邮政编码 邮编 承办法官 法院 法官 书记员
广西壮族自治区 内蒙古自治区 新疆维吾尔自治区 宁夏回族自治区 西藏自治区
""".split()


def load_terms() -> set:
    """读词表。

    支持两份文件，都放在 core/data/ 下：
      法律词表.txt    —— 内置的领域词表（法院文书常用词）
      自定义词表.txt  —— 你自己的领域词，转换时自动合并，不用改代码
    词表只影响「形近字纠正」能不能命中，不影响任何其他功能；
    非法律文档下它只是不生效，不会帮倒忙。
    """
    terms = set(_FALLBACK_TERMS)
    for fname in ("法律词表.txt", "自定义词表.txt"):
        path = os.path.join(_DATA, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        terms.update(line.split())
        except Exception:
            continue
    return {t for t in terms if len(t) >= 2}


LEGAL_TERMS = load_terms()


@dataclass
class Correction:
    before: str
    after: str
    position: int
    context: str

    def __str__(self) -> str:
        return f"「{self.before}」→「{self.after}」（…{self.context}…）"


def correct_text(text: str, terms: set | None = None,
                 max_window: int = 4) -> Tuple[str, List[Correction]]:
    """按法律词表纠正形近字。返回 (纠正后文本, 纠正记录)。"""
    if not text:
        return text, []
    terms = terms or LEGAL_TERMS
    chars = list(text)
    fixes: List[Correction] = []

    for i, ch in enumerate(chars):
        alts = CONFUSABLE.get(ch)
        if not alts:
            continue
        applied = False
        for span in (2, 3, 4):
            if applied:
                break
            lo = max(0, i - span + 1)
            hi = min(i, len(chars) - span)
            for start in range(lo, hi + 1):
                win = "".join(chars[start:start + span])
                if win in terms or not win.strip():
                    continue
                for alt in alts:
                    if alt == ch:
                        continue
                    trial = chars.copy()
                    trial[i] = alt
                    twin = "".join(trial[start:start + span])
                    if twin in terms:
                        chars[i] = alt
                        ctx0 = max(0, start - 6)
                        ctx1 = min(len(chars), start + span + 6)
                        fixes.append(Correction(
                            before=win, after=twin, position=i,
                            context="".join(chars[ctx0:ctx1]),
                        ))
                        applied = True
                        break
                if applied:
                    break
    return "".join(chars), fixes


# ----------------------------------------------------------------------------- 法院名称

RE_COURT = re.compile(r"^[\u4e00-\u9fff]{2,40}?(人民法院|人民检察院)$")
RE_CASE_NO = re.compile(r"[（(]\s*\d{4}\s*[)）]\s*[\u4e00-\u9fff]{1,4}\d{0,4}\s*[\u4e00-\u9fff]{1,4}\s*\d{1,6}\s*号")

# 行政区划通名，用来判断"法院名看起来是不是完整"
_ADMIN_TAIL = ("省", "市", "区", "县", "自治县", "旗", "自治区", "自治州", "盟", "地区",
               "林区", "新区", "开发区", "矿区")
_SPECIAL = ("最高人民法院", "解放军军事法院", "军事法院", "海事法院", "知识产权法院",
            "金融法院", "铁路运输中级法院", "铁路运输法院", "互联网法院")


def check_court_name(line: str) -> Tuple[bool, str]:
    """粗略校验一行是不是像个完整法院名。返回 (是否像, 说明)。"""
    s = line.strip()
    if not s:
        return False, "空"
    if any(k in s for k in _SPECIAL):
        return True, "专门法院/最高法院"
    if not RE_COURT.match(s):
        return False, "不以「人民法院」结尾"
    body = s.replace("人民法院", "").replace("人民检察院", "")
    if len(body) < 2:
        return False, "地名部分过短"
    if body.endswith(_ADMIN_TAIL) or any(t in body for t in _ADMIN_TAIL):
        return True, "含行政区划通名"
    return False, f"地名部分「{body}」不含区划通名（省/市/区/县等），建议人工核对"
