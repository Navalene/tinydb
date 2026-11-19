# tinydb

A tiny database engine built from scratch in pure Python, using only the standard library. It grows phase by phase into a storage engine with indexes, a mini-SQL parser, a write-ahead log, and a hand-built HTTP API.

## Status

**Phase 6: a secure HTTP API.** The engine (tables, schemas, mini-SQL, write-ahead log) is served over HTTP with the `QUERY` method, protected by read-only and read-write API keys, hardened against malformed and malicious requests, and can encrypt its files on disk. 458 tests.

## Quick start

Requires Python 3.8+ and nothing else.

```bash
python3 main.py set name Ada
python3 main.py set plans '["Part A", "Part B"]'
python3 main.py get name
python3 main.py keys
python3 main.py delete name
python3 main.py compact
python3 main.py                       # interactive tinydb> prompt
python3 main.py --db other.jsonl keys # use a different database file
```

Commands: `set`, `get`, `delete`, `exists`, `keys`, `count`, `compact`, `clear`, `help`, `exit`.
Values that are valid JSON (`42`, `true`, `[1,2]`, `{"a":1}`) are stored as that type. Anything else is stored as text.

### Searching

When values are JSON objects, you can search by any field inside them:

```bash
python3 main.py set c1 '{"name": "Ada", "plan": "PPO", "age": 70}'
python3 main.py set c2 '{"name": "Bob", "plan": "HMO", "age": 66}'
python3 main.py find plan PPO          # field equals a value
python3 main.py range age 65 70        # field between two values (use - for no limit)
python3 main.py sort age desc          # sorted by a field
python3 main.py index plan             # hash index: fast find
python3 main.py index age btree        # B-tree index: fast find, range and sort
python3 main.py indexes
python3 main.py unindex plan
```

Every search prints how it ran, for example `(2 records, btree index on age)` or `(1 record, full scan)`.

### SQL

Anything that starts with a SQL word, or ends with `;`, is SQL. SQL tables live in the `tinydb_data/` folder (change it with `--dir`).

```bash
python3 main.py "CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, plan TEXT, age INT);"
python3 main.py "INSERT INTO clients VALUES (1, 'Ada', 'PPO', 70), (2, 'Bob', 'HMO', 66);"
python3 main.py "SELECT name, age FROM clients WHERE age >= 65 ORDER BY age DESC;"
```

```
name | age
-----+----
Ada  | 70
Bob  | 66
(2 rows, full scan + merge sort)
```

In the interactive prompt, a statement can span several lines and ends at `;`.

| Statement | Example |
|---|---|
| `CREATE TABLE` | `CREATE TABLE plans (code TEXT PRIMARY KEY, premium FLOAT NOT NULL)` |
| `DROP TABLE` | `DROP TABLE plans` |
| `CREATE INDEX` | `CREATE INDEX ON clients (age)` (B-tree) or `CREATE INDEX ON clients USING HASH (plan)` |
| `INSERT` | `INSERT INTO clients (id, name) VALUES (3, 'Cy'), (4, 'Di')` |
| `SELECT` | `SELECT * FROM clients WHERE plan = 'PPO' AND age < 80 ORDER BY age LIMIT 10` |
| `UPDATE` | `UPDATE clients SET plan = 'HMO' WHERE id = 1` |
| `DELETE` | `DELETE FROM clients WHERE age < 65` |
| `SHOW TABLES`, `DESCRIBE` | `DESCRIBE clients` |

Types: `INT`, `FLOAT`, `TEXT`, `BOOL` (`TRUE`/`FALSE`), `DATE` (`'YYYY-MM-DD'`). Every table needs one `PRIMARY KEY` column (`INT`, `TEXT` or `DATE`). Columns can be `NOT NULL`. `WHERE` supports `=`, `!=` (or `<>`), `<`, `<=`, `>`, `>=` joined by `AND`. Text uses single quotes, and `''` is a quote inside text: `'O''Brien'`. `--` starts a comment.

### Demo

`demo.sql` builds a small Medicare dataset (plans, clients, enrollment periods), then answers questions like "which clients have a SEP deadline before the end of the year?" and shows the query plan change once an index exists:

```bash
rm -rf demo_data && python3 main.py --dir demo_data < demo.sql
```

### HTTP API

Make a key, start the server, and send SQL with `curl`. Reads use the HTTP `QUERY` method and writes use `POST`:

