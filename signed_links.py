"""签名链接库（仅标准库）。

用途：为"临时授权下载链接"签发与校验签名。签名覆盖资源标识、
生效/过期时间与全部业务参数；任何参数增删改都会导致校验失败，
并可通过 diff_params() 指出差异。

设计要点
--------
1. 规范化（canonicalization）
   - 参数视为"名字 -> 值的多重集合"：顺序无关，重复值按多重集计。
   - 每个名字/值先 UTF-8 编码再 percent-encode（quote(s, safe='')），
     按 (编码后名字, 编码后值) 字典序排序，以 ``name=value`` 用 ``&`` 连接。
   - 查询串解析使用 keep_blank_values=True，因此 ``a`` 与 ``a=`` 等价
     （都表示空值），而 ``a=`` 与"没有 a"不等价。
   - 保留名 ``_sig`` / ``_nb`` / ``_exp`` 不允许出现在业务参数中。

2. 签名载荷（防止跨字段错位）
       b"v1\\n" + resource + "\\n" + not_before + "\\n" + expires + "\\n" + canonical_query
   使用 HMAC-SHA256，输出为无填充的 base64url 字符串。

3. 时间窗口（边界语义，skew 为可配置的时钟偏移容忍秒数，>= 0）
       有效  <=>  not_before - skew <= now < expires + skew
   即：下界闭、上界开；now == expires（skew=0）时视为已过期。
   skew 会同时向两侧放宽边界。

4. 恒定时间比较
   使用 hmac.compare_digest（C 实现、不提前退出）。校验前先把
   非 ASCII 的签名输入直接判负，避免长度/编码差异成为计时旁路。
   验证方式见 test_signed_links.py 中的两个测试：
   - test_uses_compare_digest：monkeypatch 证明库确实调用 compare_digest；
   - test_compare_timing_smoke：统计冒烟测试，比较"首位不同"与
     "末位不同"的签名耗时无显著差异（统计检验不能证明恒定时间，
     只能作为回归冒烟；根本保证来自 compare_digest 本身）。

5. 确定性
   同一 (密钥, resource, 参数多重集, not_before, expires) 必产生
   同一签名；参数顺序不影响结果；HMAC-SHA256 本身确定。

6. 长度限制（fail-closed）
   参数个数、名/值长度、规范化串总长均有上限；签发时抛
   ParamLimitError，校验时返回 reason='invalid_params'。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "ParamLimitError",
    "ReservedParamError",
    "ParamDiff",
    "VerifyResult",
    "SignedLink",
    "SignedLinkIssuer",
    "normalize_params",
    "diff_params",
]

ParamsInput = Union[
    Mapping[str, Union[str, Sequence[str]]],
    Sequence[Tuple[str, str]],
    str,
    None,
]

RESERVED_NAMES = ("_sig", "_nb", "_exp")
_PAYLOAD_VERSION = b"v1"


class ParamLimitError(ValueError):
    """参数超出大小/数量限制。"""


class ReservedParamError(ValueError):
    """业务参数使用了保留名。"""


def _encode(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def normalize_params(params: ParamsInput) -> List[Tuple[str, str]]:
    """把多种输入形式统一为 (name, value) 对列表（保留重复项）。

    支持：查询字符串、dict（值可为标量或序列）、(name, value) 对序列、None。
    """
    if params is None:
        return []
    if isinstance(params, str):
        query = params[1:] if params.startswith("?") else params
        return [
            (k, v)
            for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)
        ]
    if isinstance(params, Mapping):
        pairs: List[Tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, str):
                pairs.append((key, value))
            elif isinstance(value, Sequence):
                for item in value:
                    pairs.append((key, item))
            else:
                pairs.append((key, str(value)))
        return pairs
    return [(k, v) for k, v in params]


@dataclass
class ParamDiff:
    """expected -> actual 的参数差异（按多重集比较）。"""

    added: Dict[str, List[str]] = field(default_factory=dict)
    removed: Dict[str, List[str]] = field(default_factory=dict)
    changed: Dict[str, Dict[str, List[str]]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def describe(self) -> str:
        if not self:
            return "无差异"
        parts = []
        for name, values in sorted(self.added.items()):
            parts.append(f"新增参数 {name}={values}")
        for name, values in sorted(self.removed.items()):
            parts.append(f"删除参数 {name}={values}")
        for name, change in sorted(self.changed.items()):
            parts.append(
                f"修改参数 {name}: 期望 {change['expected']} 实际 {change['actual']}"
            )
        return "; ".join(parts)


def _to_multiset(pairs: Sequence[Tuple[str, str]]) -> Dict[str, Counter]:
    multiset: Dict[str, Counter] = {}
    for name, value in pairs:
        multiset.setdefault(name, Counter())[value] += 1
    return multiset


def diff_params(expected: ParamsInput, actual: ParamsInput) -> ParamDiff:
    """比较两份参数，返回增/删/改差异（用于定位篡改点）。"""
    exp = _to_multiset(normalize_params(expected))
    act = _to_multiset(normalize_params(actual))
    diff = ParamDiff()
    for name in sorted(act.keys() - exp.keys()):
        diff.added[name] = sorted(act[name].elements())
    for name in sorted(exp.keys() - act.keys()):
        diff.removed[name] = sorted(exp[name].elements())
    for name in sorted(exp.keys() & act.keys()):
        if exp[name] != act[name]:
            diff.changed[name] = {
                "expected": sorted(exp[name].elements()),
                "actual": sorted(act[name].elements()),
            }
    return diff


@dataclass
class VerifyResult:
    ok: bool
    reason: Optional[str] = None  # bad_signature | not_yet_valid | expired | invalid_params
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class SignedLink:
    resource: str
    params: List[Tuple[str, str]]
    not_before: int
    expires: int
    signature: str

    def query(self) -> str:
        """生成完整查询串（业务参数 + _nb/_exp/_sig）。"""
        pairs = list(self.params)
        pairs.append(("_nb", str(self.not_before)))
        pairs.append(("_exp", str(self.expires)))
        pairs.append(("_sig", self.signature))
        return urllib.parse.urlencode(pairs)


class SignedLinkIssuer:
    def __init__(
        self,
        secret: bytes,
        *,
        clock: Callable[[], float] = time.time,
        clock_skew: float = 0.0,
        max_params: int = 64,
        max_name_len: int = 128,
        max_value_len: int = 2048,
        max_resource_len: int = 1024,
        max_canonical_len: int = 16384,
    ) -> None:
        if not secret:
            raise ValueError("secret 不能为空")
        if clock_skew < 0:
            raise ValueError("clock_skew 必须 >= 0")
        self._secret = bytes(secret)
        self._clock = clock
        self._skew = float(clock_skew)
        self._max_params = max_params
        self._max_name_len = max_name_len
        self._max_value_len = max_value_len
        self._max_resource_len = max_resource_len
        self._max_canonical_len = max_canonical_len

    # ---- 内部 ----

    def _canonical_query(self, pairs: Sequence[Tuple[str, str]]) -> str:
        if len(pairs) > self._max_params:
            raise ParamLimitError(
                f"参数个数 {len(pairs)} 超过上限 {self._max_params}"
            )
        encoded = []
        for name, value in pairs:
            if name in RESERVED_NAMES:
                raise ReservedParamError(f"参数名 {name!r} 为保留名")
            if len(name) > self._max_name_len:
                raise ParamLimitError(f"参数名过长: {name[:32]!r}...")
            if len(value) > self._max_value_len:
                raise ParamLimitError(f"参数 {name!r} 的值过长")
            encoded.append((_encode(name), _encode(value)))
        encoded.sort()
        return "&".join(f"{k}={v}" for k, v in encoded)

    def _payload(
        self,
        resource: str,
        canonical_query: str,
        not_before: int,
        expires: int,
    ) -> bytes:
        if len(resource) > self._max_resource_len:
            raise ParamLimitError("资源标识过长")
        body = "\n".join(
            [resource, str(not_before), str(expires), canonical_query]
        ).encode("utf-8")
        payload = _PAYLOAD_VERSION + b"\n" + body
        if len(payload) > self._max_canonical_len:
            raise ParamLimitError("规范化载荷过长")
        return payload

    def _sign_payload(self, payload: bytes) -> str:
        digest = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    # ---- 签发 ----

    def sign(
        self,
        resource: str,
        params: ParamsInput = None,
        *,
        expires: int,
        not_before: Optional[int] = None,
    ) -> SignedLink:
        """签发签名链接。expires 必填（临时授权）；not_before 缺省为 0。"""
        nb = 0 if not_before is None else int(not_before)
        exp = int(expires)
        if exp <= nb:
            raise ValueError("expires 必须大于 not_before")
        pairs = normalize_params(params)
        canonical = self._canonical_query(pairs)
        payload = self._payload(resource, canonical, nb, exp)
        return SignedLink(
            resource=resource,
            params=pairs,
            not_before=nb,
            expires=exp,
            signature=self._sign_payload(payload),
        )

    # ---- 校验 ----

    def verify(
        self,
        resource: str,
        params: ParamsInput,
        *,
        not_before: int,
        expires: int,
        signature: str,
        now: Optional[float] = None,
    ) -> VerifyResult:
        """校验签名与时间窗口。先验签、后验时间，篡改与过期可区分。"""
        try:
            pairs = normalize_params(params)
            canonical = self._canonical_query(pairs)
            payload = self._payload(resource, canonical, int(not_before), int(expires))
        except (ParamLimitError, ReservedParamError, ValueError) as exc:
            return VerifyResult(False, "invalid_params", str(exc))

        expected = self._sign_payload(payload)
        try:
            supplied = signature.encode("ascii")
        except (UnicodeEncodeError, AttributeError):
            return VerifyResult(False, "bad_signature", "签名编码非法")
        if not hmac.compare_digest(expected.encode("ascii"), supplied):
            return VerifyResult(False, "bad_signature", "签名不匹配（参数/资源/时间被改动）")

        current = self._clock() if now is None else now
        if current < not_before - self._skew:
            return VerifyResult(
                False, "not_yet_valid", f"链接尚未生效（not_before={not_before}）"
            )
        if current >= expires + self._skew:
            return VerifyResult(False, "expired", f"链接已过期（expires={expires}）")
        return VerifyResult(True)

    def verify_query(
        self,
        resource: str,
        query_string: str,
        *,
        now: Optional[float] = None,
    ) -> VerifyResult:
        """从完整查询串中拆出 _nb/_exp/_sig 并校验其余参数。"""
        pairs = normalize_params(query_string)
        meta: Dict[str, str] = {}
        business: List[Tuple[str, str]] = []
        for name, value in pairs:
            if name in RESERVED_NAMES:
                if name in meta:
                    return VerifyResult(False, "invalid_params", f"保留参数 {name} 重复")
                meta[name] = value
            else:
                business.append((name, value))
        if set(meta) != set(RESERVED_NAMES):
            missing = sorted(set(RESERVED_NAMES) - set(meta))
            return VerifyResult(False, "invalid_params", f"缺少签名元参数: {missing}")
        try:
            not_before = int(meta["_nb"])
            expires = int(meta["_exp"])
        except ValueError:
            return VerifyResult(False, "invalid_params", "_nb/_exp 不是整数")
        return self.verify(
            resource,
            business,
            not_before=not_before,
            expires=expires,
            signature=meta["_sig"],
            now=now,
        )
