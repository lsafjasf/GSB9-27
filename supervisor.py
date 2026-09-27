"""Supervisor daemon library (stdlib only).

Restarts a child process after abnormal exits with capped exponential
backoff. Consecutive fast crashes trip a circuit breaker that stops
automatic restarts until a human calls ``reset()``. Every child is
reaped via ``Popen.wait()`` so no zombies are left behind.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class ExitInfo:
    """How one child run ended."""

    returncode: int | None
    sig: int | None
    started_at: float
    ended_at: float
    uptime: float
    start_failed: bool = False
    error: str | None = None

    @property
    def normal(self) -> bool:
        """Normal exit == exited by itself with code 0."""
        return not self.start_failed and self.sig is None and self.returncode == 0

    def describe(self) -> str:
        if self.start_failed:
            return f"start failed: {self.error}"
        if self.sig is not None:
            return f"killed by signal {self.sig}"
        return f"exited with code {self.returncode}"


@dataclass
class TimelineEvent:
    mono: float
    wall: float
    kind: str
    detail: str


class Supervisor:
    def __init__(
        self,
        argv,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        fast_crash_window: float = 2.0,
        fast_crash_threshold: int = 3,
        logger=print,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        self.argv = list(argv)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.fast_crash_window = fast_crash_window
        self.fast_crash_threshold = fast_crash_threshold
        self.log = logger
        self.clock = clock
        self.sleeper = sleeper

        self.timeline: list[TimelineEvent] = []
        self.exits: list[ExitInfo] = []
        self.attempts = 0
        self.fast_crashes = 0
        self.circuit_open = False

        self._stop_event = threading.Event()
        self._reset_event = threading.Event()
        self._child: subprocess.Popen | None = None
        self._child_lock = threading.Lock()

    # -- events ----------------------------------------------------------

    def _emit(self, kind: str, detail: str) -> None:
        ev = TimelineEvent(self.clock(), time.time(), kind, detail)
        self.timeline.append(ev)
        self.log(f"[{time.strftime('%H:%M:%S', time.localtime(ev.wall))}"
                 f".{int((ev.wall % 1) * 1000):03d}] {kind}: {detail}")

    # -- control API (thread-safe) ---------------------------------------

    def stop(self) -> None:
        """Stop supervising; terminate the current child if any."""
        self._stop_event.set()
        self._reset_event.set()  # unblock circuit wait
        with self._child_lock:
            child = self._child
        if child is not None and child.poll() is None:
            child.terminate()

    def reset(self) -> None:
        """Manual confirmation: close the circuit and resume restarts."""
        self.fast_crashes = 0
        self.circuit_open = False
        self._emit("reset", "manual reset acknowledged, circuit closed")
        self._reset_event.set()

    # -- main loop ---------------------------------------------------------

    def _spawn(self) -> ExitInfo:
        started = self.clock()
        try:
            proc = subprocess.Popen(self.argv)
        except OSError as exc:
            ended = self.clock()
            return ExitInfo(None, None, started, ended, ended - started,
                            start_failed=True, error=str(exc))
        with self._child_lock:
            self._child = proc
        returncode = proc.wait()  # reaps the child: no zombies
        ended = self.clock()
        with self._child_lock:
            self._child = None
        sig = -returncode if returncode < 0 else None
        return ExitInfo(None if returncode < 0 else returncode,
                        sig, started, ended, ended - started)

    def _backoff(self) -> float:
        exp = min(self.attempts - 1, 10)
        return min(self.base_delay * (2 ** exp), self.max_delay)

    def run(self) -> ExitInfo:
        """Supervise until normal exit or stop(). Returns the last ExitInfo."""
        self._emit("start", f"supervising: {self.argv!r}")
        while not self._stop_event.is_set():
            self.attempts += 1
            self._emit("spawn", f"attempt #{self.attempts}")
            info = self._spawn()
            self.exits.append(info)
            self._emit("exit",
                       f"{info.describe()} after {info.uptime:.3f}s")

            if info.normal:
                self._emit("done", "normal exit (code 0), not restarting")
                return info

            if info.uptime < self.fast_crash_window:
                self.fast_crashes += 1
            else:
                self.fast_crashes = 0

            if self.fast_crashes >= self.fast_crash_threshold:
                self.circuit_open = True
                self._emit("circuit-open",
                           f"{self.fast_crashes} consecutive fast crashes; "
                           "auto-restart suspended, waiting for manual reset")
                self._reset_event.clear()
                while not self._reset_event.wait(timeout=0.1):
                    if self._stop_event.is_set():
                        return info
                if self._stop_event.is_set():
                    return info

            delay = self._backoff()
            self._emit("backoff", f"restarting in {delay:.3f}s")
            self.sleeper(delay)
        return self.exits[-1] if self.exits else None