```bash
python3 main.py --dir demo_data apikey create dashboard --role read    # prints the key once
python3 main.py --dir demo_data serve                                  # http://127.0.0.1:8000
```

In a second Terminal, with the key in `READ_KEY`:

```bash
curl -X QUERY http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $READ_KEY" \
  -H "Content-Type: application/sql" \
  --data-binary "SELECT client_id, reason, deadline FROM enrollment_periods
                 WHERE period = 'SEP' AND deadline >= '2026-11-18' ORDER BY deadline;"
```

```json
{"results": [{"message": "2 rows", "columns": ["client_id", "reason", "deadline"],
  "rows": [[2, "Has Medicaid", "2026-12-15"], [5, "Moved to a new ZIP code", "2026-12-31"]],
  "plan": "btree index on deadline + merge sort"}]}
```

| Command | What it does |
|---|---|
| `python3 main.py serve [--port 8000] [--host 127.0.0.1]` | Run the API on the `--dir` folder |
| `python3 main.py apikey create NAME --role read\|write` | Make a key. Read keys only work with `QUERY /query`. |
| `python3 main.py apikey list` | List keys by id, role and name (never the key) |
| `python3 main.py apikey revoke ID` | Delete a key. It stops working right away, even while the server runs. |

`GET /health` and `OPTIONS` need no key. Everything else is in [docs/api-spec.md](docs/api-spec.md).

### Encryption at rest

Set a passphrase before creating a **new** folder, and every log file in it is encrypted:

```bash
export TINYDB_PASSPHRASE='a long passphrase you will remember'
python3 main.py --dir secure_demo < demo.sql
python3 main.py --dir secure_demo serve
```

The files then hold only base64 lines. Opening the folder later needs the same passphrase, from the variable or a hidden prompt. A wrong one is refused before any file is read. An existing plain folder can't be switched to encrypted: load its data into a new folder instead. The key-value store (`--db`) is not encrypted.

## Design documents

| Document | What it covers |
|---|---|
| [docs/requirements.md](docs/requirements.md) | Users, use cases, and numbered requirements, each traced to the test file that checks it |
| [docs/architecture.md](docs/architecture.md) | Layer, class, sequence and recovery diagrams, and the design decisions with their trade-offs |
| [docs/api-spec.md](docs/api-spec.md) | The HTTP API: `QUERY /query` for reads, `POST /execute` for writes, JSON responses and errors |
| [docs/openapi.yaml](docs/openapi.yaml) | The same API in OpenAPI 3.2, which can describe the `QUERY` method |

## Run the tests

```bash
python3 -m unittest discover -s tests -t .
```

## Project structure

```
main.py               entry point
tinydb/__init__.py    package exports
tinydb/record.py      Record: one key and its value, validated when it's created
tinydb/storage.py     Storage: the append-only log file (append, replay, atomic rewrite)
tinydb/table.py       Table: Records saved through a Storage, plus indexes and search
tinydb/query.py       QueryParser: turns typed commands into Query objects
tinydb/cli.py         runs Queries against a Table, plus the interactive shell
tinydb/algorithms.py  binary search and merge sort, written by hand
tinydb/hashtable.py   HashTable: a hash map with separate chaining and resizing
tinydb/btree.py       BTree: a balanced B-tree with insert, delete and range reads
tinydb/index.py       HashIndex and BTreeIndex on a field inside each value
tinydb/schema.py      Column and Schema: column types, PRIMARY KEY, NOT NULL, row checks
tinydb/wal.py         WriteAheadLog: Storage plus begin/commit transactions and crash recovery
tinydb/lexer.py       splits SQL text into tokens
tinydb/sql.py         SqlParser: recursive-descent parser, tokens into statement objects
tinydb/database.py    Database: tables in a folder, runs statements, picks a query plan, opens encrypted folders
tinydb/server.py      ApiServer: the HTTP API on http.server (QUERY /query, POST /execute), limits, JSON
tinydb/auth.py        KeyStore: API keys stored as hashes, with read and write roles
tinydb/crypto.py      Cipher: encryption at rest from HMAC-SHA256 and PBKDF2
demo.sql              Medicare demo script
docs/                 requirements, architecture and API spec
tests/                one test file per module, plus test_cli.py and test_security.py
```

## Phase 1 design: an append-only log

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

## Phase 2 design: one job per class

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

## Phase 3 design: indexes

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

## Phase 4 design: SQL on top of the same engine

Phase 4 adds a SQL layer without replacing anything underneath it:

