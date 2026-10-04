"""接口版本对比：逐项判定新增 / 删除 / 签名变化，并标注影响等级。

等级说明：
  compatible : 向后兼容（新增可选参数、返回扩展时旧字段不变时调用方不受影响）
  caution    : 低风险但行为可能变化（默认值变化、返回结构扩展）——单独标注
  breaking   : 破坏性变化（删除函数/参数、新增必填参数、类型变化、返回字段删除）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Function, Interface, NO_DEFAULT, Param, ReturnField

COMPATIBLE = "compatible"
CAUTION = "caution"
BREAKING = "breaking"

LEVEL_ORDER = {COMPATIBLE: 0, CAUTION: 1, BREAKING: 2}
LEVEL_LABEL = {
    COMPATIBLE: "兼容",
    CAUTION: "低风险/需关注",
    BREAKING: "破坏性",
}

# 判定条目类别
FUNCTION_ADDED = "FUNCTION_ADDED"
FUNCTION_REMOVED = "FUNCTION_REMOVED"
PARAM_ADDED_OPTIONAL = "PARAM_ADDED_OPTIONAL"
PARAM_ADDED_REQUIRED = "PARAM_ADDED_REQUIRED"
PARAM_REMOVED_OPTIONAL = "PARAM_REMOVED_OPTIONAL"
PARAM_REMOVED_REQUIRED = "PARAM_REMOVED_REQUIRED"
PARAM_TYPE_CHANGED = "PARAM_TYPE_CHANGED"
PARAM_REQUIRED_TIGHTENED = "PARAM_REQUIRED_TIGHTENED"
PARAM_REQUIRED_LOOSENED = "PARAM_REQUIRED_LOOSENED"
PARAM_DEFAULT_CHANGED = "PARAM_DEFAULT_CHANGED"
PARAM_DEFAULT_ADDED = "PARAM_DEFAULT_ADDED"
PARAM_DEFAULT_REMOVED = "PARAM_DEFAULT_REMOVED"
RETURN_EXTENDED = "RETURN_EXTENDED"
RETURN_FIELD_REMOVED = "RETURN_FIELD_REMOVED"
RETURN_FIELD_REQUIRED_TIGHTENED = "RETURN_FIELD_REQUIRED_TIGHTENED"
RETURN_FIELD_TYPE_CHANGED = "RETURN_FIELD_TYPE_CHANGED"
RETURN_TYPE_CHANGED = "RETURN_TYPE_CHANGED"


@dataclass
class Judgment:
    function: str
    kind: str
    level: str
    target: str  # 函数名 / 参数名 / 返回字段名
    detail: str
    rationale: str


@dataclass
class CompareReport:
    old_version: str
    new_version: str
    judgments: list[Judgment] = field(default_factory=list)

    @property
    def max_level(self) -> str:
        if not self.judgments:
            return COMPATIBLE
        return max((j.level for j in self.judgments), key=lambda lv: LEVEL_ORDER[lv])

    def for_function(self, name: str) -> list[Judgment]:
        return [j for j in self.judgments if j.function == name]

    def kinds_for_function(self, name: str) -> set[str]:
        return {j.kind for j in self.for_function(name)}


def _fmt_default(value: Any) -> str:
    if value is NO_DEFAULT:
        return "<无默认值>"
    return repr(value)


def _judge_param(func_name: str, old: Param, new: Param) -> list[Judgment]:
    out: list[Judgment] = []
    if old.type != new.type:
        out.append(Judgment(
            func_name, PARAM_TYPE_CHANGED, BREAKING, new.name,
            f"参数 {new.name} 类型 {old.type} -> {new.type}",
            "调用方按旧类型传入的值可能无法被新版本接受，或产生不同行为。",
        ))
    if not old.required and new.required:
        out.append(Judgment(
            func_name, PARAM_REQUIRED_TIGHTENED, BREAKING, new.name,
            f"参数 {new.name} 由可选变为必填",
            "旧版本可不传该参数，新版本不传会报错，存量调用直接失败。",
        ))
    elif old.required and not new.required:
        out.append(Judgment(
            func_name, PARAM_REQUIRED_LOOSENED, COMPATIBLE, new.name,
            f"参数 {new.name} 由必填变为可选",
            "旧调用总是传该参数，放宽要求不会破坏存量调用。",
        ))
    if old.default is NO_DEFAULT and new.default is not NO_DEFAULT:
        out.append(Judgment(
            func_name, PARAM_DEFAULT_ADDED, COMPATIBLE, new.name,
            f"参数 {new.name} 新增默认值 {_fmt_default(new.default)}",
            "新增默认值等价于把必填放宽为可选，旧调用不受影响。",
        ))
    elif old.default is not NO_DEFAULT and new.default is NO_DEFAULT:
        out.append(Judgment(
            func_name, PARAM_DEFAULT_REMOVED, BREAKING, new.name,
            f"参数 {new.name} 移除默认值",
            "旧版本可省略的参数在新版本变为必填，省略该参数的旧调用失败。",
        ))
    elif old.default != new.default:
        out.append(Judgment(
            func_name, PARAM_DEFAULT_CHANGED, CAUTION, new.name,
            f"参数 {new.name} 默认值 {_fmt_default(old.default)} -> "
            f"{_fmt_default(new.default)}",
            "签名仍然兼容（参数仍可选、类型未变），但省略该参数的存量调用"
            "会得到不同行为，属于静默行为变化，需要单独评估。",
        ))
    return out


def _judge_returns(func_name: str, old: Function, new: Function) -> list[Judgment]:
    out: list[Judgment] = []
    if old.return_type != new.return_type:
        out.append(Judgment(
            func_name, RETURN_TYPE_CHANGED, BREAKING, "<return>",
            f"返回类型 {old.return_type} -> {new.return_type}",
            "调用方按旧类型使用返回值，类型变化会导致取值或运算失败。",
        ))
    old_fields = {f.name: f for f in old.return_fields}
    new_fields = {f.name: f for f in new.return_fields}
    for name, nf in new_fields.items():
        if name not in old_fields:
            out.append(Judgment(
                func_name, RETURN_EXTENDED, CAUTION, name,
                f"返回结构新增字段 {name}（类型 {nf.type}）",
                "新增字段不会破坏读取旧字段的调用方，但严格断言完整返回结构"
                "（如 schema 全等校验、位置解包）的调用会受影响，需单独确认。",
            ))
    for name, of in old_fields.items():
        if name not in new_fields:
            out.append(Judgment(
                func_name, RETURN_FIELD_REMOVED, BREAKING, name,
                f"返回结构删除字段 {name}",
                "读取该字段的旧调用会取空或抛错，属于破坏性变化。",
            ))
        else:
            nf = new_fields[name]
            if of.type != nf.type:
                out.append(Judgment(
                    func_name, RETURN_FIELD_TYPE_CHANGED, BREAKING, name,
                    f"返回字段 {name} 类型 {of.type} -> {nf.type}",
                    "调用方按旧类型使用该字段会出错。",
                ))
            if not of.required and nf.required:
                out.append(Judgment(
                    func_name, RETURN_FIELD_REQUIRED_TIGHTENED, BREAKING, name,
                    f"返回字段 {name} 由可选变为必有",
                    "注意：返回端的必填收紧对调用方本身兼容；此处仅在实现可能"
                    "不满足新契约时提示，默认按破坏性契约变化登记。",
                ))
    return out


def compare_interfaces(old: Interface, new: Interface) -> CompareReport:
    report = CompareReport(old.version, new.version)
    for name, nf in new.functions.items():
        if name not in old.functions:
            report.judgments.append(Judgment(
                name, FUNCTION_ADDED, COMPATIBLE, name,
                f"新增函数 {name}",
                "新增能力不影响存量调用。",
            ))
            continue
        of = old.functions[name]
        old_params = {p.name: p for p in of.params}
        new_params = {p.name: p for p in nf.params}
        for pname, np in new_params.items():
            if pname not in old_params:
                if np.required:
                    report.judgments.append(Judgment(
                        name, PARAM_ADDED_REQUIRED, BREAKING, pname,
                        f"新增必填参数 {pname}（类型 {np.type}）",
                        "旧调用不传该参数，新版本会因缺参报错。",
                    ))
                else:
                    detail = (f"新增可选参数 {pname}（类型 {np.type}，"
                              f"默认 {_fmt_default(np.default)}）")
                    report.judgments.append(Judgment(
                        name, PARAM_ADDED_OPTIONAL, COMPATIBLE, pname, detail,
                        "新参数带默认值，旧调用不传时行为与旧版本一致（以实现遵守"
                        "默认值为前提，由实际调用测试复核）。",
                    ))
        for pname, op in old_params.items():
            if pname not in new_params:
                kind = (PARAM_REMOVED_REQUIRED if op.required
                        else PARAM_REMOVED_OPTIONAL)
                level = BREAKING  # 静态契约一律按破坏登记；能否继续调用由运行时复核
                report.judgments.append(Judgment(
                    name, kind, level, pname,
                    f"删除参数 {pname}（{'必填' if op.required else '可选'}）",
                    "位置传参的调用可能错位；关键字传该参数的调用会因意外关键字"
                    "报错。静态判定一律视为破坏性，运行时测试再确认是否误报。",
                ))
            else:
                report.judgments.extend(_judge_param(name, op, new_params[pname]))
        report.judgments.extend(_judge_returns(name, of, nf))
    for name, of in old.functions.items():
        if name not in new.functions:
            report.judgments.append(Judgment(
                name, FUNCTION_REMOVED, BREAKING, name,
                f"删除函数 {name}",
                "所有对该函数的存量调用在新版本中直接失败。",
            ))
    return report
