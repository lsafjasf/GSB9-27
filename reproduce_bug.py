#!/usr/bin/env python3
"""Reproduces the retention mis-deletion bug and verifies the fix.

The old cleaner decided by filesystem mtime: anything older than the cutoff
was unlinked by path.  Three scenarios show it deleting data that is still
in use, then show the fixed cleaner (storage_cleanup) keeping it:

  1. external time change  -- the archiver restores/touches a referenced
     blob, resetting its mtime to the distant past -> judged "expired".
  2. archive in progress   -- a blob being archived right now has an old
     mtime -> deleted from under the running archive task.
  3. concurrent write      -- a writer atomically replaces a blob between
     the cleaner's stat() and unlink() -> the fresh replacement is deleted.

Exit code 0 means: bug reproduced on the old logic AND the fixed logic kept
every blob it must keep.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from storage_cleanup import DataStore, dry_run, execute

NOW = 1_700_000_000.0
MIN_AGE = 3600.0
OLD = NOW - 10 * 86400  # 10 days ago: "expired" for the old cleaner


def buggy_cleanup(data_dir: Path, cutoff: float, on_before_unlink=None) -> list[str]:
    """The old, broken implementation: judge by mtime, unlink by path."""
    deleted = []
    for path in sorted(data_dir.glob("*.blob")):
        if path.stat().st_mtime < cutoff:
            if on_before_unlink:
                on_before_unlink(path)  # window where the race happens
            path.unlink()
            deleted.append(path.name)
    return deleted


def fresh_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="retention-repro-"))


def scenario_external_time_change() -> bool:
    """Referenced blob, mtime reset by the archiver -> old cleaner deletes it."""
    root = fresh_root()
    store = DataStore(root)
    store.write("report-42", b"quarterly report", now=NOW)
    store.add_ref("report-42", "analytics-service")
    # The archive task restores/touches the file, resetting mtime far back.
    os.utime(store.blob_path("report-42"), (OLD, OLD))

    deleted = buggy_cleanup(root / "data", cutoff=NOW - 7 * 86400)
    bug = "report-42.blob" in deleted

    store2root = fresh_root()
    store2 = DataStore(store2root)
    store2.write("report-42", b"quarterly report", now=NOW)
    store2.add_ref("report-42", "analytics-service")
    os.utime(store2.blob_path("report-42"), (OLD, OLD))  # same external touch
    plan, _ = dry_run(store2, now=NOW, min_age=MIN_AGE)
    execute(store2, plan, now=NOW)
    fixed_ok = store2.blob_path("report-42").exists()

    print(f"[1] external time change : buggy deleted referenced blob={bug}  "
          f"fixed kept it={fixed_ok}")
    shutil.rmtree(root); shutil.rmtree(store2root)
    return bug and fixed_ok


def scenario_archive_in_progress() -> bool:
    """Blob being archived right now -> old cleaner deletes it mid-archive."""
    root = fresh_root()
    store = DataStore(root)
    store.write("blob-7", b"payload", now=OLD)
    os.utime(store.blob_path("blob-7"), (OLD, OLD))  # old blob on disk
    store.start_archive("blob-7", task="nightly-archive", now=NOW - 60)

    deleted = buggy_cleanup(root / "data", cutoff=NOW - 7 * 86400)
    bug = "blob-7.blob" in deleted

    root2 = fresh_root()
    store2 = DataStore(root2)
    store2.write("blob-7", b"payload", now=OLD)
    store2.start_archive("blob-7", task="nightly-archive", now=NOW - 60)
    plan, _ = dry_run(store2, now=NOW, min_age=MIN_AGE)
    execute(store2, plan, now=NOW)
    fixed_ok = store2.blob_path("blob-7").exists()

    print(f"[2] archive in progress  : buggy deleted mid-archive blob={bug}  "
          f"fixed kept it={fixed_ok}")
    shutil.rmtree(root); shutil.rmtree(root2)
    return bug and fixed_ok


def scenario_concurrent_write() -> bool:
    """Writer replaces the blob between stat() and unlink() -> fresh copy deleted."""
    root = fresh_root()
    store = DataStore(root)
    store.write("hot", b"stale version", now=OLD)
    os.utime(store.blob_path("hot"), (OLD, OLD))  # old blob on disk

    def writer_replaces(path: Path) -> None:
        # Concurrent writer atomically publishes a fresh, in-use version.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(b"fresh version, still referenced")
        os.replace(tmp, path)

    deleted = buggy_cleanup(root / "data", cutoff=NOW - 7 * 86400,
                            on_before_unlink=writer_replaces)
    bug = "hot.blob" in deleted  # the *fresh* file was unlinked

    root2 = fresh_root()
    store2 = DataStore(root2)
    store2.write("hot", b"stale version", now=OLD)
    plan, _ = dry_run(store2, now=NOW, min_age=MIN_AGE)
    # Writer publishes a replacement after the plan, before execution.
    store2.write("hot", b"fresh version, still referenced", now=NOW)
    store2.add_ref("hot", "live-reader")
    execute(store2, plan, now=NOW)
    fixed_ok = (store2.blob_path("hot").exists()
                and store2.read("hot") == b"fresh version, still referenced")

    print(f"[3] concurrent write     : buggy deleted fresh replacement={bug}  "
          f"fixed kept it={fixed_ok}")
    shutil.rmtree(root); shutil.rmtree(root2)
    return bug and fixed_ok


def main() -> int:
    results = [
        scenario_external_time_change(),
        scenario_archive_in_progress(),
        scenario_concurrent_write(),
    ]
    if all(results):
        print("\nBug reproduced on the old mtime-based cleaner in all 3 scenarios;")
        print("the fixed cleaner kept the in-use data in every scenario.")
        return 0
    print("\nFAILED: bug not reproduced or fixed cleaner still loses data.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