```
SQL text --> lexer --> parser --> statement --> Database.execute() --> Result
                                                   |            |
                                                 Schema       Table --> WriteAheadLog --> clients.jsonl
```

- **Rows are records.** A row is stored as a Record whose key is the primary key (as text) and whose value is a dict of its columns. NULL columns are left out. That's the shape Phase 3 already indexes and searches, so `WHERE`, `ORDER BY` and `CREATE INDEX` reuse `find`, `find_range`, `create_index` and merge sort.
- **Schemas are checked on write.** `CREATE TABLE` saves a schema, and every `INSERT` and `UPDATE` checks types, `NOT NULL` and primary-key uniqueness before anything is written. A FLOAT column turns `25` into `25.0`, and a DATE must be a real ISO date, so values in one column always compare cleanly.
- **One folder, one log per table.** `tinydb_data/_catalog.jsonl` is a log of `create_table` and `drop_table` entries, replayed on startup like every other log. Each table has its own `<name>.jsonl`. Table and column names may only use `a-z`, `0-9` and `_`, so a name can't escape the folder (no path traversal).
- **Lexer, then parser.** The lexer turns text into typed tokens. Anything in single quotes is always one STRING token, so data can never be read as SQL, which is the basis of injection-safe parsing. The parser is recursive descent (one method per grammar rule) and produces frozen statement objects. It never touches data, so the coming HTTP API can reuse it. A script is fully parsed before any statement runs.
- **A small query planner.** For each `WHERE`, the planner picks one access path, fastest first: primary-key lookup (a dict), a hash or B-tree index for `=`, a B-tree index for `<`, `<=`, `>`, `>=`, or a full scan. Then every condition is rechecked on the candidates. The chosen plan is printed after each result, like `EXPLAIN`. `NULL` never matches a comparison, as in standard SQL, and `ORDER BY` puts NULLs last (first with `DESC`).
- **Write-ahead log.** Every change is logged and fsynced before memory changes. A statement that writes several rows (multi-row `INSERT`, `UPDATE`, `DELETE`) is written as one transaction: a `begin` line, the changes, and a `commit` line, with one fsync (group commit). On startup only committed transactions are replayed, and an unfinished one at the end of the log is rolled back and trimmed. That makes each statement atomic. Lines outside a transaction still count on their own, so Phase 1 to 3 logs open unchanged. Compaction rewrites the log without transaction markers, acting as a checkpoint.

**Trade-offs and limits:** no joins, `OR`, `GROUP BY` or multi-statement transactions yet; one primary-key column per table; every table still lives in memory. Each statement is atomic, but a script of several statements is not.

## Phase 5 design: planning the API

Phase 5 is design work, written before the server exists:

- **Requirements with traceability.** Every functional requirement has an ID (`FR-1` to `FR-28`), the phase that built it, and the test file that checks it. Non-functional requirements cover durability, speed, security and maintainability.
- **Diagrams as code.** The architecture diagrams are Mermaid text, so GitHub draws them and they change in the same commits as the code.
- **Reads use HTTP QUERY.** `QUERY` became a standard in June 2026 ([RFC 10008](https://www.rfc-editor.org/rfc/rfc10008)). It's safe and idempotent like `GET`, but the SQL goes in the request body instead of the URL. Because a QUERY must never change data, the server parses the whole body first and refuses it if any statement writes. Writes use `POST /execute`, so the method alone tells clients and caches whether a request changes data.
- **Built on what exists.** The server will call the same `parse` and `Database.execute` as the shell and turn each `Result` into JSON. Nothing below the server changes.

## Phase 6 design: a secure HTTP API

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

**Trade-offs and limits:** no HTTPS in the server itself (use a proxy before leaving `127.0.0.1`), no rate limiting of failed keys, and no parameterized queries yet. Someone who can write the files can still drop the newest line, roll back the folder, or reorder lines in a file. Line counts, line lengths and table names still show.

## Roadmap

1. Key-value store, CLI, append-only log, compaction
2. Object-oriented refactor: Storage, Table, Record, QueryParser, unit tests
3. Hash index, then B-tree index, sorting and search
4. Tables, schemas, mini-SQL parser (CREATE, INSERT, SELECT ... WHERE), write-ahead log
5. Requirements doc, architecture diagram, API spec
6. API keys, authentication, input validation, injection-safe parsing, encryption at rest
7. Benchmarks, design write-up, and a demo using a Medicare clients, plans, and enrollment periods dataset
