"""Two KV-store implementations on top of the injectable VirtualDisk.

SafeKVStore : write tmp -> fsync tmp -> atomic rename -> fsync dir.
              Crash-safe: recovery always sees OLD or NEW per put.
BuggyKVStore: rewrites the data file in place, no fsync, no rename.
              Known defect: a crash mid-write leaves a torn file whose
              content is neither the old nor the new state.

Both stores are read-through (no in-memory caching) so that recovery
depends only on durable disk contents -- the harness's dedup assumption.
"""


def encode(db):
    return "".join("%s\t%s\n" % (k, v) for k, v in sorted(db.items())).encode()


def decode(blob):
    db = {}
    for line in blob.decode("utf-8", "replace").splitlines():
        if "\t" in line:
            key, value = line.split("\t", 1)
            db[key] = value
    return db


class _BaseKVStore:
    def __init__(self, disk, path="db/main"):
        self.disk = disk
        self.path = path
        self.tmp = path + ".tmp"
        self.dir = path.rsplit("/", 1)[0] if "/" in path else "."

    def _load(self):
        blob = self.disk.read(self.path)
        return decode(blob) if blob else {}

    def get(self, key):
        return self._load().get(key)

    def recover(self):
        # Nothing to do: reads go straight to the durable file.
        pass


class SafeKVStore(_BaseKVStore):
    def put(self, key, value):
        db = self._load()
        db[key] = value
        blob = encode(db)
        self.disk.write(self.tmp, blob)
        self.disk.fsync(self.tmp)
        self.disk.rename(self.tmp, self.path)
        self.disk.fsync_dir(self.dir)


class BuggyKVStore(_BaseKVStore):
    def put(self, key, value):
        db = self._load()
        db[key] = value
        # DEFECT (intentional, for harness self-test): in-place rewrite,
        # no tmp+rename, no fsync. Crash mid-write => torn file.
        self.disk.write(self.path, encode(db))
