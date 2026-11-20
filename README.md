# tinydb

A database engine built from scratch in pure Python, using only the standard library: no SQLite, no ORM, no web framework. An append-only log with crash recovery, hand-built hash and B-tree indexes, a mini-SQL language with typed tables, a write-ahead log, and an HTTP API that reads with the new `QUERY` method, protected by API keys, with parameterized queries and encryption at rest.

Built in seven phases alongside a computer science degree, with **540 tests**. Every example uses made-up Medicare data: plans, clients, and their enrollment periods.

## Highlights

- **Storage engine:** every change is appended to a JSON-lines log and `fsync`ed. State is rebuilt by replaying the log, a torn last line is trimmed, and compaction rewrites the log atomically.
- **Indexes written by hand:** a hash table with chaining and resizing, and a B-tree with insert, delete and range reads. Every query reports the plan it used.
- **Mini-SQL:** `CREATE TABLE`, `INSERT`, `SELECT ... WHERE ... ORDER BY ... LIMIT`, `UPDATE`, `DELETE`, `CREATE INDEX`, with typed columns, `PRIMARY KEY` and `NOT NULL`. A recursive-descent parser and a small query planner.
- **Atomic statements:** a write-ahead log with `begin` and `commit`. A crash mid-statement leaves all of it or none of it.
- **HTTP API on `http.server`:** reads use `QUERY` ([RFC 10008](https://www.rfc-editor.org/rfc/rfc10008)) and can never change data; writes use `POST`. Hashed API keys with read and write roles, request limits, and 28 attack tests.
- **Parameterized queries:** `?` placeholders whose values never pass through the lexer, so they can't become SQL.
- **Encryption at rest** from the standard library (PBKDF2, HMAC-SHA256 in counter mode, encrypt-then-MAC).
- **Measured:** benchmarks found two performance bugs the tests couldn't see, because both gave correct answers slowly. See [Benchmarks](#benchmarks).

## Try it in two minutes

Requires Python 3.8+ and nothing else.

```bash
python3 -m tinydb.demo       # a guided tour of the HTTP API: press Enter between steps
python3 -m tinydb.bench      # benchmarks on your machine (about 30 seconds)
```

The demo builds 2,000 made-up clients in a temporary folder, starts the real server with a read key and a write key, and walks through 12 requests: authentication, roles, the `QUERY` method, the soonest SEP deadlines with `?` placeholders, an index changing the query plan, an injection attempt, and a bad date being refused. It prints every request and answer.

## Quick start

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

### The SQL demo script

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

When values come from users, send **parameterized queries**: a `?` for each value, and the values separately as JSON. A value can never become SQL, and `O'Brien` needs no escaping:

```bash
curl -X QUERY http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $READ_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{"sql": "SELECT id, name, zip FROM clients WHERE name = ? AND zip = ?", "params": ["Eve O'Brien", "33139"]}
EOF
```

In Python, the same thing is `Database("demo_data").run("SELECT ... WHERE name = ?", ["Eve O'Brien"])`.

`GET /health` and `OPTIONS` need no key. Everything else is in [docs/api-spec.md](docs/api-spec.md).

### Encryption at rest

Set a passphrase before creating a **new** folder, and every log file in it is encrypted:

```bash
export TINYDB_PASSPHRASE='a long passphrase you will remember'
python3 main.py --dir secure_demo < demo.sql
python3 main.py --dir secure_demo serve
```

The files then hold only base64 lines. Opening the folder later needs the same passphrase, from the variable or a hidden prompt. A wrong one is refused before any file is read. An existing plain folder can't be switched to encrypted: load its data into a new folder instead. The key-value store (`--db`) is not encrypted.

## Benchmarks

10,000 clients and about 15,000 enrollment periods, median of 5 runs, on Python 3.9.6 on macOS-27.0.1. Full results and method: [docs/benchmarks.md](docs/benchmarks.md).

| What | Result |
|---|---|
| One fsync for 500 rows instead of 500 | **2.7x** faster inserts (50.5 µs a row, against 134.0 µs) |
| Hash index instead of a full scan | **119x** faster (48.9 µs per query, against 5.8 ms) |
| Range planner fix (found by benchmarking) | One-week range on a B-tree: **8.3 ms to 1.1 ms** |
| `TCP_NODELAY` fix (found by benchmarking) | Kept-alive `QUERY` request: **43.6 ms to 327.4 µs** on the Linux test machine |
| HTTP over an in-process query | About **192.8 µs** per request |

Before Phase 7, a range query on an indexed column used only one of its two bounds, and kept-alive HTTP requests waited about 40 ms each on Nagle's algorithm on Linux (now **327.4 µs** there). All 458 tests passed with both bugs, because the answers were right. Each fix now has a regression test.

## Design

The design write-up, [docs/design.md](docs/design.md), walks through every phase: why an append-only log, how the indexes and the planner work, why `QUERY`, how the API is secured, and what measuring taught. The short version:

| Decision | Why | Trade-off |
|---|---|---|
| Append-only log, replayed on startup | A crash can't damage saved data | Startup grows with the log until compaction |
| Everything in memory | O(1) key lookups, simple code | Data must fit in RAM |
| One transaction and one `fsync` per statement | Every statement is atomic, batching is cheap | No transactions across statements |
| Parser separate from execution | The server checks a whole request before running any of it | One more layer |
| Standard library only | Every part is understood | Encryption is built from HMAC, not AES, and says so |

## Documents

| Document | What it covers |
|---|---|
| [docs/design.md](docs/design.md) | The design write-up: each phase's design, the trade-offs, what measuring taught, and what's next |
| [docs/benchmarks.md](docs/benchmarks.md) | Every benchmark result, the method, and the before-and-after of the two fixes |
| [docs/requirements.md](docs/requirements.md) | Users, use cases, and numbered requirements, each traced to the test file that checks it |
| [docs/architecture.md](docs/architecture.md) | Layer, class, sequence and recovery diagrams, and every design decision with its trade-off |
| [docs/api-spec.md](docs/api-spec.md) | The HTTP API: `QUERY /query` for reads, `POST /execute` for writes, parameterized queries, JSON responses and errors |
| [docs/openapi.yaml](docs/openapi.yaml) | The same API in OpenAPI 3.2, which can describe the `QUERY` method |

## Run the tests

```bash
python3 -m unittest discover -s tests -t .
```

540 tests, standard library `unittest` only.

## Project structure

```
main.py               entry point
tinydb/__init__.py    package exports and version
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
tinydb/sql.py         SqlParser: recursive-descent parser, tokens into statements, ? placeholders
tinydb/database.py    Database: tables in a folder, runs statements, picks a query plan, opens encrypted folders
tinydb/server.py      ApiServer: the HTTP API on http.server (QUERY /query, POST /execute), limits, JSON
tinydb/auth.py        KeyStore: API keys stored as hashes, with read and write roles
tinydb/crypto.py      Cipher: encryption at rest from HMAC-SHA256 and PBKDF2
tinydb/sample.py      made-up Medicare data of any size, from a seed
tinydb/bench.py       benchmarks, printed as a Markdown table
tinydb/demo.py        a guided tour of the HTTP API against a real server
demo.sql              Medicare demo script for the shell
docs/                 design write-up, benchmarks, requirements, architecture and API spec
tests/                one test file per module, plus test_cli.py, test_security.py and test_planner.py
```

## Roadmap

All seven phases are done:

1. Key-value store, CLI, append-only log, compaction
2. Object-oriented refactor: Storage, Table, Record, QueryParser, unit tests
3. Hash index, then B-tree index, sorting and search
4. Tables, schemas, mini-SQL parser (CREATE, INSERT, SELECT ... WHERE), write-ahead log
5. Requirements doc, architecture diagram, API spec
6. API keys, authentication, input validation, injection-safe parsing, encryption at rest
7. Benchmarks, parameterized queries, a demo, and the design write-up

Next ideas are in [docs/design.md](docs/design.md#8-what-i-would-do-next): automatic compaction, `OR` and joins, rate limiting failed keys, and snapshots.