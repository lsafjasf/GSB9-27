"""Self-test for the crash-consistency harness.

Runs three scenarios:

1. correct-single-file : write tmp -> sync -> rename.  Must pass.
2. buggy-single-file   : write tmp -> rename, the sync is "forgotten".
                         This is a real, classic crash-safety defect and
                         the harness MUST detect it deterministically.
3. two-file-transfer   : two files updated in order; the state where only
                         the first file is updated is an explicitly
                         declared, acceptable intermediate.  Must pass.

Exit code 0 iff the harness behaves as expected on all three.
"""

import sys

from crash_harness import CrashHarness, InjectingStorage, StorageBackend

OLD = b'{"balance": 100}'
NEW = b'{"balance": 42}'
DB = "db"
TMP = "db.tmp"


# ---------------------------------------------------------------- workloads

def correct_workload(storage: InjectingStorage) -> None:
    storage.write(TMP, NEW)
    storage.sync(TMP)          # data durable BEFORE the rename
    storage.rename(TMP, DB)


def buggy_workload(storage: InjectingStorage) -> None:
    # DEFECT (planted on purpose): the temp file is renamed over the
    # database without ever being synced.  A crash after the rename
    # leaves a durable but empty "db" -- neither old nor new.
    storage.write(TMP, NEW)
    storage.rename(TMP, DB)


A0, A1, B0, B1 = b"a:0", b"a:1", b"b:0", b"b:1"


def two_file_workload(storage: InjectingStorage) -> None:
    storage.write("a.tmp", A1)
    storage.sync("a.tmp")
    storage.rename("a.tmp", "a")
    storage.write("b.tmp", B1)
    storage.sync("b.tmp")
    storage.rename("b.tmp", "b")


# ---------------------------------------------------------------- recovery

def recover_single(backend: StorageBackend) -> None:
    # Startup recovery: discard a leftover temp file from a dead writer.
    if backend.exists(TMP):
        backend.remove(TMP)


def recover_two_file(backend: StorageBackend) -> None:
    for tmp in ("a.tmp", "b.tmp"):
        if backend.exists(tmp):
            backend.remove(tmp)


# ------------------------------------------------------------- classifiers

def classify_single(backend: StorageBackend) -> str:
    if not backend.exists(DB):
        return "missing"
    data = backend.read(DB)
    if data == OLD:
        return "old"
    if data == NEW:
        return "new"
    return f"corrupt({data!r})"


def classify_two_file(backend: StorageBackend) -> str:
    a, b = backend.read("a"), backend.read("b")
    if (a, b) == (A0, B0):
        return "old"
    if (a, b) == (A1, B1):
        return "new"
    if (a, b) == (A1, B0):
        return "partial"  # declared, acceptable intermediate state
    return f"torn(a={a!r}, b={b!r})"


# ------------------------------------------------------------------ driver

def run_scenarios():
    correct = CrashHarness(
        scenario="correct-single-file (write -> sync -> rename)",
        initial_files={DB: OLD},
        workload=correct_workload,
        recover=recover_single,
        classify=classify_single,
        allowed_states={"old", "new"},
    ).run()

    buggy = CrashHarness(
        scenario="buggy-single-file (write -> rename, NO sync)",
        initial_files={DB: OLD},
        workload=buggy_workload,
        recover=recover_single,
        classify=classify_single,
        allowed_states={"old", "new"},
    ).run()

    two_file = CrashHarness(
        scenario="two-file-transfer (declared intermediate state)",
        initial_files={"a": A0, "b": B0},
        workload=two_file_workload,
        recover=recover_two_file,
        classify=classify_two_file,
        allowed_states={"old", "new", "partial"},
    ).run()

    return correct, buggy, two_file


def main() -> int:
    correct, buggy, two_file = run_scenarios()
    for report in (correct, buggy, two_file):
        print(report.format())
        print()

    failures = []

    # The correct implementation must be fully clean.
    if not correct.ok:
        failures.append("correct implementation reported violations")

    # The planted bug MUST be detected, and specifically at the rename:
    # the rename is durable but the data was never synced.
    rename_violations = [
        o for o in buggy.violations if "rename" in o.label and o.state.startswith("corrupt")
    ]
    if not rename_violations:
        failures.append("planted no-sync bug was NOT detected")
    if buggy.ok:
        failures.append("buggy implementation unexpectedly passed")

    # The declared intermediate state must make the two-file update pass.
    if not two_file.ok:
        failures.append("two-file scenario with declared intermediate state failed")
    if not any(o.state == "partial" for o in two_file.outcomes):
        failures.append("intermediate state 'partial' was never exercised")

    print("=" * 60)
    if failures:
        for failure in failures:
            print(f"SELF-TEST FAILED: {failure}")
        return 1
    print(
        "SELF-TEST PASSED: harness caught the planted no-sync bug at "
        f"crash point(s) {[o.index for o in rename_violations]}, and "
        "cleared both correct implementations."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
