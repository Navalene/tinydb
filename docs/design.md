# tinydb design write-up

| | |
|---|---|
| **Document** | Design write-up |
| **Version** | 1.0 (Phase 7) |
| **Date** | 2026-11-20 |
| **Related** | [requirements.md](requirements.md), [architecture.md](architecture.md), [api-spec.md](api-spec.md), [benchmarks.md](benchmarks.md) |

## 1. Summary

tinydb is a database engine written from scratch in Python, using only the standard library: no SQLite, no ORM, no web framework. It stores every change in an append-only log and rebuilds its state by replaying that log. It has hand-built hash and B-tree indexes, a mini-SQL language with typed tables, a write-ahead log that makes every statement atomic, and an HTTP API that reads with the new `QUERY` method (RFC 10008). The API is protected by hashed API keys with read and write roles, hardened against malformed and hostile requests, takes parameterized queries, and can encrypt its files at rest.

It was built in seven phases, one per course, and each phase kept everything before it working: a Phase 1 log file still opens in version 1.0. There are 540 unit tests.

The last phase measured it. The benchmarks found two performance bugs that no test had caught, because both gave correct answers slowly. Section 4 tells that story.

## 2. Goals and constraints

| Goal | How it shaped the design |
|---|---|
| **Learn how databases work** by building every part | Hash table, B-tree, binary search, merge sort, lexer, parser, WAL and HTTP handling are all written by hand, not imported. |
| **Never lose saved data** | Append-only files, `fsync` before every reply, write-ahead logging, and recovery that trims a torn last line but refuses corruption in the middle. |
| **Standard library only** | Shaped two decisions above all: the server is built on `http.server`, and encryption is built from HMAC-SHA256 and PBKDF2 because Python has no AES built in. |
| **Explainable** | One job per module, one test file per module, and every query reports the plan it used. |
| **A realistic demo** | Everything is shown on made-up Medicare data: plans, clients and their enrollment periods, such as "which clients have a SEP deadline in the next 30 days?" |

## 3. The design, phase by phase

Each phase added a layer on top of the last, without replacing what was underneath.

### Phase 1 design: an append-only log

The data lives in `tinydb.jsonl`, a JSON-lines file where every line is one change:

```
{"op": "set", "key": "name", "value": "Ada"}
{"op": "set", "key": "name", "value": "Grace"}
{"op": "delete", "key": "name"}
```

- **Append, never overwrite.** A write adds one line to the end of the file and calls `fsync` so it's really on disk. Existing bytes are never touched, so a crash can't damage data that was already saved.
- **Replay on startup.** The current state is rebuilt by reading the log from top to bottom and applying each change to an in-memory dict. After that, reads are O(1) dict lookups.
- **Torn writes are ignored.** A record only counts once its line ends with a newline. If the program crashes in the middle of writing a line, that partial last line is skipped and trimmed off on the next startup. Bad data in the middle of the log is real corruption, so it raises an error instead of being skipped.
- **Compaction.** Overwrites and deletes leave old lines behind, so the log grows forever. `compact` rewrites it with one line per live key. It writes a temp file, fsyncs it, and swaps it in with an atomic `os.replace`, so a crash during compaction leaves either the old log or the new one, never half of each.

