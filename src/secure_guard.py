"""修复后的沙箱路径校验。

原则：
1. 先解码、拒绝 NUL，再做任何判断 —— 校验与打开看到同一字符串。
2. 边界判定基于 os.path.realpath() 规范化后的真实路径（解析 .. 与所有
   符号链接），并用 commonpath 做带分隔符语义的包含判断，杜绝前缀混淆。
3. 判定与打开使用同一路径：打开的是 resolve() 返回的真实路径，而不是
   用户原始输入；打开时加 O_NOFOLLOW，并在打开后通过 /proc/self/fd
   （或 fstat 对比 dev+ino）复核 fd 确实落在沙箱内，缓解 TOCTOU。
"""

import os
import urllib.parse


class PathEscapeError(PermissionError):
    """路径逃逸出沙箱根目录。"""


class InvalidPathError(ValueError):
    """路径本身非法（NUL、过长、无法 stat 等）。"""


class SecurePathGuard:
    def __init__(self, root):
        # 根目录自身也规范化，避免调用方传入带 symlink/.. 的 root。
        self.root = os.path.realpath(root)
        if not os.path.isdir(self.root):
            raise InvalidPathError("sandbox root is not a directory: %r" % root)

    # ---- 内部工具 ------------------------------------------------------

    def _decode(self, user_path):
        if not isinstance(user_path, str):
            raise InvalidPathError("path must be str")
        if "\x00" in user_path:
            raise InvalidPathError("NUL byte in path")
        # 只解码一次，且在任何校验之前；此后不再变换字符串。
        decoded = urllib.parse.unquote(user_path)
        if "\x00" in decoded:
            raise InvalidPathError("NUL byte after URL decoding")
        return decoded

    def _within_root(self, real):
        try:
            return os.path.commonpath([self.root, real]) == self.root
        except ValueError:
            # 不同盘符/一绝对一相对等无法比较的情况，一律视为越界。
            return False

    # ---- 公开接口 ------------------------------------------------------

    def resolve(self, user_path):
        """把用户输入解析为沙箱内的真实路径；越界则抛 PathEscapeError。"""
        decoded = self._decode(user_path)
        if os.path.isabs(decoded):
            candidate = decoded
        else:
            candidate = os.path.join(self.root, decoded)
        try:
            real = os.path.realpath(candidate)
        except OSError as exc:  # 例如 ENAMETOOLONG
            raise InvalidPathError("cannot resolve path: %s" % exc)
        if not self._within_root(real):
            raise PathEscapeError(
                "path escapes sandbox: %r -> %r (root %r)"
                % (user_path, real, self.root)
            )
        return real

    def is_cross_mount(self, real):
        """真实路径与沙箱根是否位于不同挂载点（st_dev 不同）。"""
        try:
            return os.stat(real).st_dev != os.stat(self.root).st_dev
        except OSError:
            return False

    def open_file(self, user_path):
        """校验并打开文件，返回二进制文件对象。

        判定与打开使用同一个 realpath 结果；打开后复核 fd 的真实路径，
        防止检查与打开之间路径被替换（TOCTOU 缓解，非根除）。
        """
        real = self.resolve(user_path)

        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)   # 末段被换成 symlink 时直接失败
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(real, flags)
        except OSError as exc:
            # 不存在、过长、权限不足、末段是 symlink 等都统一抛 OSError 系异常
            raise exc

        try:
            self._verify_fd(fd, real)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb")

    def _verify_fd(self, fd, real):
        """打开后复核：fd 指向的对象必须仍在沙箱内且与判定路径一致。"""
        proc_fd = "/proc/self/fd/%d" % fd
        if os.path.exists("/proc/self/fd"):
            actual = os.readlink(proc_fd)
            actual = actual.removesuffix(" (deleted)")
            if not self._within_root(os.path.realpath(actual)):
                raise PathEscapeError(
                    "fd %d points outside sandbox: %r" % (fd, actual)
                )
        else:
            # 无 /proc 的平台：退化为 dev+ino 一致性比较。
            st_fd = os.fstat(fd)
            st_path = os.stat(real)
            if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
                raise PathEscapeError("opened file does not match checked path")
