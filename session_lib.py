"""会话标识轮换与校验库（仅标准库）。

防御会话固定（session fixation）攻击：
- 会话标识一律由服务端用 secrets 生成，客户端无法自选。
- 权限提升（登录）时原子地轮换标识：新标识生效与旧标识失效在同一把锁内完成。
- 登出后标识立即失效，重放即被拒绝。
- 全程记录审计日志：轮换记录（旧->新对照）、拒绝记录、登出记录。
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Session:
    sid: str
    user_id: Optional[str] = None          # None 表示匿名会话
    created_at: float = field(default_factory=time.time)
    authenticated_at: Optional[float] = None

    @property
    def authenticated(self) -> bool:
        return self.user_id is not None


class SessionError(Exception):
    """会话操作非法（如用无效标识提权）。"""


class SessionManager:
    """线程安全的会话管理器。"""

    def __init__(self, token_bytes: int = 32):
        if token_bytes < 16:
            raise ValueError("token_bytes 至少为 16（128 位熵）")
        self._token_bytes = token_bytes
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        # 审计日志
        self.rotation_log: list[dict] = []    # 轮换记录：旧标识 -> 新标识
        self.rejection_log: list[dict] = []   # 拒绝记录：无效/失效标识的访问
        self.logout_log: list[dict] = []      # 登出记录

    # ---- 内部工具 ----

    def _new_sid(self) -> str:
        while True:
            sid = secrets.token_urlsafe(self._token_bytes)
            if sid not in self._sessions:      # 防碰撞（理论上不会发生）
                return sid

    def _record_rejection(self, sid: Optional[str], reason: str) -> None:
        self.rejection_log.append({
            "ts": time.time(),
            "sid": sid,
            "reason": reason,
            "thread": threading.current_thread().name,
        })

    # ---- 公开接口 ----

    def create_session(self) -> str:
        """建立新（匿名）会话，返回服务端生成的标识。"""
        with self._lock:
            sid = self._new_sid()
            self._sessions[sid] = Session(sid=sid)
            return sid

    def validate(self, sid: Optional[str]) -> Optional[Session]:
        """校验标识。有效返回 Session，无效返回 None 并记录拒绝日志。"""
        with self._lock:
            if not sid:
                self._record_rejection(sid, "empty-or-missing-sid")
                return None
            session = self._sessions.get(sid)
            if session is None:
                self._record_rejection(sid, "unknown-or-invalidated-sid")
                return None
            return session

    def elevate(self, sid: Optional[str], user_id: str) -> str:
        """登录提权：原子地轮换标识。

        在同一把锁内完成：作废旧标识 -> 签发新标识 -> 绑定用户身份。
        旧标识从此刻起必然被 validate 拒绝。
        """
        if not user_id:
            raise SessionError("user_id 不能为空")
        with self._lock:
            old = self._sessions.pop(sid, None) if sid else None
            if old is None:
                self._record_rejection(sid, "elevate-with-invalid-sid")
                raise SessionError("无法提权：会话标识无效或已失效")
            new_sid = self._new_sid()
            self._sessions[new_sid] = Session(
                sid=new_sid,
                user_id=user_id,
                created_at=old.created_at,
                authenticated_at=time.time(),
            )
            self.rotation_log.append({
                "ts": time.time(),
                "event": "privilege-escalation-rotation",
                "old_sid": sid,
                "new_sid": new_sid,
                "user_id": user_id,
            })
            return new_sid

    def logout(self, sid: Optional[str]) -> bool:
        """登出：立即作废标识。重复登出视为无效访问并记录。"""
        with self._lock:
            session = self._sessions.pop(sid, None) if sid else None
            if session is None:
                self._record_rejection(sid, "logout-with-invalid-sid")
                return False
            self.logout_log.append({
                "ts": time.time(),
                "sid": sid,
                "user_id": session.user_id,
            })
            return True
