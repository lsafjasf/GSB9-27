"""signed_url — HMAC-SHA256 签名链接库（仅标准库）。

设计要点：
- 签名覆盖：资源标识 + 生效时间(_nb) + 过期时间(_exp) + 全部业务参数（含重复参数、空值参数）。
- 规范化：参数名/值均做 RFC 3986 percent-encoding 后按 (名, 值) 字典序排序，
  因此参数书写顺序不影响签名（顺序不同不算篡改），但增、删、改任何一个参数都会使签名失效。
- 时间窗口：合法当且仅当  not_before - skew <= now <=expires_at + skew（闭区间，边界时刻有效）。
- 签名比较使用 hmac.compare_digest（恒定时间）。
- 时钟通过 clock 参数注入，便于测试。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qsl, quote, urlsplit

_VERSION = "v1"
RESERVED_PARAMS = ("_sig", "_exp", "_nb")

ParamPair = Tuple[str, str]
ParamsInput = Union[
    Mapping[str, Union[str, Sequence[str]]],
    Iterable[ParamPair],
    None,
]

REASON_OK = "ok"
REASON_MALFORMED = "malformed"          # 链接结构非法（缺签名段、时间非整数等）
REASON_BAD_SIGNATURE = "bad_signature"  # 签名不匹配：参数/资源/时间被增删改
REASON_NOT_YET_VALID = "not_yet_valid"  # 未到生效时间
REASON_EXPIRED = "expired"              # 已过过期时间


class IssueError(ValueError):
    """签发参数非法。"""


def _normalize_params(params: ParamsInput) -> Tuple[ParamPair, ...]:
    """把 dict / (名,值) 列表统一成参数对元组，保留重复参数与空值。"""
    if params is None:
        return ()
    pairs: List[ParamPair] = []
    if isinstance(params, Mapping):
        for name, value in params.items():
            if isinstance(value, str):
                pairs.append((name, value))
            else:  # 同名多值
                for single in value:
                    pairs.append((name, single))
    else:
        for name, value in params:
            pairs.append((name, value))
    for name, value in pairs:
        if not isinstance(name, str) or not isinstance(value, str):
            raise IssueError("参数名与值必须是 str")
        if name in RESERVED_PARAMS:
            raise IssueError(f"参数名 {name!r} 为保留字段")
    return tuple(pairs)


def _validate_resource(resource: str) -> None:
    if not resource or "\n" in resource or "\r" in resource:
        raise IssueError("resource 不能为空且不能包含换行符")


def canonical_query(pairs: Sequence[ParamPair]) -> str:
    """规范化查询串：percent-encode 后按 (名, 值) 排序，用 & 连接。

    确定性说明：输出只取决于参数多重集合，与输入顺序无关；
    空值编码为 ``a=``，与缺失参数 ``a`` 语义不同；重复参数全部保留。
    """
    encoded = sorted((quote(n, safe=""), quote(v, safe="")) for n, v in pairs)
    return "&".join(f"{n}={v}" for n, v in encoded)


def _signed_message(resource: str, not_before: int, expires_at: int,
                    pairs: Sequence[ParamPair]) -> bytes:
    """待签名的规范化载荷（版本前缀防跨协议混淆）。"""
    text = "\n".join([
        _VERSION,
        resource,
        str(int(not_before)),
        str(int(expires_at)),
        canonical_query(pairs),
    ])
    return text.encode("utf-8")


def _key_bytes(key: Union[str, bytes]) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else bytes(key)


def compute_signature(resource: str, params: ParamsInput, key: Union[str, bytes],
                      *, not_before: int, expires_at: int) -> str:
    """对 (资源, 时间窗, 参数) 计算 HMAC-SHA256 签名（hex）。"""
    pairs = _normalize_params(params)
    _validate_resource(resource)
    message = _signed_message(resource, not_before, expires_at, pairs)
    return hmac.new(_key_bytes(key), message, hashlib.sha256).hexdigest()


def build_url(resource: str, params: ParamsInput, key: Union[str, bytes], *,
              expires_at: int, not_before: int = 0, prefix: str = "") -> str:
    """签发完整签名链接：``prefix + resource?...&_nb=..&_exp=..&_sig=..``。

    同一 (resource, params, key, 时间窗) 的输出完全确定，与 params 书写顺序无关。
    """
    pairs = _normalize_params(params)
    signature = compute_signature(resource, pairs, key,
                                  not_before=not_before, expires_at=expires_at)
    query = canonical_query(pairs)
    trailer = f"_nb={int(not_before)}&_exp={int(expires_at)}&_sig={signature}"
    query = f"{query}&{trailer}" if query else trailer
    return f"{prefix}{resource}?{query}"


def issue(resource: str, params: ParamsInput, key: Union[str, bytes], *,
          ttl: int, not_before: Optional[int] = None,
          clock: Callable[[], float] = time.time, prefix: str = "") -> str:
    """便捷签发：以 clock() 为当前时间，not_before 默认为当前时刻。"""
    now = int(clock())
    nb = now if not_before is None else int(not_before)
    return build_url(resource, params, key,
                     expires_at=nb + int(ttl), not_before=nb, prefix=prefix)


@dataclass
class ParamDiff:
    """expected -> actual 的参数差异（多重集合语义）。"""
    added: List[ParamPair] = field(default_factory=list)    # actual 多出来的 (名,值)
    removed: List[ParamPair] = field(default_factory=list)  # actual 缺少的 (名,值)

    @property
    def changed_names(self) -> List[str]:
        """同名但取值集合发生变化的参数名。"""
        names = {n for n, _ in self.added} & {n for n, _ in self.removed}
        return sorted(names)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed)


def diff_params(expected: ParamsInput, actual: ParamsInput) -> ParamDiff:
    """比较两份参数（签发侧期望值 vs 实际收到的），给出增/删/改差异。"""
    exp = Counter(_normalize_params(expected))
    act = Counter(_normalize_params(actual))
    added = sorted((act - exp).elements())
    removed = sorted((exp - act).elements())
    return ParamDiff(added=added, removed=removed)


@dataclass
class VerificationResult:
    ok: bool
    reason: str = REASON_OK
    detail: str = ""
    resource: str = ""
    params: Tuple[ParamPair, ...] = ()
    not_before: Optional[int] = None
    expires_at: Optional[int] = None

    def diff_against(self, expected: ParamsInput) -> ParamDiff:
        """与签发侧期望参数对比，指出被增/删/改的具体参数。"""
        return diff_params(expected, self.params)


def verify(url: str, key: Union[str, bytes], *,
           clock: Callable[[], float] = time.time,
           skew: int = 0) -> VerificationResult:
    """校验签名链接。

    检查顺序：结构 -> 签名（恒定时间比较） -> 时间窗。
    时间窗为闭区间 [not_before - skew, expires_at + skew]，边界时刻视为有效。
    """
    split = urlsplit(url)
    resource = split.path
    raw_pairs = parse_qsl(split.query, keep_blank_values=True)

    sig_values: List[str] = []
    exp_values: List[str] = []
    nb_values: List[str] = []
    user_pairs: List[ParamPair] = []
    for name, value in raw_pairs:
        if name == "_sig":
            sig_values.append(value)
        elif name == "_exp":
            exp_values.append(value)
        elif name == "_nb":
            nb_values.append(value)
        else:
            user_pairs.append((name, value))

    if len(sig_values) != 1 or len(exp_values) != 1 or len(nb_values) > 1:
        return VerificationResult(False, REASON_MALFORMED,
                                  "缺少或重复 _sig/_exp/_nb 字段",
                                  resource=resource)
    try:
        expires_at = int(exp_values[0])
        not_before = int(nb_values[0]) if nb_values else 0
    except ValueError:
        return VerificationResult(False, REASON_MALFORMED,
                                  "_exp/_nb 不是整数",
                                  resource=resource)

    message = _signed_message(resource, not_before, expires_at, user_pairs)
    expected_sig = hmac.new(_key_bytes(key), message, hashlib.sha256).hexdigest()
    # 恒定时间比较：比较耗时与匹配前缀长度无关，避免逐字节计时侧信道。
    if not hmac.compare_digest(expected_sig, sig_values[0]):
        return VerificationResult(False, REASON_BAD_SIGNATURE,
                                  "签名不匹配：参数、资源标识或时间字段被增删改",
                                  resource=resource, params=tuple(user_pairs),
                                  not_before=not_before, expires_at=expires_at)

    now = int(clock())
    if now < not_before - skew:
        return VerificationResult(False, REASON_NOT_YET_VALID,
                                  f"当前 {now} 早于生效下界 {not_before - skew}",
                                  resource=resource, params=tuple(user_pairs),
                                  not_before=not_before, expires_at=expires_at)
    if now > expires_at + skew:
        return VerificationResult(False, REASON_EXPIRED,
                                  f"当前 {now} 晚于过期上界 {expires_at + skew}",
                                  resource=resource, params=tuple(user_pairs),
                                  not_before=not_before, expires_at=expires_at)

    return VerificationResult(True, REASON_OK, "",
                              resource=resource, params=tuple(user_pairs),
                              not_before=not_before, expires_at=expires_at)
