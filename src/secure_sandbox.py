"""修复后的沙箱路径校验实现（仅标准库）。

安全原则：
1. 先解码、后校验：百分号编码在校验之前完成，校验看到的就是文件系统看到的路径。
2. 基于 realpath 的规范化真实路径做边界判定（解析所有符号链接与 ..）。
3. 边界判定带分隔符边界并做 normcase（兼容大小写不敏感文件系统）。
4. 判定与打开使用同一路径：打开的是解析后的真实路径，且用 dir_fd 逐分量
   O_NOFOLLOW 遍历，从根本上避免"检查后被替换"（TOCTOU）。
5. 可选的跨挂载点检测：比较目标与沙箱根的 st_dev。
"""

from __future__ import annotations

import os
import stat
import urllib.parse

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class SandboxError(Exception):
    """沙箱策略拒绝的基类。"""


class PathEscapeError(SandboxError):
    """解析后的真实路径越出沙箱根。"""


class InvalidPathError(SandboxError):
    """路径本身非法（NUL、非法编码、绝对路径、过长等）。"""


class NotARegularFileError(SandboxError):
    """目标存在但不是普通文件（如目录）。"""


def _decode(user_path: str) -> str:
    """输入清洗：拒绝 NUL，先做一次百分号解码。"""
    if not isinstance(user_path, str) or not user_path:
        raise InvalidPathError("empty or non-string path")
    if "\x00" in user_path:
        raise InvalidPathError("NUL byte in path")
    try:
        decoded = urllib.parse.unquote(user_path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidPathError(f"invalid percent-encoding: {exc}") from exc
    if "\x00" in decoded:
        raise InvalidPathError("NUL byte after decoding")
    return decoded


def _is_within(root_real: str, candidate_real: str) -> bool:
    """带分隔符边界与大小写规范的包含判定。"""
    root_c = os.path.normcase(root_real).rstrip(os.sep) + os.sep
    cand_c = os.path.normcase(candidate_real)
    return cand_c == root_c[:-1] or cand_c.startswith(root_c)


class Sandbox:
    """只读文件沙箱。root 之外的路径一律拒绝。"""

    def __init__(self, root: str, *, allow_mounts: bool = False):
        self.root_real = os.path.realpath(root)
        if not os.path.isdir(self.root_real):
            raise InvalidPathError(f"sandbox root is not a directory: {root!r}")
        self.allow_mounts = allow_mounts
        self.root_dev = os.stat(self.root_real).st_dev

    # ------------------------------------------------------------------ #
    # 路径解析与边界判定
    # ------------------------------------------------------------------ #
    def resolve(self, user_path: str) -> str:
        """把用户输入解析为沙箱内的真实路径；越界即抛 PathEscapeError。"""
        decoded = _decode(user_path)
        if os.path.isabs(decoded):
            raise InvalidPathError("absolute paths are not allowed")
        candidate = os.path.join(self.root_real, decoded)
        try:
            real = os.path.realpath(candidate)
        except OSError as exc:  # 路径过长、符号链接环等
            raise InvalidPathError(f"cannot resolve path: {exc}") from exc
        if not _is_within(self.root_real, real):
            raise PathEscapeError(
                f"resolved path {real!r} escapes sandbox root {self.root_real!r}"
            )
        if not self.allow_mounts:
            self._check_same_device(real)
        return real

    def _check_same_device(self, real: str) -> None:
        """跨挂载点检测：realpath 无法发现 bind/tmpfs 挂载，用 st_dev 兜底。

        目标不存在时向上找最近的存在祖先（挂载点本身必须存在）。
        """
        path = real
        while True:
            try:
                dev = os.stat(path).st_dev
            except FileNotFoundError:
                parent = os.path.dirname(path)
                if parent == path or not _is_within(self.root_real, parent):
                    return
                path = parent
                continue
            except OSError as exc:
                raise InvalidPathError(f"cannot stat path: {exc}") from exc
            if dev != self.root_dev:
                raise PathEscapeError(
                    f"path {real!r} crosses a mount point (st_dev differs)"
                )
            return

    # ------------------------------------------------------------------ #
    # 打开：与判定使用同一真实路径，dir_fd 逐分量遍历防 TOCTOU
    # ------------------------------------------------------------------ #
    def open(self, user_path: str):
        """以只读二进制模式打开沙箱内文件，返回文件对象。"""
        real = self.resolve(user_path)
        fd = self._open_beneath(real)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise NotARegularFileError(f"not a regular file: {user_path!r}")
            if not self.allow_mounts and st.st_dev != self.root_dev:
                raise PathEscapeError("opened file is on a different device")
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb")

    def _open_beneath(self, real: str) -> int:
        """从沙箱根的 fd 出发逐分量打开，每级都 O_NOFOLLOW。

        即使攻击者在 resolve() 之后替换了路径中的某个目录或符号链接，
        遍历也只会落在替换后的安全目标上或直接失败，不会逃出沙箱。
        平台不支持 O_NOFOLLOW 时退化为直接打开已解析的真实路径。
        """
        flags = os.O_RDONLY | _O_CLOEXEC
        if not _O_NOFOLLOW:
            return os.open(real, flags)

        rel = os.path.relpath(real, self.root_real)
        components = [c for c in rel.split(os.sep) if c not in ("", ".")]
        if not components:
            raise NotARegularFileError("path is the sandbox root directory")

        dir_flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
        try:
            dir_fd = os.open(self.root_real, dir_flags)
        except OSError as exc:
            raise InvalidPathError(f"cannot open sandbox root: {exc}") from exc
        try:
            for comp in components[:-1]:
                next_fd = os.open(comp, dir_flags, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = next_fd
            try:
                return os.open(components[-1], flags | _O_NOFOLLOW, dir_fd=dir_fd)
            except OSError as exc:
                # 解析后又被换成符号链接（竞态攻击）——防护生效，拒绝之
                if exc.errno == 40:  # ELOOP
                    raise PathEscapeError(
                        "symlink swapped in after validation (race detected)"
                    ) from exc
                raise
        finally:
            os.close(dir_fd)
