"""分级规则表。

每条规则可基于三类证据命中：字段名（name_patterns）、取值样例
（value_patterns + value_check）、来源（sources）。规则带显式优先级，
冲突时按 (priority, level) 从高到低裁决，见 engine.resolve。
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Optional


def luhn_ok(number: str) -> bool:
    """银行卡号 Luhn 校验。"""
    if not number.isdigit():
        return False
    total = 0
    for index, ch in enumerate(reversed(number)):
        digit = int(ch)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@dataclass(frozen=True)
class Rule:
    rule_id: str
    level: str
    priority: int
    description: str
    name_patterns: tuple = ()
    value_patterns: tuple = ()
    sources: Optional[tuple] = None  # None 表示不限来源
    value_check: Optional[Callable[[str], bool]] = None  # 正则之外的取值校验
    value_threshold: float = 0.5  # 取值样例命中比例下限
    basis: str = ""  # 规则依据（写入分级依据表）

    def name_hit(self, field_name: str) -> Optional[str]:
        for pattern in self.name_patterns:
            if re.search(pattern, field_name, re.IGNORECASE):
                return pattern
        return None

    def source_hit(self, source: str) -> bool:
        if self.sources is None:
            return True
        return source in self.sources

    def value_hits(self, samples):
        """返回 (命中数, 命中样例列表)。"""
        hits = []
        for sample in samples:
            text = "" if sample is None else str(sample).strip()
            if not text:
                continue
            matched = any(re.fullmatch(p, text) for p in self.value_patterns)
            if matched and self.value_check is not None:
                matched = bool(self.value_check(text))
            if matched:
                hits.append(text)
        return len(hits), hits


DEFAULT_RULES = (
    Rule(
        rule_id="R-ID-VALUE",
        level="L4",
        priority=100,
        description="取值符合 18 位身份证号格式",
        value_patterns=(r"\d{17}[\dXx]",),
        value_threshold=0.3,
        basis="《个人信息保护法》敏感个人信息：身份证件号码",
    ),
    Rule(
        rule_id="R-BANK-VALUE",
        level="L4",
        priority=98,
        description="取值为 16-19 位数字且通过 Luhn 校验（银行卡号）",
        value_patterns=(r"\d{16,19}",),
        value_check=luhn_ok,
        value_threshold=0.3,
        basis="金融数据安全分级指南：银行卡号属高度敏感",
    ),
    Rule(
        rule_id="R-PHONE-VALUE",
        level="L3",
        priority=90,
        description="取值符合大陆手机号格式",
        value_patterns=(r"1[3-9]\d{9}",),
        value_threshold=0.3,
        basis="个人信息分级：手机号属敏感个人信息",
    ),
    Rule(
        rule_id="R-EMAIL-VALUE",
        level="L3",
        priority=88,
        description="取值符合邮箱格式",
        value_patterns=(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",),
        value_threshold=0.3,
        basis="个人信息分级：邮箱属敏感个人信息",
    ),
    Rule(
        rule_id="R-EMP-CODE-VALUE",
        level="L2",
        priority=65,
        description="取值为内部工号格式（如 E0001）",
        value_patterns=(r"[A-Z]\d{4}",),
        basis="内部编码仅用于组织内部，定为内部级",
    ),
    Rule(
        rule_id="R-ID-NAME",
        level="L4",
        priority=60,
        description="字段名指向身份证件号",
        name_patterns=(r"身份证", r"id_?card", r"identity"),
        basis="按字段语义推定：身份证件号属高度敏感（可被取值证据推翻）",
    ),
    Rule(
        rule_id="R-NAME-PII",
        level="L3",
        priority=55,
        description="字段名指向姓名",
        name_patterns=(r"姓名", r"real_?name", r"full_?name"),
        basis="个人信息分级：姓名属敏感个人信息",
    ),
    Rule(
        rule_id="R-ADDR-NAME",
        level="L3",
        priority=55,
        description="字段名指向住址",
        name_patterns=(r"地址", r"address", r"addr"),
        basis="个人信息分级：住址属敏感个人信息",
    ),
    Rule(
        rule_id="R-SOURCE-PUBLIC",
        level="L1",
        priority=58,
        description="来源为对外公开渠道",
        sources=("官网公开页", "公开年报"),
        basis="已公开信息定为公开级（来源证据优先于字段名，弱于取值证据）",
    ),
    Rule(
        rule_id="R-STATUS-NAME",
        level="L1",
        priority=10,
        description="字段名为状态/类型类通用枚举",
        name_patterns=(r"status", r"状态", r"type", r"类型"),
        basis="通用枚举字段不含个人信息，定为公开级",
    ),
)

DEFAULT_LEVEL = "L2"
DEFAULT_LEVEL_REASON = "无规则命中，按兜底策略定为内部级"
