#!/usr/bin/env python3
"""LEGACY (buggy) retention cleanup -- kept ONLY to reproduce the mis-deletion.

Bug: it infers "this blob has been archived" from ``atime >= mtime``
("it was read after its last write, so some reader/archiver has it").
The archive task reads blobs, which bumps atime, so blobs that are still
referenced / still being archived suddenly look "archived" and get deleted.
Timestamp-based inference is the root cause; do not use this module.
"""

import os
import time


def find_deletable(data_dir, retention_seconds, now=None):
    now = time.time() if now is None else now
    doomed = []
    for name in sorted(os.listdir(data_dir)):
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        if now - st.st_mtime < retention_seconds:
            continue
        # BUG: "read since last write" is treated as "safely archived".
        if st.st_atime >= st.st_mtime:
            doomed.append(path)
    return doomed


def run(data_dir, retention_seconds, now=None):
    """Delete in place, no dry-run, no audit trail. Returns deleted paths."""
    deleted = []
    for path in find_deletable(data_dir, retention_seconds, now=now):
        os.remove(path)
        deleted.append(path)
    return deleted
