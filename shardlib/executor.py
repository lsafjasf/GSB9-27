"""Deterministic shard executor with retry + re-splitting.

The executor runs on a virtual clock (so tests are fast and repeatable) with a
fixed pool of workers. Each shard is processed by one worker, item by item.

Failure policy
--------------
* A failed item is retried, but never in place: once its shard finishes, the
  *residual* set of failed items is re-split into smaller shards
  (:func:`shardlib.splitter.child_shard_count`) and enqueued.
* Already succeeded items are never touched again: re-splits contain only the
  residual items, and the progress tracker rejects double commits.
* After ``max_item_attempts`` an item is declared dead and reported; the run
  still drains everything else (including the "everything fails" case).

Progress
--------
A :class:`~shardlib.progress.ProgressTracker` is updated once per committed
item and asserted after every single update, so the reported progress is
provably ``completed_work / total_work`` rather than a shard fraction.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .models import Failure, Item, Shard
from .progress import ProgressTracker, Snapshot
from .splitter import child_shard_count, split_cost_balanced

Handler = Callable[[Item, int], "float | Failure"]


@dataclass
class ShardRecord:
    shard_id: str
    depth: int
    parent_id: str | None
    item_ids: list[str]
    start_tick: float
    end_tick: float | None = None
    worker: int | None = None
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return (self.end_tick if self.end_tick is not None else self.start_tick) - self.start_tick


@dataclass
class RunResult:
    makespan: float
    succeeded: list[str]
    dead: list[str]
    invocations: dict[str, int]
    wasted_work: float
    worker_busy_time: list[float]
    shard_records: list[ShardRecord]
    snapshots: list[Snapshot]
    item_finish_tick: dict[str, float]
    item_duration: dict[str, float]
    resplits: list[dict]
    added_item_ids: list[str]
    final_fraction: float

    @property
    def ok(self) -> bool:
        return not self.dead


class Executor:
    def __init__(
        self,
        handler: Handler,
        *,
        shard_count: int,
        workers: int = 4,
        max_item_attempts: int = 3,
        splitter: Callable[..., list[Shard]] = split_cost_balanced,
    ):
        if shard_count < 1 or workers < 1 or max_item_attempts < 1:
            raise ValueError("shard_count, workers and max_item_attempts must be >= 1")
        self._handler = handler
        self._shard_count = shard_count
        self._workers = workers
        self._max_attempts = max_item_attempts
        self._splitter = splitter

    def run(
        self,
        initial_items: Iterable[Item],
        *,
        late_items: Sequence[tuple[float, Iterable[Item]]] = (),
    ) -> RunResult:
        initial_items = list(initial_items)

        tracker = ProgressTracker()
        committed: list[str] = []
        invocations: dict[str, int] = {item.item_id: 0 for item in initial_items}
        attempts: dict[str, int] = {item.item_id: 0 for item in initial_items}
        known_ids: set[str] = set()
        dead: set[str] = set()
        item_finish_tick: dict[str, float] = {}
        item_duration: dict[str, float] = {}
        wasted_work = 0.0
        added_item_ids: list[str] = []

        ready: list[Shard] = []
        records: dict[str, ShardRecord] = {}
        resplits: list[dict] = []
        snapshots: list[Snapshot] = []

        # Heap events: (tick, kind, seq, payload); kind 0 = add items, 1 = item done.
        events: list[tuple[float, int, int, object]] = []
        seq = 0

        def schedule_add(tick: float, items: list[Item], batch: str) -> None:
            nonlocal seq
            heapq.heappush(events, (tick, 0, seq, (items, batch)))
            seq += 1

        schedule_add(0.0, initial_items, "add0")
        for idx, (tick, items) in enumerate(late_items, start=1):
            schedule_add(float(tick), list(items), f"add{idx}")

        free_workers = list(range(self._workers))
        active: dict[int, tuple[Shard, int, set[str]]] = {}
        busy_time = [0.0] * self._workers

        def commit_snapshot(tick: float) -> None:
            tracker.assert_consistent(committed)
            snapshots.append(tracker.snapshot(tick))

        def start_shard(worker: int, shard: Shard, tick: float) -> None:
            """Assign ``shard`` to ``worker``; handler runs first item at ``tick``."""

            record = records[shard.shard_id]
            record.start_tick = tick
            record.worker = worker
            active[worker] = (shard, 0, set())
            _schedule_next(worker, tick)

        def _schedule_next(worker: int, tick: float) -> None:
            nonlocal seq
            shard, idx, residual = active[worker]
            if idx >= len(shard.items):
                _close_shard(worker, tick)
                return
            item = shard.items[idx]
            if item.item_id in dead:
                # Item gave up during an earlier shard; skip without reprocessing.
                active[worker] = (shard, idx + 1, residual)
                _schedule_next(worker, tick)
                return
            attempt = attempts[item.item_id] + 1
            attempts[item.item_id] = attempt
            invocations[item.item_id] = invocations.get(item.item_id, 0) + 1
            outcome = self._handler(item, attempt)
            if isinstance(outcome, Failure):
                duration, failure = outcome.spent_work, outcome
            else:
                duration, failure = float(outcome), None
            heapq.heappush(
                events,
                (tick + duration, 1, seq, (worker, duration, failure)),
            )
            seq += 1

        def _close_shard(worker: int, tick: float) -> None:
            shard, _idx, residual = active.pop(worker)
            record = records[shard.shard_id]
            record.end_tick = tick
            free_workers.append(worker)
            free_workers.sort()
            if residual:
                residual_items = [
                    item for item in shard.items if item.item_id in residual
                ]
                children_k = child_shard_count(
                    len(residual_items), shard.depth + 1, self._shard_count
                )
                children = self._splitter(
                    residual_items,
                    children_k,
                    prefix=f"{shard.shard_id}.r",
                    depth=shard.depth + 1,
                    parent_id=shard.shard_id,
                )
                for child in children:
                    records[child.shard_id] = ShardRecord(
                        shard_id=child.shard_id,
                        depth=child.depth,
                        parent_id=child.parent_id,
                        item_ids=[item.item_id for item in child.items],
                        start_tick=tick,
                    )
                resplits.append(
                    {
                        "parent": shard.shard_id,
                        "at_tick": tick,
                        "residual_items": sorted(residual),
                        "children": [child.shard_id for child in children],
                        "depth": shard.depth + 1,
                    }
                )
                ready.extend(children)

        while events:
            tick, kind, _event_seq, payload = heapq.heappop(events)

            if kind == 0:
                new_items, batch = payload  # type: ignore[misc]
                if new_items:
                    for item in new_items:
                        if batch != "add0" and item.item_id in known_ids:
                            raise ValueError(
                                f"duplicate item_id in late add: {item.item_id}"
                            )
                        known_ids.add(item.item_id)
                        invocations[item.item_id] = 0
                        attempts[item.item_id] = 0
                        if batch != "add0":
                            added_item_ids.append(item.item_id)
                    tracker.add_items(new_items)
                    shards = self._splitter(
                        new_items,
                        self._shard_count,
                        prefix=batch,
                    )
                    for shard in shards:
                        records[shard.shard_id] = ShardRecord(
                            shard_id=shard.shard_id,
                            depth=shard.depth,
                            parent_id=shard.parent_id,
                            item_ids=[item.item_id for item in shard.items],
                            start_tick=tick,
                        )
                    ready.extend(shards)
                    snapshots.append(tracker.snapshot(tick))
                    tracker.assert_consistent(committed)

            else:
                worker, duration, failure = payload  # type: ignore[misc]
                shard, idx, residual = active[worker]
                item = shard.items[idx]
                record = records[shard.shard_id]
                busy_time[worker] += duration

                if failure is None:
                    # Exactly-once commit: a second commit raises AssertionError.
                    tracker.mark_completed(item.item_id)
                    committed.append(item.item_id)
                    record.succeeded.append(item.item_id)
                    item_finish_tick[item.item_id] = tick
                    item_duration[item.item_id] = duration
                    commit_snapshot(tick)
                else:
                    wasted_work += duration
                    record.failed.append(item.item_id)
                    if attempts[item.item_id] >= self._max_attempts:
                        dead.add(item.item_id)
                    else:
                        residual.add(item.item_id)

                active[worker] = (shard, idx + 1, residual)
                _schedule_next(worker, tick)

            # Drain everything dispatchable at (or after) this tick.
            while free_workers and ready:
                worker = free_workers.pop(0)
                shard = ready.pop(0)
                start_shard(worker, shard, tick)

        makespan = max(
            (record.end_tick or 0.0) for record in records.values()
        ) if records else 0.0
        tracker.assert_consistent(committed)
        final_snapshot = tracker.snapshot(makespan)
        if not any(snap.tick == makespan for snap in snapshots):
            snapshots.append(final_snapshot)

        return RunResult(
            makespan=makespan,
            succeeded=sorted(committed),
            dead=sorted(dead),
            invocations=dict(sorted(invocations.items())),
            wasted_work=wasted_work,
            worker_busy_time=busy_time,
            shard_records=list(records.values()),
            snapshots=snapshots,
            item_finish_tick=item_finish_tick,
            item_duration=item_duration,
            resplits=resplits,
            added_item_ids=added_item_ids,
            final_fraction=tracker.fraction(),
        )
