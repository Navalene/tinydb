# tinydb requirements

| | |
|---|---|
| **Document** | Software requirements specification |
| **Version** | 1.0 (Phase 7) |
| **Date** | 2026-11-20 |
| **Status** | All 7 phases built and tested (540 tests): the engine, the HTTP API with API keys and encryption at rest, parameterized queries, benchmarks and a demo. |

## 1. Purpose and scope

tinydb is a small database engine written from scratch in pure Python. It stores data in an append-only log, rebuilds its state from that log on startup, indexes data with a hand-built hash table and B-tree, and runs a mini-SQL language with typed tables and atomic writes.

This document says **what** tinydb must do and **how well**, so every feature can be checked against a test. It covers the engine (Phases 1 to 4), the HTTP API and its security (Phase 6), and parameterized queries, benchmarks and the demo (Phase 7). [architecture.md](architecture.md) explains how it is built, and [api-spec.md](api-spec.md) defines the HTTP API.

## 2. Users

| User | What they need |
|---|---|
| **Developer** (the author) | Learn how databases work by building one, and explain every part in an interview. |
| **Application developer** | Store and query data from a Python program, the shell, or over HTTP. |
| **Medicare agent** (demo user) | Find clients, plans and enrollment deadlines quickly, such as clients with a SEP deadline this month. |
| **Reviewer** (instructor, interviewer) | Run the tests and the demo, and trace each requirement to code and a test. |

## 3. Use cases

**UC-1 Store and read a value.** The developer runs `python3 main.py set plan PPO`, then `get plan`, and sees `"PPO"`. After closing and reopening the program, `get plan` still shows `"PPO"`.

**UC-2 Define a table.** The developer runs `CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, ...)`. The table exists until it is dropped, including after a restart.

**UC-3 Add and change client data.** The developer inserts several clients in one `INSERT`. If any row is invalid (wrong type, missing `NOT NULL` value, duplicate primary key), no rows are saved and the error says which column is wrong.

**UC-4 Find clients with an upcoming SEP deadline.** The agent asks for every SEP enrollment period with a deadline between today and December 31, ordered by deadline. tinydb returns the rows and says how it found them (full scan, or a B-tree index on `deadline`).

**UC-5 Recover after a crash.** The power fails in the middle of a write. On the next start, every committed change is there, and the half-written change is gone as if it never started.

**UC-6 Query over HTTP.** An application with a read-only API key sends `SELECT` SQL in the body of an HTTP `QUERY` request and gets the rows back as JSON.

**UC-7 Change data over HTTP.** An application with a read-write API key sends `INSERT`, `UPDATE` or `DELETE` SQL with `POST` and gets back a message such as `Inserted 3 rows`.

**UC-8 Keep client data private on disk.** The agent's laptop is lost. The database folder was opened with a passphrase, so the files hold no readable names, birth dates or Medicaid status, and they can't be opened without the passphrase.

**UC-9 Search by what a user typed.** An application lets the agent search clients by name. It sends `SELECT ... WHERE name = ?` with the typed name as a separate value. A name like `O'Brien` works without escaping, and input like `x'; DROP TABLE clients; --` finds nobody and changes nothing.

**UC-10 Show and measure the project.** A reviewer runs `python3 -m tinydb.demo` and watches the API answer real requests, then runs `python3 -m tinydb.bench` and gets a table of timings for their own machine.

## 4. Functional requirements

"Must" is required. "Should" is wanted but can wait. Each requirement is checked by the test file shown.

### 4.1 Storage and recovery

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-1 | Must save every change as one JSON line appended to a log file, and never overwrite existing lines. | 1 | `test_storage.py` |
| FR-2 | Must rebuild all data on startup by replaying the log from top to bottom. | 1 | `test_storage.py`, `test_table.py` |
| FR-3 | Must ignore a torn (half-written) last line, and trim it on the next start. | 1 | `test_storage.py` |
| FR-4 | Must raise an error, not skip, when a line in the middle of the log is corrupt. | 1 | `test_storage.py` |
| FR-5 | Must compact the log to one line per live key, swapping the file in atomically. | 1 | `test_storage.py`, `test_table.py` |

### 4.2 Key-value store

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-6 | Must support `set`, `get`, `delete`, `exists`, `keys`, `count`, `compact` and `clear`. | 1 | `test_table.py`, `test_cli.py` |
| FR-7 | Must store any JSON value, and reject an empty or non-string key. | 2 | `test_record.py` |

