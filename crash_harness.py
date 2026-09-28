"""Crash-consistency test harness (Python 3, standard library only).

Model: prefix persistence. Every storage operation (sector write, fsync,
rename, ...) mutates a virtual disk and is registered as a crash point.
A crash at point i means exactly the first i operations persisted and the
rest were lost. The harness enumerates one crash point before every
operation plus one at the very end of the workload, replays the workload
once per point, runs recovery, and checks the recovered state against the
caller-declared set of allowed outcomes (OLD / NEW / explicitly named
intermediate states). "Anything goes" is not supported: a recovered state
that is not in the allowed set is reported as a violation.

Soundness assumption for dedup: recovery depends only on durable disk
contents, so two crash points with identical disk signatures are
equivalent and the later one is skipped (recorded in the skip stats).
"""

SECTOR_SIZE = 16


class CrashInjected(Exception):
    """Raised inside the workload at the moment the machine 'crashes'."""


class CrashInjector:
    """Counts storage operations; raises CrashInjected at the target point."""

    def __init__(self):
        self.ops = []        # record mode: (desc, mutates, disk_signature_before)
        self.crash_at = None # run mode: index of the crash point, None = record
        self.disabled = False
        self._count = 0
        self._disk = None

    def attach(self, disk):
        self._disk = disk

    def point(self, desc, mutates):
        if self.disabled:
            return
        if self.crash_at is None:
            self.ops.append((desc, mutates, self._disk.signature()))
        elif self._count == self.crash_at:
            raise CrashInjected(desc)
        else:
            self._count += 1


class VirtualDisk:
    """Injectable storage layer. Writes are sectorized so torn writes occur."""

    def __init__(self, sector_size=SECTOR_SIZE):
        self.files = {}
        self.sector_size = sector_size
        self.injector = None

    def _point(self, desc, mutates=True):
        if self.injector is not None:
            self.injector.point(desc, mutates)

    def write(self, path, data):
        self._point("truncate %s" % path)
        self.files[path] = b""
        for off in range(0, len(data), self.sector_size):
            end = min(off + self.sector_size, len(data))
            self._point("write %s [%d:%d]" % (path, off, end))
            self.files[path] = self.files.get(path, b"") + data[off:end]

    def read(self, path):
        self._point("read %s" % path, mutates=False)
        return self.files.get(path)

    def fsync(self, path):
        # Barrier op: no state change in the prefix model, but it is still
        # enumerated as a crash point (crashes before/after it differ).
        self._point("fsync %s" % path)

    def rename(self, src, dst):
        self._point("rename %s -> %s" % (src, dst))
        self.files[dst] = self.files.pop(src)

    def fsync_dir(self, path):
        self._point("fsync dir %s" % path)

    def signature(self):
        return tuple(sorted(self.files.items()))


class Violation:
    def __init__(self, point, context, token, allowed):
        self.point = point
        self.context = context
        self.token = token
        self.allowed = allowed

    def __str__(self):
        return "point %d (%s): recovered %r, allowed %s" % (
            self.point, self.context, self.token,
            "{" + ", ".join(sorted(map(repr, self.allowed))) + "}")


class Report:
    def __init__(self, name):
        self.name = name
        self.points = 0
        self.tested = 0
        self.skipped = []    # (point, reason)
        self.outcomes = []   # (point, context, token)
        self.violations = []

    @property
    def ok(self):
        return not self.violations

    def render(self):
        lines = []
        lines.append("=" * 72)
        lines.append("scenario: %s" % self.name)
        lines.append("-" * 72)
        for point, context, token in self.outcomes:
            mark = "VIOLATION" if any(v.point == point for v in self.violations) else "ok"
            lines.append("  [%2d] tested  %-40s -> %r [%s]" % (point, context, token, mark))
        for point, reason in self.skipped:
            lines.append("  [%2d] skipped %s" % (point, reason))
        lines.append("-" * 72)
        lines.append("crash points enumerated : %d" % self.points)
        lines.append("tested                  : %d" % self.tested)
        lines.append("skipped                 : %d" % len(self.skipped))
        reasons = {}
        for _, reason in self.skipped:
            if reason.startswith("disk state identical"):
                key = "disk state identical to earlier point"
            else:
                key = "previous op is read-only"
            reasons[key] = reasons.get(key, 0) + 1
        for key in sorted(reasons):
            lines.append("    %-36s: %d" % (key, reasons[key]))
        lines.append("violations              : %d" % len(self.violations))
        for v in self.violations:
            lines.append("    %s" % v)
        lines.append("result: %s" % ("PASS" if self.ok else "FAIL"))
        return "\n".join(lines)


class CrashHarness:
    """Enumerates crash points of a workload and checks every one of them.

    seed(disk)          installs the durable initial (old) state
    make_store(disk)    builds the storage implementation under test
    workload(store)     deterministic sequence of store operations
    checker(store)      inspects the recovered store, returns a token
    allowed             set of tokens considered correct; anything else
                        is a violation. No "any state passes" mode exists.
    """

    def __init__(self, name, seed, make_store, workload, checker, allowed):
        self.name = name
        self.seed = seed
        self.make_store = make_store
        self.workload = workload
        self.checker = checker
        self.allowed = frozenset(allowed)

    def _fresh_disk(self, crash_at=None):
        disk = VirtualDisk()
        inj = CrashInjector()
        inj.crash_at = crash_at
        inj.attach(disk)
        disk.injector = inj
        self.seed(disk)
        return disk, inj

    @staticmethod
    def _recover(store):
        recover = getattr(store, "recover", None)
        if recover is not None:
            recover()

    def _record(self):
        disk, inj = self._fresh_disk()
        inj.disabled = True
        self._recover(self.make_store(disk))
        inj.disabled = False
        self.workload(self.make_store(disk))
        return inj.ops, disk.signature()

    def _run_point(self, i, ops):
        disk, inj = self._fresh_disk(crash_at=i)
        inj.disabled = True
        self._recover(self.make_store(disk))
        inj.disabled = False
        try:
            self.workload(self.make_store(disk))
        except CrashInjected:
            pass
        inj.disabled = True  # recovery and checking must not crash
        fresh = self.make_store(disk)
        try:
            self._recover(fresh)
            token = self.checker(fresh)
        except Exception as exc:
            token = ("RECOVERY-FAILURE", "%s: %s" % (type(exc).__name__, exc))
        if i < len(ops):
            context = "before %r" % ops[i][0]
        else:
            context = "after final op"
        return token, context

    def run(self):
        ops, final_sig = self._record()
        # Determinism guard: the workload must be replayable.
        ops2, _ = self._record()
        assert [o[0] for o in ops] == [o[0] for o in ops2], \
            "workload is not deterministic; crash points would be meaningless"

        sigs = [sig for (_, _, sig) in ops] + [final_sig]
        report = Report(self.name)
        report.points = len(sigs)
        seen = {}
        for i in range(len(sigs)):
            if i > 0 and not ops[i - 1][1]:
                report.skipped.append(
                    (i, "previous op is read-only: %r (no state change)" % ops[i - 1][0]))
                continue
            if sigs[i] in seen:
                report.skipped.append(
                    (i, "disk state identical to point %d (no durable change)" % seen[sigs[i]]))
                continue
            seen[sigs[i]] = i
            token, context = self._run_point(i, ops)
            report.tested += 1
            report.outcomes.append((i, context, token))
            if token not in self.allowed:
                report.violations.append(Violation(i, context, token, self.allowed))
        return report
