# Benchmarks

- Date (UTC): 2026-09-27T19:32:25+00:00
- Python: 3.12.3 on Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
- Command: `python3 benchmark.py --events 100000 --data-size 256 --chunk-size 65536 --long-line-size 10485760`
- Scope: parser only; transport, TLS, HTTP, and application callbacks are excluded.

## Throughput

| Events | Payload/event | Input | Chunk | Time | Throughput | Event rate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100,000 | 256 B | 27.75 MiB | 64.00 KiB | 0.294 s | 94.46 MiB/s | 340,349 events/s |

## Memory

Memory is Python heap memory tracked by `tracemalloc`; peak is the maximum during parsing.

| Scenario | Input | Events | Time | Current | Peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| Many 256 B events | 27.47 MiB | 100,000 | 1.447 s | 160.00 B | 1.40 KiB |
| One 10.00 MiB data line | 10.00 MiB | 1 | 0.030 s | 107.00 B | 40.00 MiB |
