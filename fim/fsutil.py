"""Filesystem helpers shared by the baseline and fixed monitors."""
from __future__ import annotations

import hashlib
import os
import stat
from typing import Iterator, Optional, Tuple

_CHUNK = 65536


def rel_posix(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    return rel.replace(os.sep, "/")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def snapshot_stat(path: str) -> Tuple[int, int, int]:
    """Return (size, mtime_ns, mode bits)."""
    st = os.lstat(path)
    return st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode)


def walk_files(root: str) -> Iterator[str]:
    """Yield regular files under root, skipping symlinks (non-following)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode):
                yield full


def atomic_write(path: str, data: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def same_content_hash(path: str, digest: Optional[str]) -> bool:
    if digest is None:
        return False
    try:
        return sha256_file(path) == digest
    except OSError:
        return False