### 4.3 Indexes and search

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-8 | Must find records by a field inside their values (`find`), by range (`range`), and in sorted order (`sort`). | 3 | `test_table.py` |
| FR-9 | Must build a hash index for equality and a B-tree index for equality, ranges and order, both written by hand. | 3 | `test_hashtable.py`, `test_btree.py`, `test_index.py` |
| FR-10 | Must give the same answer with or without an index, and report which plan ran. | 3 | `test_table.py` |
| FR-11 | Must save index definitions in the log and rebuild index contents on startup. | 3 | `test_table.py` |

### 4.4 SQL

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-12 | Must run `CREATE TABLE`, `DROP TABLE`, `CREATE INDEX`, `INSERT`, `SELECT`, `UPDATE`, `DELETE`, `SHOW TABLES` and `DESCRIBE`. | 4 | `test_sql.py`, `test_database.py` |
| FR-13 | Must support the types `INT`, `FLOAT`, `TEXT`, `BOOL` and `DATE`, one `PRIMARY KEY` column per table, and `NOT NULL`. | 4 | `test_schema.py` |
| FR-14 | Must check every row against the schema before writing, and reject wrong types, missing `NOT NULL` values and duplicate primary keys. | 4 | `test_schema.py`, `test_database.py` |
| FR-15 | `SELECT` must support `WHERE` with `=`, `!=`, `<`, `<=`, `>`, `>=` joined by `AND`, plus `ORDER BY` and `LIMIT`. | 4 | `test_sql.py`, `test_database.py` |
| FR-16 | Must pick the fastest access path for `WHERE` (primary key, index, or full scan) and report it. | 4 | `test_database.py` |
| FR-17 | Must report SQL errors with what was expected and where, without running any statement of a script that doesn't parse. | 4 | `test_lexer.py`, `test_sql.py` |

### 4.5 Transactions

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-18 | Must write every change to the log, and force it to disk, before changing memory (write-ahead). | 4 | `test_wal.py` |
| FR-19 | Must make each statement atomic: a multi-row write is saved completely or not at all. | 4 | `test_wal.py`, `test_transaction.py` |
| FR-20 | Must roll back and trim an unfinished transaction at the end of the log on startup. | 4 | `test_wal.py` |
| FR-21 | Must still open logs written by Phases 1 to 3. | 4 | `test_wal.py` |

### 4.6 Shell

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-22 | Must run one command from the command line, or an interactive `tinydb>` prompt. | 1 | `test_cli.py` |
| FR-23 | Must run SQL typed at the prompt, passed as an argument, or piped in from a file. | 4 | `test_cli.py` |

### 4.7 HTTP API

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-24 | Must accept read SQL (`SELECT`, `SHOW TABLES`, `DESCRIBE`) in the body of an HTTP `QUERY` request, and return JSON. | 6 | `test_server.py` |
| FR-25 | Must refuse any statement that changes data inside a `QUERY` request, because `QUERY` is a safe method. | 6 | `test_server.py` |
| FR-26 | Must accept write SQL with `POST`, and refuse read statements there. | 6 | `test_server.py` |
| FR-27 | Must return errors as JSON with a matching HTTP status code. | 6 | `test_server.py` |
| FR-28 | Must require an API key on every request except the health check and `OPTIONS`. | 6 | `test_server.py` |
| FR-29 | Must give each key a role: `read` keys may only use `QUERY /query`, and `write` keys may also use `POST /execute`. | 6 | `test_server.py` |
| FR-30 | Must store only a hash of each key, show the key once when it's made, and let a key be revoked while the server runs. | 6 | `test_auth.py` |
| FR-31 | Must run nothing from a request whose body is cut short, sent too slowly, too large, or has an ambiguous length. | 6 | `test_security.py` |
| FR-32 | Must limit statements per request, open connections, name length and number size. | 6 | `test_security.py` |

### 4.8 Encryption at rest

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-33 | Must encrypt every log line in a folder opened with a passphrase, including the catalog and transaction markers. | 6 | `test_encryption.py` |
| FR-34 | Must detect any change to an encrypted line, and a line moved from another table's file. | 6 | `test_crypto.py`, `test_encryption.py` |
| FR-35 | Must refuse a wrong or missing passphrase before reading any log, and change no file. | 6 | `test_encryption.py` |
| FR-36 | Must read the passphrase from `TINYDB_PASSPHRASE` or a hidden prompt, never from a command-line argument. | 6 | `test_cli.py` |

### 4.9 Parameterized queries

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-37 | Must accept a `?` placeholder anywhere a value can go (`WHERE`, `VALUES`, `SET`), filled in order from values passed separately, which never pass through the lexer. | 7 | `test_params.py` |
| FR-38 | Must refuse, before running anything, too few or too many values, values that aren't a list, and values that aren't a string, number, boolean or null. | 7 | `test_params.py` |
| FR-39 | Must accept SQL with values as an `application/json` body (`sql`, `params`) on both API paths, and refuse unknown fields, numbers over 30 digits, `NaN` and `Infinity`. | 7 | `test_params.py` |

