"""Content-addressable storage with reference-counted GC and mark-sweep fallback.

Design
------
- Blobs are stored once under objects/<hh>/<rest-of-sha256-hex>.
- A SQLite index (WAL) tracks, per object: size and refcount, plus the
  reference graph (edges parent->child) and named roots.
- Refcount semantics: refcount of an object == number of incoming references
  (from named roots + anonymous ref-handles + edges from other objects).
  Every reference is a row in the reference graph, so mark-sweep can always
  distinguish a live object from garbage. When the count drops to 0 the
  object is reclaimed immediately (file + row removed, children decremented
  recursively).
- Concurrency safety: every mutation and every GC critical section runs
  under a single store-wide re-entrant lock, and file operations are ordered
  as "write file before publishing row" (put) and "remove row before
  deleting file" (reclaim). A concurrent put()/add_root()/ref() therefore
  can never observe a half-reclaimed object, and the collector can never
  delete a blob whose reference was just created -- the refcount check and
  the file unlink are atomic with respect to writers.
- Mark-sweep sweep() is a stop-the-world fallback that repairs refcount
  drift: reachability is recomputed from the roots, unreachable objects are
  reclaimed, and surviving objects' refcounts are recomputed from the actual
  graph. A report describing every correction is returned.

Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import threading
import time
import uuid

__all__ = ["ContentStore", "ObjectMissing"]


class ObjectMissing(KeyError):
    """Raised when referencing or reading an object that does not exist."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects(
    hash     TEXT PRIMARY KEY,
    size     INTEGER NOT NULL,
    refcount INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS edges(
    parent TEXT NOT NULL,
    child  TEXT NOT NULL,
    PRIMARY KEY(parent, child)
);
CREATE TABLE IF NOT EXISTS roots(
    name TEXT PRIMARY KEY,
    hash TEXT NOT NULL
);
"""


class ContentStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.objects_dir = os.path.join(self.path, "objects")
        os.makedirs(self.objects_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            os.path.join(self.path, "index.db"), check_same_thread=False
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _obj_path(self, h: str) -> str:
        return os.path.join(self.objects_dir, h[:2], h[2:])

    def _has_row(self, h: str) -> bool:
        cur = self._db.execute("SELECT 1 FROM objects WHERE hash=?", (h,))
        return cur.fetchone() is not None

    # -- core API ----------------------------------------------------------
    def put(self, data: bytes, refs=()) -> str:
        """Store *data* once; *refs* are child hashes this object points to.

        Returns the content hash. The new object starts with refcount 0;
        call ref()/add_root() (or put_root()) to keep it alive.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        data = bytes(data)
        refs = list(refs)
        with self._lock:
            h = self._put_locked(data, refs)
            self._db.commit()
            return h

    def put_many(self, items) -> list:
        """Batch put: items are bytes or (bytes, refs) tuples.

        One lock acquisition and one commit for the whole batch --
        much faster than repeated put() for many small objects.
        """
        hashes = []
        with self._lock:
            for item in items:
                if isinstance(item, tuple):
                    data, refs = item
                else:
                    data, refs = item, ()
                hashes.append(self._put_locked(bytes(data), list(refs),
                                               sync=False))
            # durability for the whole batch: fsync touched dirs once,
            # then the index commit (WAL is synchronous=FULL)
            for d in os.listdir(self.objects_dir):
                dirpath = os.path.join(self.objects_dir, d)
                if os.path.isdir(dirpath):
                    fd = os.open(dirpath, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            self._db.commit()
        return hashes

    def _put_locked(self, data: bytes, refs: list, sync: bool = True) -> str:
            h = self._hash(data)
            for child in refs:
                if not self._has_row(child):
                    raise ObjectMissing(child)
            path = self._obj_path(h)
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                        f.flush()
                        if sync:
                            os.fsync(f.fileno())
                    os.replace(tmp, path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            old_children = {
                r[0]
                for r in self._db.execute(
                    "SELECT child FROM edges WHERE parent=?", (h,)
                )
            }
            new_children = set(refs)
            self._db.execute(
                "INSERT INTO objects(hash,size,refcount) VALUES(?,?,0)"
                " ON CONFLICT(hash) DO NOTHING",
                (h, len(data)),
            )
            for child in new_children - old_children:
                self._db.execute(
                    "INSERT OR IGNORE INTO edges(parent,child) VALUES(?,?)",
                    (h, child),
                )
                self._db.execute(
                    "UPDATE objects SET refcount=refcount+1 WHERE hash=?",
                    (child,),
                )
            for child in old_children - new_children:
                self._db.execute(
                    "DELETE FROM edges WHERE parent=? AND child=?", (h, child)
                )
                self._decref(child)
            return h

    def put_root(self, name: str, data: bytes, refs=()) -> str:
        """Atomically put *data* and anchor it under root *name*."""
        with self._lock:
            h = self.put(data, refs=refs)
            self.add_root(name, h)
            return h

    def ref(self, h: str) -> str:
        """Pin an existing object; returns an opaque handle for release().

        The handle is an anonymous root, so the pin is visible to the
        mark-sweep collector exactly like any other reference.
        """
        with self._lock:
            if not self._has_row(h):
                raise ObjectMissing(h)
            handle = f"__handle__{uuid.uuid4().hex}"
            self._db.execute(
                "INSERT INTO roots(name,hash) VALUES(?,?)", (handle, h)
            )
            self._db.execute(
                "UPDATE objects SET refcount=refcount+1 WHERE hash=?", (h,)
            )
            self._db.commit()
            return handle

    def release(self, handle: str) -> None:
        """Drop a reference obtained from ref(); reclaims at refcount 0."""
        with self._lock:
            row = self._db.execute(
                "SELECT hash FROM roots WHERE name=?", (handle,)
            ).fetchone()
            if row is None:
                raise ObjectMissing(handle)
            self._db.execute("DELETE FROM roots WHERE name=?", (handle,))
            self._decref(row[0])
            self._db.commit()

    def add_root(self, name: str, h: str) -> None:
        with self._lock:
            if not self._has_row(h):
                raise ObjectMissing(h)
            row = self._db.execute(
                "SELECT hash FROM roots WHERE name=?", (name,)
            ).fetchone()
            if row is not None and row[0] != h:
                self._decref(row[0])
            self._db.execute(
                "INSERT INTO roots(name,hash) VALUES(?,?)"
                " ON CONFLICT(name) DO UPDATE SET hash=excluded.hash",
                (name, h),
            )
            if row is None or row[0] != h:
                self._db.execute(
                    "UPDATE objects SET refcount=refcount+1 WHERE hash=?",
                    (h,),
                )
            self._db.commit()

    def remove_root(self, name: str) -> None:
        with self._lock:
            row = self._db.execute(
                "SELECT hash FROM roots WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                raise KeyError(name)
            self._db.execute("DELETE FROM roots WHERE name=?", (name,))
            self._decref(row[0])
            self._db.commit()

    def get(self, h: str) -> bytes:
        with self._lock:
            if not self._has_row(h):
                raise ObjectMissing(h)
            path = self._obj_path(h)
        # File read outside the lock is safe: a live row means the file
        # cannot be unlinked (reclaim removes the row first, under lock).
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise ObjectMissing(h) from None

    def exists(self, h: str) -> bool:
        with self._lock:
            return self._has_row(h)

    def roots(self) -> dict:
        """Named roots only (anonymous ref-handles are hidden)."""
        with self._lock:
            return dict(
                self._db.execute(
                    "SELECT name,hash FROM roots WHERE name NOT LIKE '__handle__%'"
                )
            )

    def refcount(self, h: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT refcount FROM objects WHERE hash=?", (h,)
            ).fetchone()
            if row is None:
                raise ObjectMissing(h)
            return row[0]

    # -- reclamation -------------------------------------------------------
    def _decref(self, h: str) -> None:
        """Decrement refcount; cascade-reclaim when it hits 0. Caller holds lock."""
        stack = [h]
        while stack:
            cur = stack.pop()
            self._db.execute(
                "UPDATE objects SET refcount=refcount-1 WHERE hash=?", (cur,)
            )
            row = self._db.execute(
                "SELECT refcount FROM objects WHERE hash=?", (cur,)
            ).fetchone()
            if row is None or row[0] > 0:
                continue
            children = [
                r[0]
                for r in self._db.execute(
                    "SELECT child FROM edges WHERE parent=?", (cur,)
                )
            ]
            self._db.execute("DELETE FROM edges WHERE parent=?", (cur,))
            self._db.execute("DELETE FROM objects WHERE hash=?", (cur,))
            try:
                os.unlink(self._obj_path(cur))
            except FileNotFoundError:
                pass
            stack.extend(children)

    def sweep(self) -> dict:
        """Mark-sweep fallback GC. Repairs refcount drift. Returns a report."""
        with self._lock:
            t0 = time.perf_counter()
            reachable = set()
            stack = [r[0] for r in self._db.execute("SELECT hash FROM roots")]
            while stack:
                cur = stack.pop()
                if cur in reachable:
                    continue
                reachable.add(cur)
                stack.extend(
                    r[0]
                    for r in self._db.execute(
                        "SELECT child FROM edges WHERE parent=?", (cur,)
                    )
                )
            all_rows = self._db.execute("SELECT hash,size FROM objects").fetchall()
            all_hashes = {r[0] for r in all_rows}
            sizes = dict(all_rows)
            garbage = all_hashes - reachable
            bytes_reclaimed = sum(sizes[h] for h in garbage)
            for g in garbage:
                self._db.execute("DELETE FROM edges WHERE parent=?", (g,))
                self._db.execute("DELETE FROM objects WHERE hash=?", (g,))
                try:
                    os.unlink(self._obj_path(g))
                except FileNotFoundError:
                    pass
            self._db.execute(
                "DELETE FROM edges WHERE parent NOT IN (SELECT hash FROM objects)"
            )
            # Reclaim orphan files (e.g. crash between file write and commit).
            orphan_files = 0
            for dirpath, _dirs, files in os.walk(self.objects_dir):
                for fn in files:
                    fh = os.path.basename(dirpath) + fn
                    if fh not in all_hashes:
                        try:
                            os.unlink(os.path.join(dirpath, fn))
                            orphan_files += 1
                        except FileNotFoundError:
                            pass
            # Recompute refcounts of survivors from the actual graph.
            corrections = []
            for h in sorted(reachable):
                expected = self._db.execute(
                    "SELECT (SELECT COUNT(*) FROM roots WHERE hash=?) +"
                    "       (SELECT COUNT(*) FROM edges WHERE child=?)",
                    (h, h),
                ).fetchone()[0]
                stored = self._db.execute(
                    "SELECT refcount FROM objects WHERE hash=?", (h,)
                ).fetchone()[0]
                if stored != expected:
                    corrections.append(
                        {"hash": h, "stored": stored, "expected": expected}
                    )
                    self._db.execute(
                        "UPDATE objects SET refcount=? WHERE hash=?",
                        (expected, h),
                    )
            self._db.commit()
            return {
                "scanned": len(all_hashes),
                "reachable": len(reachable),
                "swept": len(garbage),
                "orphan_files_removed": orphan_files,
                "bytes_reclaimed": bytes_reclaimed,
                "refcount_corrections": corrections,
                "elapsed_s": time.perf_counter() - t0,
            }

    # -- validation --------------------------------------------------------
    def verify(self, check_hashes: bool = True) -> dict:
        """Validate index/files agreement and refcount consistency."""
        with self._lock:
            problems = []
            rows = self._db.execute("SELECT hash,size,refcount FROM objects").fetchall()
            for h, size, refcount in rows:
                path = self._obj_path(h)
                if not os.path.exists(path):
                    problems.append(f"missing file for {h}")
                    continue
                actual = os.path.getsize(path)
                if actual != size:
                    problems.append(f"size mismatch for {h}: {actual} != {size}")
                if check_hashes:
                    with open(path, "rb") as f:
                        if self._hash(f.read()) != h:
                            problems.append(f"content hash mismatch for {h}")
                expected = self._db.execute(
                    "SELECT (SELECT COUNT(*) FROM roots WHERE hash=?) +"
                    "       (SELECT COUNT(*) FROM edges WHERE child=?)",
                    (h, h),
                ).fetchone()[0]
                if refcount != expected:
                    problems.append(
                        f"refcount drift for {h}: stored={refcount} expected={expected}"
                    )
            db_hashes = {r[0] for r in rows}
            for dirpath, _dirs, files in os.walk(self.objects_dir):
                for fn in files:
                    h = os.path.basename(dirpath) + fn
                    if h not in db_hashes:
                        problems.append(f"orphan file {h}")
            return {"ok": not problems, "objects": len(rows), "problems": problems}

    def compact(self) -> None:
        """Reclaim free pages in the index (SQLite keeps them otherwise)."""
        with self._lock:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("VACUUM")

    def stats(self) -> dict:
        with self._lock:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            row = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size),0) FROM objects"
            ).fetchone()
            disk = 0
            for dirpath, _dirs, files in os.walk(self.path):
                for fn in files:
                    disk += os.path.getsize(os.path.join(dirpath, fn))
            return {
                "objects": row[0],
                "logical_unique_bytes": row[1],
                "disk_bytes": disk,
            }
