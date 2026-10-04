"""scope_check — 令牌作用域（scope）校验库，仅依赖 Python 3 标准库。

核心思想：令牌“没过期”只是必要条件，不是充分条件。每次访问都必须
逐项判定所需作用域是否被令牌持有的作用域覆盖，并且判定必须读取
注册中心的实时状态（吊销立即生效，不做缓存）。

作用域模型
----------
作用域为以冒号分隔的分层字符串::

    <action>:<resource_type>[:<resource_id>]

例如 ``read:invoice``、``write:invoice:inv-1001``、``admin:*``。

匹配规则（覆盖判定 covers(granted, required)）
----------------------------------------------
R1  逐段比较，段数必须相等（不存在跨层级的“递归通配”）。
R2  授权段为 "*" 时覆盖所需段的任意值（通配可出现在任意段位置）。
R3  授权段非 "*" 时必须与所需段完全相等（区分大小写）。
R4  资源级限定通过第三段 resource_id 表达：令牌持有
    ``read:invoice:inv-1001`` 只能读 inv-1001，读 inv-1002 会被拒绝。
R5  多项所需作用域之间是 AND 语义：全部覆盖才放行，缺一即拒。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

WILDCARD = "*"


class ScopeError(ValueError):
    """作用域字符串非法。"""


def parse_scope(scope: str) -> Tuple[str, ...]:
    """解析并校验作用域字符串，返回段元组。非法时抛 ScopeError。"""
    if not isinstance(scope, str):
        raise ScopeError(f"scope 必须是字符串: {scope!r}")
    parts = tuple(scope.split(":"))
    if any(part == "" for part in parts):
        raise ScopeError(f"scope 含空段: {scope!r}")
    return parts


def make_scope(action: str, resource_type: str, resource_id: Optional[str] = None) -> str:
    """构造作用域字符串；resource_id 为 None 表示不限定具体资源。"""
    parts = [action, resource_type]
    if resource_id is not None:
        parts.append(resource_id)
    scope = ":".join(parts)
    parse_scope(scope)  # 复用校验，空段（如空字符串 id）会被拒绝
    return scope


def covers(granted: str, required: str) -> bool:
    """判定单个授权作用域是否覆盖单个所需作用域（规则 R1–R3）。"""
    granted_parts = parse_scope(granted)
    required_parts = parse_scope(required)
    if len(granted_parts) != len(required_parts):  # R1
        return False
    return all(g == WILDCARD or g == r for g, r in zip(granted_parts, required_parts))  # R2/R3


def missing_scopes(granted: Sequence[str], required: Sequence[str]) -> List[str]:
    """返回未被覆盖的所需作用域列表（R5：逐项判定，AND 语义）。"""
    granted_list = list(granted)
    return [req for req in required if not any(covers(g, req) for g in granted_list)]


@dataclass
class Token:
    """不透明令牌。授权判定不解析令牌内容，只通过 token_id 查注册中心。"""

    token_id: str
    subject: str
    scopes: Tuple[str, ...]
    expires_at: Optional[float] = None  # Unix 时间戳；None 表示不过期
    revoked: bool = False

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) >= self.expires_at


@dataclass
class Decision:
    """一次授权判定的结构化记录（拒绝/放行均留痕）。"""

    allowed: bool
    reason: str  # ok / token_unknown / token_revoked / token_expired / scope_missing
    token_id: Optional[str]
    subject: Optional[str]
    required: Tuple[str, ...]
    missing: Tuple[str, ...] = ()
    matched_by: Dict[str, str] = field(default_factory=dict)  # required -> 覆盖它的 granted
    timestamp: float = field(default_factory=time.time)


class TokenRegistry:
    """令牌注册中心：签发、吊销、授权判定。

    authorize() 每次都读取注册中心的实时状态，因此 revoke() 之后
    下一次判定立即失效，无任何缓存延迟。
    """

    def __init__(self) -> None:
        self._tokens: Dict[str, Token] = {}
        self.decision_log: List[Decision] = []

    def issue(
        self,
        subject: str,
        scopes: Sequence[str],
        ttl_seconds: Optional[float] = None,
    ) -> Token:
        for scope in scopes:
            parse_scope(scope)
        token = Token(
            token_id=uuid.uuid4().hex,
            subject=subject,
            scopes=tuple(scopes),
            expires_at=None if ttl_seconds is None else time.time() + ttl_seconds,
        )
        self._tokens[token.token_id] = token
        return token

    def revoke(self, token_id: str) -> bool:
        """吊销令牌。成功返回 True；令牌不存在返回 False。"""
        token = self._tokens.get(token_id)
        if token is None:
            return False
        token.revoked = True
        return True

    def authorize(
        self,
        token_id: str,
        required: Sequence[str],
        now: Optional[float] = None,
    ) -> Decision:
        """判定 token_id 是否具备全部 required 作用域，并记录决策日志。"""
        required = tuple(required)
        token = self._tokens.get(token_id)

        if token is None:
            return self._record(False, "token_unknown", token_id, None, required)
        if token.revoked:
            return self._record(False, "token_revoked", token_id, token.subject, required)
        if token.is_expired(now):
            return self._record(False, "token_expired", token_id, token.subject, required)

        missing = missing_scopes(token.scopes, required)
        if missing:
            return self._record(
                False, "scope_missing", token_id, token.subject, required, missing=missing
            )

        matched_by = {
            req: next(g for g in token.scopes if covers(g, req)) for req in required
        }
        return self._record(
            True, "ok", token_id, token.subject, required, matched_by=matched_by
        )

    def _record(
        self,
        allowed: bool,
        reason: str,
        token_id: Optional[str],
        subject: Optional[str],
        required: Tuple[str, ...],
        missing: Sequence[str] = (),
        matched_by: Optional[Dict[str, str]] = None,
    ) -> Decision:
        decision = Decision(
            allowed=allowed,
            reason=reason,
            token_id=token_id,
            subject=subject,
            required=required,
            missing=tuple(missing),
            matched_by=matched_by or {},
        )
        self.decision_log.append(decision)
        return decision