### 4.10 Performance and showcase

| ID | Requirement | Phase | Checked by |
|---|---|---|---|
| FR-40 | Must give a B-tree both ends of a range on its column (`deadline >= X AND deadline <= Y`), so it reads only the rows in the range. | 7 | `test_planner.py` |
| FR-41 | Must generate the same made-up Medicare data of any size from a seed. | 7 | `test_sample.py` |
| FR-42 | Must benchmark inserts, lookups, ranges, startup, compaction, HTTP and encryption, and print the results as a Markdown table. | 7 | `test_bench.py` |
| FR-43 | Must include a guided demo that runs the real server and checks every answer it shows. | 7 | `test_demo.py` |

## 5. Non-functional requirements

| ID | Quality | Requirement |
|---|---|---|
| NFR-1 | Durability | A write is reported as done only after it has been `fsync`ed to disk. |
| NFR-2 | Reliability | A crash at any point never leaves a half-applied statement after restart. |
| NFR-3 | Performance | A primary-key lookup is O(1). An indexed `=` or range is O(log n + k), where k is the rows returned. Measured numbers are in [benchmarks.md](benchmarks.md). |
| NFR-4 | Portability | Runs on Python 3.8 or newer on macOS and Linux, with only the standard library. |
| NFR-5 | Security | Table and column names may only use `a-z`, `0-9` and `_`, so a name can't become a path outside the data folder. |
| NFR-6 | Security | Quoted text is always data and never SQL, and `?` values never pass through the lexer at all, so input can't change what a statement does (no SQL injection). |
| NFR-7 | Security | The server listens on `127.0.0.1` by default, never sends a traceback or its Python version, and sends `nosniff` and `Content-Security-Policy` headers. |
| NFR-8 | Maintainability | One module per job, one test file per module, and 0 problems in VS Code (Pylance). |
| NFR-9 | Testability | The engine never prints. It returns Result objects, so it can be tested without capturing output. |
| NFR-10 | Usability | Every error says what went wrong in plain words, such as `Column age is INT, so it can't hold 'old'`. |
| NFR-11 | Security | Secrets are compared in constant time (`hmac.compare_digest`), and random values come from `secrets`, never `random`. |
| NFR-12 | Performance | Requests on a kept-alive HTTP connection are never held back by Nagle's algorithm (the server sets `TCP_NODELAY`). Checked by `KeepAliveTest` in `test_bench.py`. |

## 6. Constraints

- **No outside packages.** No SQLite, ORM, database library or web framework. Only the Python standard library.
- **Single process.** One program opens a data folder at a time. Two programs writing the same folder is not supported.
- **In memory.** All rows and indexes are held in memory, so the data must fit in RAM.
- **Hand-built HTTP.** The API uses `http.server` from the standard library.

## 7. Out of scope

Joins, `OR`, `GROUP BY` and aggregate functions, transactions that span several statements, replication, HTTPS in the server itself (use a proxy), and user accounts beyond API keys. These are good next steps, but tinydb doesn't need them to meet its goals.

## 8. Glossary

| Term | Meaning |
|---|---|
| Append-only log | A file that only ever grows at the end. Old lines are never changed. |
| Replay | Rebuilding the current state by applying every log line in order. |
| Compaction | Rewriting the log with only the current data, to make it smaller. |
| Index | A structure that maps a field's values to records, so a search doesn't read every record. |
| Query plan | The way a statement finds its rows: primary key, index, or full scan. |
| WAL | Write-ahead log: changes are logged before they are applied, so they can be recovered. |
| Atomic | All or nothing: a statement's changes are saved together, or none are. |
| Safe method | An HTTP method that only reads, so a client can repeat or cache it. `GET` and `QUERY` are safe. `POST` is not. |
| API key | A long random secret that a program sends with each request to prove it's allowed in. |
| Encryption at rest | Data is encrypted in the files on disk, so a copy of the files can't be read without the key. |
| SEP | Special Enrollment Period: a Medicare window, with a deadline, for changing plans after a life event. |
| Parameterized query | SQL with a `?` where each value goes, and the values sent separately, so they can never become SQL. |
| Benchmark | A repeatable measurement of how long something takes, reported as the median of several runs. |
| Nagle's algorithm | A TCP feature that holds back small writes until earlier ones are acknowledged. Turned off with `TCP_NODELAY`. |
