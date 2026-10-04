"""卷引用计数与租约回收管理（仅依赖 Python 标准库）。

核心模型
--------
* 每个卷维护一份持有者表 ``holder_id -> 租约到期时间``，引用计数即持有者数量。
* ``mount`` / ``unmount`` 按 (卷, 使用者) 幂等：同一使用者重复挂载不会让计数 +1。
* 每次挂载携带租约（TTL）。使用者必须周期性 ``renew``；异常退出无法续期时，
  租约到期由回收器 ``reap_expired`` 回收，引用计数 -1。
* 引用计数归零后卷才允许删除，避免“判断错了就是数据丢失”。

为便于确定性自测，时间通过可注入的 ``clock`` 获取（默认 ``time.monotonic``）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

__all__ = [
    "MountRecord",
    "ReclaimRecord",
    "VolumeManager",
    "VolumeError",
    "VolumeNotFound",
    "VolumeBusy",
    "NotMounted",
    "LeaseExpired",
]


class VolumeError(Exception):
    """卷管理相关错误的基类。"""


class VolumeNotFound(VolumeError):
    """操作的卷不存在。"""


class VolumeBusy(VolumeError):
    """卷引用计数大于 0，禁止删除。"""


class NotMounted(VolumeError):
    """使用者并未挂载该卷却发起卸载。"""


class LeaseExpired(VolumeError):
    """租约已超时（或已被回收），必须重新挂载。"""


@dataclass(frozen=True)
class MountRecord:
    """一次挂载操作的结果快照。"""

    volume_id: str
    holder_id: str
    refcount: int          # 操作后该卷的引用计数
    duplicate: bool        # 是否为同一使用者的重复挂载（计数未增加）
    lease_deadline: float  # 租约到期时间点（clock 时间）


@dataclass(frozen=True)
class ReclaimRecord:
    """一次租约超时回收事件。"""

    volume_id: str
    holder_id: str
    refcount_after: int    # 回收后该卷的引用计数
    reclaimed_at: float    # 回收发生时间点（clock 时间）


class _Volume:
    __slots__ = ("holders",)

    def __init__(self) -> None:
        # holder_id -> 租约到期时间
        self.holders: dict[str, float] = {}


class VolumeManager:
    """卷引用计数 + 租约超时回收管理器（线程内单例使用，非线程安全）。"""

    def __init__(self, lease_ttl: float = 30.0, clock=time.monotonic) -> None:
        if lease_ttl <= 0:
            raise ValueError("lease_ttl 必须为正数")
        self._lease_ttl = float(lease_ttl)
        self._clock = clock
        self._volumes: dict[str, _Volume] = {}

    @property
    def lease_ttl(self) -> float:
        return self._lease_ttl

    # ---- 卷生命周期 -----------------------------------------------------

    def create_volume(self, volume_id: str) -> None:
        self._require_id("volume_id", volume_id)
        if volume_id in self._volumes:
            raise VolumeError(f"卷已存在: {volume_id}")
        self._volumes[volume_id] = _Volume()

    def delete_volume(self, volume_id: str) -> None:
        """仅当引用计数为 0（且过期租约已回收）时才允许删除。"""
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        if vol.holders:
            raise VolumeBusy(
                f"卷 {volume_id} 仍有 {len(vol.holders)} 个引用，禁止删除"
            )
        del self._volumes[volume_id]

    def exists(self, volume_id: str) -> bool:
        return volume_id in self._volumes

    # ---- 挂载 / 卸载 ---------------------------------------------------

    def mount(self, volume_id: str, holder_id: str) -> MountRecord:
        """挂载卷。同一使用者重复挂载幂等：计数不变，仅刷新租约。"""
        self._require_id("holder_id", holder_id)
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        duplicate = holder_id in vol.holders
        deadline = self._clock() + self._lease_ttl
        vol.holders[holder_id] = deadline
        return MountRecord(
            volume_id=volume_id,
            holder_id=holder_id,
            refcount=len(vol.holders),
            duplicate=duplicate,
            lease_deadline=deadline,
        )

    def unmount(self, volume_id: str, holder_id: str) -> int:
        """主动卸载，返回卸载后的引用计数。"""
        self._require_id("holder_id", holder_id)
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        if holder_id not in vol.holders:
            raise NotMounted(
                f"使用者 {holder_id} 未挂载卷 {volume_id}（可能已被超时回收）"
            )
        del vol.holders[holder_id]
        return len(vol.holders)

    def renew(self, volume_id: str, holder_id: str) -> float:
        """租约续期，返回新的到期时间；租约已超时则拒绝，要求重新挂载。"""
        self._require_id("holder_id", holder_id)
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        if holder_id not in vol.holders:
            raise LeaseExpired(
                f"使用者 {holder_id} 对卷 {volume_id} 的租约已失效，请重新挂载"
            )
        deadline = self._clock() + self._lease_ttl
        vol.holders[holder_id] = deadline
        return deadline

    # ---- 查询与回收 -----------------------------------------------------

    def refcount(self, volume_id: str) -> int:
        """当前有效引用计数（查询时顺带惰性回收已过期租约）。"""
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        return len(vol.holders)

    def holders(self, volume_id: str) -> list[str]:
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        return sorted(vol.holders)

    def lease_deadline(self, volume_id: str, holder_id: str) -> float:
        vol = self._get(volume_id)
        self._reap(volume_id, vol)
        if holder_id not in vol.holders:
            raise NotMounted(f"使用者 {holder_id} 未挂载卷 {volume_id}")
        return vol.holders[holder_id]

    def reap_expired(self) -> list[ReclaimRecord]:
        """回收器周期调用：扫描所有卷，回收全部已过期租约。"""
        reclaimed: list[ReclaimRecord] = []
        for volume_id, vol in list(self._volumes.items()):
            reclaimed.extend(self._reap(volume_id, vol))
        return reclaimed

    # ---- 内部实现 -------------------------------------------------------

    def _get(self, volume_id: str) -> _Volume:
        self._require_id("volume_id", volume_id)
        try:
            return self._volumes[volume_id]
        except KeyError:
            raise VolumeNotFound(f"卷不存在: {volume_id}") from None

    def _reap(self, volume_id: str, vol: _Volume) -> list[ReclaimRecord]:
        now = self._clock()
        expired = [h for h, deadline in vol.holders.items() if deadline <= now]
        records: list[ReclaimRecord] = []
        for holder_id in expired:
            del vol.holders[holder_id]
            records.append(
                ReclaimRecord(
                    volume_id=volume_id,
                    holder_id=holder_id,
                    refcount_after=len(vol.holders),
                    reclaimed_at=now,
                )
            )
        return records

    @staticmethod
    def _require_id(name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} 必须是非空字符串")
