#!/usr/bin/env python3
"""Measure parser throughput and memory with deterministic synthetic streams."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import platform
import sys
import time
import tracemalloc

from eventstream import SSEParser, encode_event, heartbeat


def format_bytes(value):
    units = ["B", "KiB", "MiB", "GiB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} GiB"


def build_stream(event_count, data_size):
    parts = []
    payload = "x" * data_size
    for index in range(event_count):
        if index % 100 == 0:
            parts.append(heartbeat())
        parts.append(encode_event(payload, event="bench", id=f"evt-{index}"))
    return b"".join(parts)


def parse_stream(raw, chunk_size):
    parser = SSEParser()
    event_count = 0
    started = time.perf_counter()
    for offset in range(0, len(raw), chunk_size):
        event_count += len(parser.feed(raw[offset : offset + chunk_size]))
    event_count += len(parser.close())
    elapsed = time.perf_counter() - started
    return event_count, elapsed


def measure_many_small_events(event_count, data_size):
    raw = encode_event("x" * data_size, event="bench", id="steady")
    parser = SSEParser()
    tracemalloc.start()
    tracemalloc.reset_peak()
    started = time.perf_counter()
    delivered = 0
    for _ in range(event_count):
        delivered += len(parser.feed(raw))
    delivered += len(parser.close())
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "input_bytes": len(raw) * event_count,
        "events": delivered,
        "elapsed": elapsed,
        "current": current,
        "peak": peak,
    }


def measure_long_line(data_size, chunk_size):
    raw = encode_event("x" * data_size, id="long-line")
    parser = SSEParser()
    tracemalloc.start()
    tracemalloc.reset_peak()
    started = time.perf_counter()
    delivered = 0
    for offset in range(0, len(raw), chunk_size):
        delivered += len(parser.feed(raw[offset : offset + chunk_size]))
    delivered += len(parser.close())
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "input_bytes": len(raw),
        "events": delivered,
        "elapsed": elapsed,
        "current": current,
        "peak": peak,
    }


def main():
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--events", type=int, default=100_000)
    argument_parser.add_argument("--data-size", type=int, default=256)
    argument_parser.add_argument("--chunk-size", type=int, default=65_536)
    argument_parser.add_argument("--long-line-size", type=int, default=10 * 1024 * 1024)
    argument_parser.add_argument("--output", default="BENCHMARKS.md")
    args = argument_parser.parse_args()

    raw = build_stream(args.events, args.data_size)
    parsed_events, elapsed = parse_stream(raw, args.chunk_size)
    throughput_mib = len(raw) / elapsed / (1024 * 1024)
    events_per_second = parsed_events / elapsed

    small_memory = measure_many_small_events(args.events, args.data_size)
    long_memory = measure_long_line(args.long_line_size, args.chunk_size)

    lines = [
        "# Benchmarks",
        "",
        f"- Date (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Python: {sys.version.split()[0]} on {platform.platform()}",
        f"- Command: `python3 benchmark.py --events {args.events} --data-size {args.data_size} --chunk-size {args.chunk_size} --long-line-size {args.long_line_size}`",
        "- Scope: parser only; transport, TLS, HTTP, and application callbacks are excluded.",
        "",
        "## Throughput",
        "",
        "| Events | Payload/event | Input | Chunk | Time | Throughput | Event rate |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {parsed_events:,} | {args.data_size:,} B | {format_bytes(len(raw))} | {format_bytes(args.chunk_size)} | {elapsed:.3f} s | {throughput_mib:.2f} MiB/s | {events_per_second:,.0f} events/s |",
        "",
        "## Memory",
        "",
        "Memory is Python heap memory tracked by `tracemalloc`; peak is the maximum during parsing.",
        "",
        "| Scenario | Input | Events | Time | Current | Peak |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| Many {args.data_size:,} B events | {format_bytes(small_memory['input_bytes'])} | {small_memory['events']:,} | {small_memory['elapsed']:.3f} s | {format_bytes(small_memory['current'])} | {format_bytes(small_memory['peak'])} |",
        f"| One {format_bytes(args.long_line_size)} data line | {format_bytes(long_memory['input_bytes'])} | {long_memory['events']:,} | {long_memory['elapsed']:.3f} s | {format_bytes(long_memory['current'])} | {format_bytes(long_memory['peak'])} |",
        "",
    ]
    report = "\n".join(lines)
    with open(args.output, "w", encoding="utf-8") as output_file:
        output_file.write(report)
    print(report)


if __name__ == "__main__":
    main()
