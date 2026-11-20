# tinydb benchmarks

| | |
|---|---|
| **Document** | Benchmark results |
| **Version** | 1.0 (Phase 7) |
| **Date** | 2026-11-20 |
| **Machine** | Python 3.9.6 on macOS-27.0.1 |
| **Data** | 10,000 made-up clients, about 15,000 enrollment periods, 8 plans (`tinydb/sample.py`, seed 7) |
| **Related** | [design.md](design.md#4-what-measuring-taught), [architecture.md](architecture.md) |

## 1. How to reproduce

```bash
python3 -m tinydb.bench                       # 10,000 clients, median of 5 runs (about 30 seconds)
python3 -m tinydb.bench --clients 1000 --repeat 3
```

Everything runs in a temporary folder that's deleted afterwards. Numbers vary from machine to machine and run to run, so compare the **ratios** between rows, not the raw times.

## 2. Method

- **End to end.** Every benchmark goes through `Database.run` (the same path as the shell), or through a real `ApiServer` on a free port with real `QUERY` requests. The numbers include lexing, parsing, planning and building results, not only the data structures.
- **Same data every time.** `sample.py` generates the data from a fixed seed, with its own random number generator.
- **Median of several runs.** Noise only makes a run slower, never faster, so the median is a fairer summary than the average. Slow benchmarks (inserts, startup, encryption) run 3 times, the rest 5.
- **Garbage collector paused** while a run is timed, as Python's `timeit` does, so one benchmark's leftovers aren't collected during the next one.
- **Every plan checked.** The Notes column shows the query plan each benchmark actually used, and the row count, so a faster plan is also shown to give the same answer.

## 3. Results

| Benchmark | Median | Per operation | Per second | Notes |
|---|---:|---:|---:|---|
| INSERT, 1 row each | 67.0 ms | 134.0 µs | 7,461 | 500 rows, 500 fsyncs |
| INSERT, 500 rows each | 25.3 ms | 50.5 µs | 19,789 | 500 rows, 1 fsync |
| SELECT by primary key | 2.0 ms | 40.7 µs | 24,552 | primary key lookup |
| SELECT by birth_date, no index | 287.7 ms | 5.8 ms | 174 | full scan |
| SELECT by birth_date, hash index | 2.4 ms | 48.9 µs | 20,439 | hash index on birth_date |
| SELECT by birth_date, B-tree index | 2.6 ms | 51.4 µs | 19,445 | btree index on birth_date |
| Range on deadline, no index | 221.1 ms | 11.1 ms | 90 | 330 rows, full scan |
| Range on deadline, B-tree index | 22.4 ms | 1.1 ms | 892 | 330 rows, btree index on deadline |
| Open (replay), fresh | 204.2 ms | 204.2 ms | 5 | 3.8 MB |
| Open (replay), after 3 full updates | 459.5 ms | 459.5 ms | 2 | 8.3 MB |
| Compact all tables | 109.3 ms | 109.3 ms | 9 |  |
| Open (replay), after compaction | 220.7 ms | 220.7 ms | 5 | 3.8 MB |
| SELECT by primary key, in-process | 8.5 ms | 42.4 µs | 23,570 | 1 row per query |
| QUERY /query, new connection each | 75.9 ms | 379.4 µs | 2,636 | 1 row per query |
| QUERY /query, one kept-alive connection | 47.0 ms | 235.2 µs | 4,252 | 1 row per query |
| QUERY /query, 4 clients at once | 51.0 ms | 255.0 µs | 3,922 | 4 threads, one database lock |
| Derive key (PBKDF2, 600,000 rounds) | 356.8 ms | 356.8 ms | 3 |  |
| Load 10,000 clients, plain | 1.36 s | 1.36 s | 1 |  |
| Load 10,000 clients, encrypted | 1.84 s | 1.84 s | 1 |  |
| Open (replay), plain | 205.0 ms | 205.0 ms | 5 | 3.8 MB |
| Open (replay), encrypted | 964.9 ms | 964.9 ms | 1 | 6.6 MB |
| SELECT by primary key, plain | 2.1 ms | 41.9 µs | 23,864 |  |
| SELECT by primary key, encrypted | 2.0 ms | 40.8 µs | 24,510 |  |

## 4. Before and after the Phase 7 fixes

The benchmark found two performance bugs. Both gave correct answers, so all 458 tests passed. Section 5 explains each one.

| Benchmark | Before | After | Change |
|---|---:|---:|---:|
| Range on deadline, B-tree index | 8.3 ms | 1.1 ms | 7.5x faster |
| QUERY /query, one kept-alive connection (Linux test machine) | 43.6 ms | 327.4 µs | 133x faster |
| QUERY /query, one kept-alive connection (this machine) | not recorded | 235.2 µs | |

## 5. What the numbers show

**Batching.** One `INSERT` per row cost 134.0 µs a row, and 500 rows per `INSERT` cost 50.5 µs: 2.7x faster. Each `INSERT` is one transaction and one `fsync`, and the `fsync`, waiting for the disk, is the expensive part. This is why bulk loads use big transactions (group commit).

On macOS the gap is smaller than on Linux (about 7x there), and that's a real durability detail, not noise. On macOS, `fsync` hands the data to the drive but doesn't wait for the drive to write it from its own cache. Waiting for that needs `fcntl(fd, F_FULLFSYNC)`, which Python's `os.fsync` doesn't call. Linux's `fsync` does wait. So on a Mac, a sudden power cut could lose the last writes tinydb reported as saved, and a production database on macOS would use `F_FULLFSYNC` (SQLite has an option for exactly this).

**Indexes.** Finding clients by birth date took 5.8 ms per query with a full scan, 48.9 µs with a hash index (119x faster) and 51.4 µs with a B-tree. For `=`, both indexes are about equally fast, and a primary key lookup (40.7 µs) is about the same again. At that point almost all the time is parsing the SQL, not searching.

**Ranges, and the first bug.** Before the fix, a one-week range on an indexed `deadline` took 8.3 ms, only 1.3x faster than a full scan (11.1 ms). The planner handed the B-tree only `deadline >= X`, so it read every deadline from X onwards (about 6,000 rows) and checked `<= Y` row by row to keep 330 rows. `range_bounds` now passes both ends. After: 1.1 ms, 10x faster than a full scan. Most of what's left is sorting and returning the 330 rows.

**Startup and compaction.** Opening the database replays every log. Fresh: 204.2 ms (3.8 MB). After three `UPDATE`s of every client, which append a new copy of every row each time: 459.5 ms (8.3 MB). Compaction took 109.3 ms, and afterwards opening took 220.7 ms (3.8 MB). An append-only log trades startup time for simple, crash-safe writes, and compaction pays that back.

**HTTP, and the second bug.** The same primary key `SELECT` took 42.4 µs in-process, 235.2 µs over one kept-alive HTTP connection, and 379.4 µs with a new connection per request. So HTTP adds about 192.8 µs per request, almost all of it Python's `http.client` and `http.server` and JSON. A new connection adds the TCP handshake and a new server thread. On the Linux test machine, before the fix, kept-alive requests took **43.6 ms** each, slower than new connections. The server wrote the headers and the body in two writes. Nagle's algorithm held the body back until the client acknowledged the headers, and the client delays acknowledgements by up to 40 ms. `disable_nagle_algorithm = True` (`TCP_NODELAY`) removes the delay on every system. This machine's before-the-fix run wasn't recorded, which is why the before-and-after above uses the Linux number.

**Several clients at once.** QUERY /query, 4 clients at once took 255.0 µs per request, against 235.2 µs for one client: no faster. The clients run **in the same Python process** as the server, so client threads, server threads and the one database lock all take turns under one GIL. A real load test would run the clients in another process or on another machine.

**Encryption.** Loading 10,000 clients took 1.36 s plain and 1.84 s encrypted (35% slower: an HMAC for every line). Opening took 205.0 ms plain and 964.9 ms encrypted (4.7x), of which 356.8 ms is deriving the key with PBKDF2, slow on purpose against guessing. The files grew from 3.8 MB to 6.6 MB (1.7x: base64, a nonce and a tag on every line). Reads took 41.9 µs plain and 40.8 µs encrypted, the same within noise, because rows are decrypted once at startup and live in memory.

## 6. Caveats

- One machine, one run. Repeat it to see the spread: the order of magnitude is stable, the last digit isn't.
- Everything fits in memory, and the operating system caches the files, so these are warm-cache numbers.
- Clients and server share one process in the HTTP benchmarks, which favors one client over several.
- `fsync` speed depends heavily on the disk and the operating system, so the batching ratio varies most between machines.
