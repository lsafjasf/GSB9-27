"""Minimal end-to-end demo: write, random read, corrupt, locate, repair."""

import os
import tempfile

import snapshotstore as ss

with tempfile.TemporaryDirectory() as tmp:
    primary = os.path.join(tmp, "snap.bin")
    replica = os.path.join(tmp, "replica.bin")

    snapshot = b"".join(
        b"|%-8s id=%05d status=%s padding%s"
        % (b"RECORD", i, b"RUNNING" if i % 2 else b"PENDING", b"z" * 40)
        for i in range(2000))

    stats = ss.write_snapshot(primary, snapshot, chunk_size=4096)
    ss.write_snapshot(replica, snapshot, chunk_size=4096)
    print("wrote", stats["chunks"], "chunks;",
          stats["original_size"], "->", stats["compressed_size"], "bytes")

    with ss.SnapshotReader(primary) as rd:
        assert rd.read_all() == snapshot          # lossless round trip
        offset = 5000
        print("random read @5000:", rd.read_range(offset, length=32))
        print("byte 5000 lives in chunk", rd.locate(5000))

    # corrupt chunk 1
    with ss.SnapshotReader(primary) as rd:
        victim = rd.chunks[1].offset
    with open(primary, "r+b") as fh:
        fh.seek(victim + 3)
        fh.write(b"\x00")

    with ss.SnapshotReader(primary) as rd:
        bad = rd.verify()
        print("corruption located at chunks:", bad)
        try:
            rd.read_chunk(bad[0])
        except ss.CorruptChunkError as exc:
            print("refused bad data ->", exc)

    fixed = ss.restore_chunks(primary, [replica], auto_repair=True)
    print("restored chunks:", fixed)
    with ss.SnapshotReader(primary) as rd:
        assert rd.verify() == []
        assert rd.read_all() == snapshot
        print("post-repair round trip OK")
