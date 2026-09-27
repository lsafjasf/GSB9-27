"""Durable fixed-window metrics store.

Three classes of data are kept on disk, in a single checksummed state file:

* counters:   cumulative, non-negative counters (e.g. requests_total)
* gauges:     absolute values that may move in either direction
* monotones:  cumulative values that may never decrease (e.g. bytes_sent)

Per-window aggregation never starts from zero after a restart: every window
(bucket) carries its own cumulative counter on disk, so a restart landing in
the middle of a window is transparent and the window's aggregate equals
before + after.

Persistence policy:

* save  -> temp file + fsync + os.replace (atomic) + fsync of the directory
* check -> SHA-256 of the canonical JSON payload, stored in the envelope
* backup -> the previous state file is kept as "<state>.bak" (generation
  lag of one); recovery prefers the primary, then the backup
* corruption / truncation is NEVER ignored by silently starting from zero.
  The caller picks an explicit policy (recover | fail | reset), and when
  data is discarded the bad file is quarantined.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time

STATE_VERSION = 1
ENVELOPE_VERSION = 1


class CorruptStateFileError(Exception):
    """State file is missing, truncated, or fails checksum/schema checks."""


class StaleStateFileError(Exception):
    """State file is older than the configured maximum age."""


class ConfigurationMismatchError(Exception):
    """State file was written with incompatible window settings."""


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_number(value) -> bool:
    # bool is an int subclass; counters must be plain numbers
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class MetricsStore:
    def __init__(
        self,
        state_path,
        window_seconds=3600,
        max_age_seconds=None,
        on_corrupt="fail",
        clock=time.time,
    ):
        if on_corrupt not in ("recover", "fail", "reset"):
            raise ValueError("on_corrupt must be one of recover|fail|reset")
        self.state_path = os.fspath(state_path)
        self.bak_path = self.state_path + ".bak"
        self.window_seconds = int(window_seconds)
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_age_seconds = max_age_seconds
        self.on_corrupt = on_corrupt
        self._clock = clock
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}
        self.monotones: dict[str, float] = {}
        self.windows: dict[str, dict[str, int]] = {}
        self.origin = int(self._clock())
        self.recovered_from = None  # "primary" | "backup" | "fresh" | "reset"
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self):
        primary_missing = not os.path.exists(self.state_path)
        if not primary_missing:
            # Staleness is an mtime property: check it before the checksum so
            # a stale file that is also torn is reported as stale, not as
            # "recoverable". StaleStateFileError deliberately propagates.
            self._check_path_staleness(self.state_path)
            try:
                self._adopt(self._read_validated(self.state_path))
                self.recovered_from = "primary"
                return
            except CorruptStateFileError:
                pass

        if os.path.exists(self.bak_path):
            self._check_path_staleness(self.bak_path)
            try:
                self._adopt(self._read_validated(self.bak_path))
                self.recovered_from = "backup"
                return
            except CorruptStateFileError:
                pass

        if not primary_missing:
            # A primary existed but neither it nor the backup is usable.
            if self.on_corrupt == "fail":
                raise CorruptStateFileError(
                    "state file %s is corrupt and no valid backup exists"
                    % self.state_path
                )
            if self.on_corrupt == "recover":
                raise CorruptStateFileError(
                    "state file %s is corrupt and no valid backup to recover from"
                    % self.state_path
                )
            # on_corrupt == "reset": explicit opt-in; quarantine first.
            self._quarantine(self.state_path)
            if os.path.exists(self.bak_path):
                self._quarantine(self.bak_path)

        self.recovered_from = "fresh" if primary_missing else "reset"

    def _adopt(self, state):
        self.origin = state["origin"]
        self.counters = state["counters"]
        self.gauges = state["gauges"]
        self.monotones = state["monotones"]
        self.windows = state["windows"]

    def _check_path_staleness(self, path):
        if self.max_age_seconds is None:
            return
        age = int(self._clock()) - int(os.path.getmtime(path))
        if age > int(self.max_age_seconds):
            raise StaleStateFileError(
                "state file %s is %ss old (limit %ss)"
                % (path, age, self.max_age_seconds)
            )

    def _read_validated(self, path):
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise CorruptStateFileError("cannot read %s: %s" % (path, exc))
        if not raw:
            raise CorruptStateFileError("%s is empty (truncated to 0 bytes)" % path)
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CorruptStateFileError("%s is not valid JSON: %s" % (path, exc))
        if not isinstance(envelope, dict):
            raise CorruptStateFileError("%s envelope is not an object" % path)
        for key in ("version", "payload", "sha256"):
            if key not in envelope:
                raise CorruptStateFileError("%s missing envelope key %r" % (path, key))
        if envelope["version"] != ENVELOPE_VERSION:
            raise CorruptStateFileError(
                "%s unsupported envelope version %r" % (path, envelope["version"])
            )
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise CorruptStateFileError("%s payload is not an object" % path)
        expected = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        given = envelope["sha256"]
        if not isinstance(given, str) or given.lower() != expected:
            raise CorruptStateFileError("%s checksum mismatch" % path)
        self._validate_state(payload)
        return payload

    def _validate_state(self, state):
        for key in ("version", "window_seconds", "origin", "counters",
                    "gauges", "monotones", "windows"):
            if key not in state:
                raise CorruptStateFileError("payload missing key %r" % key)
        if state["version"] != STATE_VERSION:
            raise CorruptStateFileError("unsupported payload version %r"
                                        % state["version"])
        if state["window_seconds"] != self.window_seconds:
            raise ConfigurationMismatchError(
                "window_seconds changed: state=%s configured=%s"
                % (state["window_seconds"], self.window_seconds)
            )
        if not _is_number(state["origin"]):
            raise CorruptStateFileError("origin must be a number")
        for section in ("counters", "gauges", "monotones"):
            values = state[section]
            if not isinstance(values, dict) or not all(
                isinstance(name, str) and _is_number(value)
                for name, value in values.items()
            ):
                raise CorruptStateFileError("%s must be {str: number}" % section)
        for name, value in state["counters"].items():
            if value < 0:
                raise CorruptStateFileError("counter %r is negative" % name)
        windows = state["windows"]
        if not isinstance(windows, dict):
            raise CorruptStateFileError("windows must be an object")
        for name, buckets in windows.items():
            if not isinstance(name, str) or not isinstance(buckets, dict):
                raise CorruptStateFileError("windows entries must be {str: {str: int}}")
            for index, value in buckets.items():
                if not (isinstance(index, str) and index.isdigit()
                        and isinstance(value, int) and not isinstance(value, bool)
                        and value >= 0):
                    raise CorruptStateFileError(
                        "window bucket %s[%s] must be a non-negative int"
                        % (name, index)
                    )

    # ------------------------------------------------------------- mutations

    def _bucket(self, ts=None):
        if ts is None:
            ts = self._clock()
        return int(ts) // self.window_seconds

    def incr(self, name, amount=1, ts=None):
        if not _is_number(amount) or amount < 0:
            raise ValueError("increments must be non-negative numbers")
        self.counters[name] = self.counters.get(name, 0) + amount
        buckets = self.windows.setdefault(name, {})
        index = str(self._bucket(ts))
        buckets[index] = buckets.get(index, 0) + amount
        return self.counters[name]

    def set_gauge(self, name, value):
        if not _is_number(value):
            raise ValueError("gauge values must be numbers")
        self.gauges[name] = value

    def add_monotone(self, name, amount=1):
        if not _is_number(amount):
            raise ValueError("amount must be a number")
        if amount < 0:
            raise ValueError("monotone metrics cannot decrease")
        self.monotones[name] = self.monotones.get(name, 0) + amount
        return self.monotones[name]

    # -------------------------------------------------------------- readings

    def value(self, name):
        return self.counters.get(name, 0)

    def window_value(self, name, ts=None):
        index = str(self._bucket(ts))
        return self.windows.get(name, {}).get(index, 0)

    def series(self, name, start_ts, end_ts=None, fill_empty=True):
        """Fixed-window aggregates, one bucket per window (zero-filled).

        Empty buckets are filled with 0 by default so a restart can never
        produce a missing point; callers can distinguish true gaps only by
        inspecting the raw buckets.
        """
        if end_ts is None:
            end_ts = self._clock()
        first = self._bucket(start_ts)
        last = self._bucket(end_ts)
        buckets = self.windows.get(name, {})
        out = []
        for index in range(first, last + 1):
            value = buckets.get(str(index))
            if value is None:
                if fill_empty:
                    value = 0
                else:
                    continue
            out.append((index * self.window_seconds, value))
        return out

    def snapshot(self):
        return {
            "origin": self.origin,
            "recovered_from": self.recovered_from,
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "monotones": dict(self.monotones),
            "windows": {name: dict(buckets)
                        for name, buckets in self.windows.items()},
        }

    # ------------------------------------------------------------------ save

    def _payload(self, retain_windows):
        windows = self.windows
        if retain_windows is not None:
            cutoff = self._bucket(self._clock() - retain_windows) - 1
            windows = {}
            for name, buckets in self.windows.items():
                kept = {i: v for i, v in buckets.items() if int(i) >= cutoff}
                if kept:
                    windows[name] = kept
        return {
            "version": STATE_VERSION,
            "window_seconds": self.window_seconds,
            "origin": self.origin,
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "monotones": dict(self.monotones),
            "windows": windows,
        }

    def flush(self, retain_windows=None):
        payload = self._payload(retain_windows)
        body = canonical_bytes(payload)
        envelope = canonical_bytes({
            "version": ENVELOPE_VERSION,
            "sha256": hashlib.sha256(body).hexdigest(),
            "payload": payload,
        })
        directory = os.path.dirname(os.path.abspath(self.state_path)) or "."
        tmp_path = "%s.tmp.%d" % (self.state_path, os.getpid())
        with open(tmp_path, "wb") as handle:
            handle.write(envelope)
            handle.flush()
            os.fsync(handle.fileno())
        # rotate: primary -> backup, new -> primary, fsync directory each time
        if os.path.exists(self.state_path):
            os.replace(self.state_path, self.bak_path)
        os.replace(tmp_path, self.state_path)
        _fsync_dir(directory)
        return self.state_path

    def _quarantine(self, path):
        if not os.path.exists(path):
            return
        directory = os.path.dirname(os.path.abspath(path))
        quarantine = os.path.join(directory, "quarantine")
        os.makedirs(quarantine, exist_ok=True)
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(self._clock()))
        dest = os.path.join(
            quarantine,
            "%s.%s.%s.corrupt"
            % (os.path.basename(path), stamp, os.getpid()),
        )
        with contextlib.suppress(OSError):
            os.replace(path, dest)

    def close(self):
        self.flush()


def _fsync_dir(directory):
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = None
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
    except OSError:
        pass
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def make_store(state_path, fixed_ts=None, **kwargs):
    """Factory used by the CLI/tests: METRICS_FIXED_TS pins the clock."""
    if fixed_ts is None:
        fixed_ts = os.environ.get("METRICS_FIXED_TS")
    clock = (lambda: float(fixed_ts)) if fixed_ts is not None else time.time
    return MetricsStore(state_path, clock=clock, **kwargs)
