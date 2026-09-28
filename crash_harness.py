"""Crash-consistency test harness (Python 3, standard library only).

The harness systematically tests *every* crash point of a storage workload
instead of spot-checking one.  It works in three passes:

1. Enumeration -- the workload runs once against an instrumented storage
   facade which records a crash point before and after every mutating
   primitive (write / sync / rename / mkdir / remove).
2. Injection -- for each enumerated point the workload is replayed from a
   fresh disk and the power is "cut" exactly at that point.
3. Verification -- the implementation's recovery procedure runs, the
   recovered durable state is classified, and the resulting tag must be
   one of the caller-declared acceptable states (e.g. "old", "new", or an
   explicitly named intermediate state).  Anything else is a violation.
   There is deliberately no "any state passes" mode.

Durability model (deterministic, documented; models a metadata-journaling
filesystem with a volatile page cache):

* write()  -- data lands in the page cache only; lost on crash unless
              sync() is called for that file afterwards.
* sync()   -- makes the file's cached data durable.
* rename() / mkdir() / remove() -- namespace operations are durable
              immediately.  Renaming a file whose data was never synced
              leaves a durable but empty (zero-length) file, mirroring the
              classic ext4 delayed-allocation failure mode.

Determinism requirement: the workload, recovery and classifier must be
deterministic functions of the storage state, because each crash point is
tested by replaying the workload from scratch.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set


class CrashInjected(Exception):
    """Raised inside the workload at the moment the harness cuts power."""


class SimulatedDisk:
    """The durable truth: only what lives here survives a power cut."""

    def __init__(
        self,
        files: Optional[Dict[str, bytes]] = None,
        dirs: Optional[Set[str]] = None,
    ) -> None:
        self.files: Dict[str, bytes] = dict(files or {})
        self.dirs: Set[str] = set(dirs or {"/"})

    def clone(self) -> "SimulatedDisk":
        return SimulatedDisk(self.files, self.dirs)

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.dirs):
            digest.update(b"D\0" + path.encode() + b"\0")
        for path in sorted(self.files):
            digest.update(b"F\0" + path.encode() + b"\0")
            digest.update(hashlib.sha256(self.files[path]).digest())
        return digest.hexdigest()


class StorageBackend:
    """POSIX-ish storage with the durability model described above.

    This is the injectable storage layer: code under test talks to it
    (through InjectingStorage), and tests can subclass or replace it as
    long as the same method surface is provided.
    """

    def __init__(self, disk: SimulatedDisk) -> None:
        self._disk = disk
        self._cache: Dict[str, bytes] = {}  # volatile page cache

    # -- mutating primitives (crash points are generated around these) --

    def write(self, path: str, data: bytes) -> None:
        self._cache[path] = bytes(data)

    def sync(self, path: str) -> None:
        if path in self._cache:
            self._disk.files[path] = self._cache[path]
        elif path not in self._disk.files:
            raise FileNotFoundError(path)

    def rename(self, src: str, dst: str) -> None:
        if src not in self._cache and src not in self._disk.files:
            raise FileNotFoundError(src)
        cached = self._cache.pop(src, None)
        if cached is not None:
            self._cache[dst] = cached
        else:
            self._cache.pop(dst, None)
        if src in self._disk.files:
            self._disk.files[dst] = self._disk.files.pop(src)
        else:
            # Inode exists but no data was ever flushed: durable file is
            # zero-length even though reads before the crash saw data.
            self._disk.files[dst] = b""

    def mkdir(self, path: str) -> None:
        self._disk.dirs.add(path)

    def remove(self, path: str) -> None:
        if path not in self._cache and path not in self._disk.files:
            raise FileNotFoundError(path)
        self._cache.pop(path, None)
        self._disk.files.pop(path, None)

    # -- read-only helpers (no crash points) --

    def read(self, path: str) -> bytes:
        if path in self._cache:
            return self._cache[path]
        if path in self._disk.files:
            return self._disk.files[path]
        raise FileNotFoundError(path)

    def exists(self, path: str) -> bool:
        return path in self._cache or path in self._disk.files

    # -- harness hooks --

    def crash(self) -> None:
        """Simulate power loss: everything volatile evaporates."""
        self._cache.clear()

    def durable_fingerprint(self) -> str:
        return self._disk.fingerprint()


class InjectingStorage:
    """Storage facade seen by the code under test.

    Wraps every mutating primitive with a "before" and an "after" crash
    point.  With crash_at=None the points are merely enumerated; with
    crash_at=i the i-th point raises CrashInjected instead of proceeding.
    """

    def __init__(self, backend: StorageBackend, crash_at: Optional[int] = None) -> None:
        self._backend = backend
        self._crash_at = crash_at
        self._next = 0
        self.points: List[str] = []

    def _hit(self, label: str) -> None:
        index = self._next
        self._next += 1
        if self._crash_at is None:
            self.points.append(label)
        elif index == self._crash_at:
            raise CrashInjected(label)

    def _op(self, label: str, fn: Callable, *args) -> None:
        self._hit(f"before:{label}")
        fn(*args)
        self._hit(f"after:{label}")

    def write(self, path: str, data: bytes) -> None:
        self._op(f"write({path})", self._backend.write, path, data)

    def sync(self, path: str) -> None:
        self._op(f"sync({path})", self._backend.sync, path)

    def rename(self, src: str, dst: str) -> None:
        self._op(f"rename({src}->{dst})", self._backend.rename, src, dst)

    def mkdir(self, path: str) -> None:
        self._op(f"mkdir({path})", self._backend.mkdir, path)

    def remove(self, path: str) -> None:
        self._op(f"remove({path})", self._backend.remove, path)

    # Read-only passthroughs: no crash points.
    def read(self, path: str) -> bytes:
        return self._backend.read(path)

    def exists(self, path: str) -> bool:
        return self._backend.exists(path)


@dataclass
class CrashPointOutcome:
    index: int
    label: str
    status: str          # "pass" | "violation" | "skipped"
    state: str = ""      # classifier tag of the recovered state
    detail: str = ""


@dataclass
class CoverageReport:
    scenario: str
    outcomes: List[CrashPointOutcome]
    allowed_states: Set[str]
    commit_state: str = ""        # classifier tag for a clean, crash-free run
    commit_ok: bool = True

    @property
    def enumerated(self) -> int:
        return len(self.outcomes)

    @property
    def tested(self) -> int:
        return sum(1 for o in self.outcomes if o.status != "skipped")

    @property
    def skipped(self) -> List[CrashPointOutcome]:
        return [o for o in self.outcomes if o.status == "skipped"]

    @property
    def violations(self) -> List[CrashPointOutcome]:
        return [o for o in self.outcomes if o.status == "violation"]

    @property
    def ok(self) -> bool:
        return not self.violations and self.commit_ok

    def format(self) -> str:
        lines = [
            f"=== Scenario: {self.scenario} ===",
            f"crash points enumerated : {self.enumerated}",
            f"tested                  : {self.tested}",
            f"skipped                 : {len(self.skipped)}",
        ]
        for o in self.skipped:
            lines.append(f"  - point {o.index} [{o.label}]: {o.detail}")
        lines.append(f"violations              : {len(self.violations)}")
        for o in self.violations:
            lines.append(f"  ! point {o.index} [{o.label}]: {o.detail}")
        commit = "ok" if self.commit_ok else "UNEXPECTED"
        lines.append(f"crash-free commit state : {self.commit_state!r} ({commit})")
        for o in self.outcomes:
            if o.status == "pass":
                lines.append(f"  . point {o.index} [{o.label}] -> {o.state}")
        return "\n".join(lines)


class CrashHarness:
    """Enumerates crash points of a workload and tests every one of them.

    Parameters
    ----------
    initial_files:
        Durable files present before the workload (the "old" state).
    workload:
        fn(InjectingStorage) performing one logical transaction.
    recover:
        fn(StorageBackend) run after each simulated crash, before
        classification.  May be None if the design is crash-tolerant
        without recovery.
    classify:
        fn(StorageBackend) -> str returning a tag for the recovered
        durable state (e.g. "old", "new", "partial", "corrupt(...)").
    allowed_states:
        The exhaustive set of acceptable tags.  A recovered state whose
        tag is not in this set is reported as a violation.
    """

    def __init__(
        self,
        *,
        scenario: str = "unnamed",
        initial_files: Optional[Dict[str, bytes]] = None,
        workload: Callable[[InjectingStorage], None],
        recover: Optional[Callable[[StorageBackend], None]] = None,
        classify: Callable[[StorageBackend], str],
        allowed_states: Set[str],
    ) -> None:
        self._scenario = scenario
        self._initial_files = dict(initial_files or {})
        self._workload = workload
        self._recover = recover
        self._classify = classify
        self._allowed = set(allowed_states)

    def _fresh_backend(self) -> StorageBackend:
        return StorageBackend(SimulatedDisk(files=self._initial_files))

    def run(self) -> CoverageReport:
        # Pass 1: enumerate crash points and check the crash-free commit.
        enum_backend = self._fresh_backend()
        enum_storage = InjectingStorage(enum_backend)
        self._workload(enum_storage)
        points = list(enum_storage.points)
        commit_state = self._classify(enum_backend)
        commit_ok = commit_state in self._allowed

        # Pass 2+3: crash at every point, recover, classify, compare.
        outcomes: List[CrashPointOutcome] = []
        seen: Dict[str, int] = {}  # durable fingerprint -> first point index
        for index, label in enumerate(points):
            backend = self._fresh_backend()
            storage = InjectingStorage(backend, crash_at=index)
            try:
                self._workload(storage)
            except CrashInjected:
                pass
            backend.crash()

            fingerprint = backend.durable_fingerprint()
            if fingerprint in seen:
                outcomes.append(CrashPointOutcome(
                    index=index,
                    label=label,
                    status="skipped",
                    detail=(
                        f"durable state identical to crash point {seen[fingerprint]}; "
                        f"deterministic recovery would reproduce its verdict"
                    ),
                ))
                continue
            seen[fingerprint] = index

            if self._recover is not None:
                self._recover(backend)
            state = self._classify(backend)
            if state in self._allowed:
                outcomes.append(CrashPointOutcome(
                    index=index, label=label, status="pass", state=state,
                ))
            else:
                outcomes.append(CrashPointOutcome(
                    index=index,
                    label=label,
                    status="violation",
                    state=state,
                    detail=(
                        f"recovered state {state!r} is not in the allowed set "
                        f"{sorted(self._allowed)}"
                    ),
                ))

        return CoverageReport(
            scenario=self._scenario,
            outcomes=outcomes,
            allowed_states=set(self._allowed),
            commit_state=commit_state,
            commit_ok=commit_ok,
        )