This is the same idea behind Bitcask (Riak's storage engine), Redis's AOF file, and the LSM-tree logs in Cassandra and RocksDB. Phase 4's write-ahead log grows out of it.

**Trade-offs:** writes are fast because appending is O(1), but startup time grows with the size of the log, and every key has to fit in memory. Compaction fixes the first problem. The second is still true after Phase 3: indexes live in memory too, and are rebuilt from the log on startup.

### Phase 2 design: one job per class

Phase 1 had one class, `KeyValueStore`, that validated input, wrote the file, replayed it, and held the data. Phase 2 splits those jobs apart so each class has a **single responsibility**:

```
typed text --> QueryParser --> Query --> cli.execute() --> Table --> Storage --> tinydb.jsonl
                                                             |
                                                           Record
```

- **Record** is a small frozen dataclass (`key`, `value`). It checks its own key and value when it's created, so every other class can trust it.
- **Storage** only reads and writes lines of JSON. It doesn't know what "set" or "delete" mean. `replay(apply)` hands each entry to a function the Table passes in, which is the same recovery loop a write-ahead log uses.
- **Table** holds Records in a dict for O(1) reads and turns changes into log entries. It's given a Storage instead of building one inside itself (**dependency injection**), so a test or a future WAL can swap in a different one.
- **QueryParser** turns words into a `Query` object and never touches data. Executing a Query is a separate step. Phase 4's SQL parser keeps the same split: it builds statement objects, and the Database runs them.

The file format didn't change, so a Phase 1 `tinydb.jsonl` still opens.

### Phase 3 design: indexes

Phase 2 could only find a record by its key. Finding "every client on a PPO plan" meant opening every record, a **full scan**, O(n). Phase 3 adds **indexes** on fields inside the values, built from scratch with no `dict` or `bisect` doing the real work:

| Structure | File | Cost | Used for |
|---|---|---|---|
| Binary search | `algorithms.py` | O(log n) | finding a key's place inside a B-tree node |
| Merge sort | `algorithms.py` | O(n log n), stable | sorting when there's no B-tree index |
| Hash table | `hashtable.py` | O(1) average | `HashIndex`: "field equals value" |
| B-tree | `btree.py` | O(log n) | `BTreeIndex`: equals, ranges, and sorted order |

- **An index maps a field's values to record keys.** An index on `plan` stores `"PPO" -> {"c1", "c3"}`, so `find plan PPO` is one lookup instead of a scan.
- **Hash vs B-tree.** A hash index is the fastest for exact matches but keeps values in no order, so it can't help with a range. A B-tree keeps values sorted, so a range is O(log n + k) and `sort` reads it in order with no sorting step.
- **A tiny query planner.** `find`, `range` and `sort` each check for an index that can answer them, and fall back to a full scan (plus merge sort) when there isn't one. Every search reports which plan ran, like SQL's `EXPLAIN`. The tests check that both plans always give the same answer.
- **Comparing mixed JSON values.** One field can hold a number in one record and text in another, and Python can't compare `70 < "seventy"`. `index_key` turns each value into a `(type rank, value)` tuple, so null < booleans < numbers < strings < lists < objects. The same key is used for hashing, so both index types and the full scan agree on what "equal" means.
- **Indexes are saved in the log, but not their contents.** `index age btree` appends `{"op": "index", "field": "age", "kind": "btree"}`, and `unindex` appends `{"op": "drop_index", ...}`. On startup, replay rebuilds each index from the records. Compaction writes index lines first. `set` and `delete` lines didn't change, so a Phase 1 or 2 log still opens.
- **The cost of an index.** Every `set` and `delete` updates every index too, and each index uses memory. Indexes speed up reads and slow down writes, which is why you index the fields you search on, not all of them.

### Phase 4 design: SQL on top of the same engine

Phase 4 adds a SQL layer without replacing anything underneath it:

```
SQL text --> lexer --> parser --> statement --> Database.execute() --> Result
                                                   |            |
                                                 Schema       Table --> WriteAheadLog --> clients.jsonl
```

- **Rows are records.** A row is stored as a Record whose key is the primary key (as text) and whose value is a dict of its columns. NULL columns are left out. That's the shape Phase 3 already indexes and searches, so `WHERE`, `ORDER BY` and `CREATE INDEX` reuse `find`, `find_range`, `create_index` and merge sort.
- **Schemas are checked on write.** `CREATE TABLE` saves a schema, and every `INSERT` and `UPDATE` checks types, `NOT NULL` and primary-key uniqueness before anything is written. A FLOAT column turns `25` into `25.0`, and a DATE must be a real ISO date, so values in one column always compare cleanly.
- **One folder, one log per table.** `tinydb_data/_catalog.jsonl` is a log of `create_table` and `drop_table` entries, replayed on startup like every other log. Each table has its own `<name>.jsonl`. Table and column names may only use `a-z`, `0-9` and `_`, so a name can't escape the folder (no path traversal).
- **Lexer, then parser.** The lexer turns text into typed tokens. Anything in single quotes is always one STRING token, so data can never be read as SQL, which is the basis of injection-safe parsing. The parser is recursive descent (one method per grammar rule) and produces frozen statement objects. It never touches data, so the HTTP API reuses it. A script is fully parsed before any statement runs.
- **A small query planner.** For each `WHERE`, the planner picks one access path, fastest first: primary-key lookup (a dict), a hash or B-tree index for `=`, a B-tree index for `<`, `<=`, `>`, `>=`, or a full scan. Then every condition is rechecked on the candidates. (Phase 7's benchmark found that a range used only one of its bounds. See section 4.) The chosen plan is printed after each result, like `EXPLAIN`. `NULL` never matches a comparison, as in standard SQL, and `ORDER BY` puts NULLs last (first with `DESC`).
- **Write-ahead log.** Every change is logged and fsynced before memory changes. A statement that writes several rows (multi-row `INSERT`, `UPDATE`, `DELETE`) is written as one transaction: a `begin` line, the changes, and a `commit` line, with one fsync (group commit). On startup only committed transactions are replayed, and an unfinished one at the end of the log is rolled back and trimmed. That makes each statement atomic. Lines outside a transaction still count on their own, so Phase 1 to 3 logs open unchanged. Compaction rewrites the log without transaction markers, acting as a checkpoint.

**Trade-offs and limits:** no joins, `OR`, `GROUP BY` or multi-statement transactions yet; one primary-key column per table; every table still lives in memory. Each statement is atomic, but a script of several statements is not.

### Phase 5 design: planning the API

Phase 5 is design work, written before the server exists:

- **Requirements with traceability.** Every functional requirement has an ID (`FR-1` to `FR-28`), the phase that built it, and the test file that checks it. Non-functional requirements cover durability, speed, security and maintainability.
- **Diagrams as code.** The architecture diagrams are Mermaid text, so GitHub draws them and they change in the same commits as the code.
- **Reads use HTTP QUERY.** `QUERY` became a standard in June 2026 ([RFC 10008](https://www.rfc-editor.org/rfc/rfc10008)). It's safe and idempotent like `GET`, but the SQL goes in the request body instead of the URL. Because a QUERY must never change data, the server parses the whole body first and refuses it if any statement writes. Writes use `POST /execute`, so the method alone tells clients and caches whether a request changes data.
- **Built on what exists.** The server calls the same `parse` and `Database.execute` as the shell and turn each `Result` into JSON. Nothing below the server changes.

### Phase 6 design: a secure HTTP API

Phase 6 builds the API that Phase 5 designed, then attacks it:

```
curl --> http.server --> ApiHandler: path --> method --> API key --> role --> headers --> body
                                                                                          |
                              JSON <-- Result <-- Database.execute <-- check every statement <-- parse
```

- **Hand-built on `http.server`.** It calls `do_QUERY` for a `QUERY` request, so the method needs no special support. Routing, body checks and JSON are written by hand, and every refusal is an `ApiError` that becomes one JSON error shape. `ThreadingHTTPServer` handles connections on threads, and one lock lets one request at a time use the Database.
- **QUERY is really safe.** The whole body is parsed and every statement checked before the first one runs, so `SELECT ...; DELETE ...;` sent with `QUERY` changes nothing.
- **Authentication and authorization.** API keys are 256-bit random tokens from `secrets`. Only their SHA-256 hashes are stored, so a copied keys file is useless. Plain SHA-256 is enough because the keys are random, unlike passwords. Keys are compared with `hmac.compare_digest`, and the loop never stops early. A `read` key gets `403` on `POST /execute`, so a dashboard can never change a record (least privilege). The key is checked before the body is read, and the log names the key id behind each request.
- **Hardening found a real bug.** `rfile.read(n)` returns whatever arrived, so a dropped connection turned `DELETE FROM clients WHERE id = 5` into `DELETE FROM clients`. Now a short body runs nothing. `test_security.py` has 28 attacks, and 15 of them broke the server before the fixes: slowloris (10-second timeouts), connection floods (32 slots, then `503`), request smuggling (two `Content-Length`s), huge numbers, infinite FLOATs, overlong names, 40,000 statements in one body, terminal escapes in log lines, and a `Server` header that named the Python version. The SQL injection attacks already failed, because the Phase 4 lexer never reads quoted text as SQL.
- **Encryption at rest from the standard library.** Python has no AES built in, so `crypto.py` uses PBKDF2 (600,000 rounds, random salt) to make an encryption key and a MAC key, XORs each line with an HMAC-SHA256 keystream (counter mode), and seals it with an HMAC tag (encrypt-then-MAC). The tag also covers the file name, so lines can't be moved between tables. A real product should use AES-GCM from a reviewed library. This is a learning build, and it says so.
- **Encryption in one place.** Only `Storage` reads and writes lines, so encryption is two methods there, and the WAL, tables, indexes and SQL didn't change. A torn encrypted line fails to decrypt, `DecryptionError` is a `ValueError`, and the existing recovery code trims it. A check value in `_crypto.json` refuses a wrong passphrase **before** replay, so recovery can't mistake a wrong key for a torn write and trim real data.

**Trade-offs and limits:** no HTTPS in the server itself (use a proxy before leaving `127.0.0.1`), and no rate limiting of failed keys. Parameterized queries came in Phase 7. Someone who can write the files can still drop the newest line, roll back the folder, or reorder lines in a file. Line counts, line lengths and table names still show.

### Phase 7 design: measure it, fix it, show it

Phase 7 added three tools on top of the finished engine, and one feature:

- **`sample.py`** makes a made-up Medicare book of business of any size from a seed. It uses its own `random.Random(seed)`, so nothing else in the program can shift the sequence: the same seed gives the same rows on every run and every machine. It loads 500 rows per `INSERT`, because each `INSERT` is one transaction and one `fsync`.
- **`bench.py`** measures through the same entry points users have: `Database.run` for the engine and a real server on a free port for the API. So every number includes parsing and planning. Each benchmark runs several times and reports the **median**, because noise only ever makes a run slower. The garbage collector is paused while a run is timed, as Python's `timeit` does. The output is a Markdown table, which became [benchmarks.md](benchmarks.md).
- **`demo.py`** starts the real server, with a read key and a write key, on a temporary copy of the sample data, and walks through 12 requests, printing each request and its answer. Its questions are asked "as of" a fixed date, so the demo gives the same answers next year. A test checks every status code it gets, so it can't break silently.
- **Parameterized queries.** `?` can stand for any value, and the values travel separately: `run("... WHERE name = ?", ["O'Brien"])`, or over HTTP as `{"sql": "...", "params": [...]}`. The parser fills each `?` straight into the parsed statement, so a value never passes through the lexer and can't become SQL. Phase 6 made the parser injection-safe. This makes the **programs that use tinydb** injection-safe, even ones that handle user input carelessly. The JSON parser refuses numbers over 30 digits (on Python 3.9, converting a million-digit number takes seconds) and `NaN`.

## 4. What measuring taught

All numbers are from `python3 -m tinydb.bench` with 10,000 clients and about 15,000 enrollment periods, on Python 3.9.6 on macOS-27.0.1. [benchmarks.md](benchmarks.md) has every number and how to reproduce them.

**The design claims held up:**

| Claim | Measured |
|---|---|
| Batching rows into one transaction saves fsyncs | 500 rows in one `INSERT` cost **50.5 µs** a row, against **134.0 µs** with one `INSERT` per row: **2.7x** faster |
| An index beats a full scan | Finding a client by birth date: **5.8 ms** with a full scan, **48.9 µs** with a hash index. **119x** faster. |
| The log grows until compaction | Three updates to every client grew the folder from **3.8 MB** to **8.3 MB** and startup from **204.2 ms** to **459.5 ms**. Compaction brought both back. |
| Encryption costs at write and startup, not reads | Loading was **35%** slower and opening **4.7x** slower (356.8 ms of that is PBKDF2, on purpose). Reads were the same speed, because rows live decrypted in memory. |

**Two bugs nobody had seen.** All 458 tests passed going into Phase 7. Both bugs gave the right answer, just slowly, so no test could see them.

1. **The planner used half of a range.** For `deadline >= X AND deadline <= Y`, the planner gave the B-tree only the first condition and checked the other row by row. A one-week range read about 6,000 rows to return 330, so a B-tree range was only **1.3x** faster than a full scan. The fix collects every bound on the column (`range_bounds`). Afterwards: **1.1 ms** instead of **8.3 ms**, and **10x** faster than a full scan. A new test counts the rows a plan reads, which is the test that was missing.
2. **Nagle's algorithm met delayed ACK.** On the Linux test machine, every request on a kept-alive HTTP connection took **43.6 ms**, slower than opening a new connection each time. (This machine's before-the-fix run wasn't recorded.) The server wrote the headers and the body in two writes. TCP's Nagle algorithm held the body back until the client acknowledged the headers, and the client waits up to 40 ms before acknowledging. One line fixed it on every system: `disable_nagle_algorithm = True`, which sets `TCP_NODELAY`. Afterwards: **235.2 µs** per request on my Mac. A regression test sends 20 requests on one connection and fails if they take 40 ms each.

**What the numbers say about the design:** most of a small query's time is Python parsing SQL, not searching. A primary key lookup takes about 40.7 µs end to end, and the dictionary lookup inside it is well under a microsecond. Over HTTP, `http.client`, `http.server` and JSON add about 192.8 µs. Several clients at once were not faster than one, because the clients, the server threads and the one database lock all share a single Python process and its GIL (global interpreter lock). A real load test would run the clients in another process.

**A durability detail the Mac showed.** On my Mac, batching made inserts only 2.7x faster, against about 7x on Linux. macOS `fsync` doesn't wait for the drive to write its own cache to storage; that takes `fcntl(fd, F_FULLFSYNC)`, which Python's `os.fsync` doesn't call. So on macOS, tinydb's "saved" is weaker than on Linux, and a production version would use `F_FULLFSYNC` there.

## 5. The key trade-offs

The full table is in [architecture.md](architecture.md#7-design-decisions). The five that mattered most:

| Decision | Bought | Cost |
|---|---|---|
| Append-only log, replayed on startup | Writes can't damage saved data, and recovery is simple to reason about | Startup time grows with the log until compaction (measured above) |
| Everything in memory | O(1) key lookups and simple code | Data must fit in RAM |
| One transaction per statement, one `fsync` per transaction | Every statement is atomic, and batching is cheap | No transactions that span statements |
| Parser separate from execution | The server checks every statement in a request before running any, so `QUERY` really is safe | One more layer |
| Standard library only | Every part is understood, and nothing to install | Encryption is built from HMAC rather than AES, and says so: production would use AES-GCM from a reviewed library |

## 6. Security in one paragraph

Reads use `QUERY`, and the server refuses a whole request if any statement in it would write. API keys are 256-bit random tokens stored only as SHA-256 hashes, compared in constant time, with read and write roles. The server listens on `127.0.0.1` by default, checks the key before reading a body, and refuses short, slow, oversized and ambiguous requests (28 attack tests, 15 of which broke the first version of the server). Quoted SQL text is always data, and `?` values never reach the lexer at all. With a passphrase, every log line is encrypted and authenticated, and a wrong passphrase is refused before any file is read.

## 7. Limitations

- One process opens a data folder at a time, and one request at a time uses the database.
- No joins, `OR`, `GROUP BY`, or transactions that span statements.
- No HTTPS in the server itself: put a proxy in front before leaving `127.0.0.1`.
- No rate limiting of failed API keys.
- The encryption is a learning build from standard parts, not a reviewed library. Someone who can write the files can still roll the folder back or drop its newest line.

## 8. What I would do next

1. **Automatic compaction**, started when the log is twice the size of the live data, so startup time stays flat.
2. **`OR` and joins** in the SQL, with the planner choosing between indexes.
3. **Rate limiting** failed API keys, per client address.
4. **A load test from a separate process**, to measure the server without sharing a GIL with its clients.
5. **Snapshots plus a shorter log**, so startup doesn't replay history at all.

## 9. What I learned

The hardest phase was Phase 3, because of the algorithms it contains: Deleting from the B-tree took me the longest, the hash table, and merge sort.

The bug that taught me the most was a one-line fix that was easy to miss: the `TCP_NODELAY` line that removed a 40 ms Nagle delay from every kept-alive HTTP request. The tests passed and the answers were right. They were just slow, and only measuring showed it.

What clicked for me was the macOS `fsync` result. Batching made inserts only 2.7x faster on my Mac, against about 7x on Linux. That's because macOS `fsync` doesn't wait for the drive to finish writing. I learned that "saved" doesn't mean the same thing on every system.

If I started again, I would write benchmarks much earlier instead of saving them for the last phase. Both performance bugs had been there for phases, and every test passed, because tests check that answers are right, not that they’re fast. Measuring from Phase 3 on would have caught them right away.