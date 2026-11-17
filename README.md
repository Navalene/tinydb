# tinydb

A tiny database engine built from scratch in pure Python, using only the standard library. It grows phase by phase into a storage engine with indexes, a mini-SQL parser, a write-ahead log, and a hand-built HTTP API.

## Status

**Phase 1: key-value store, CLI, append-only log, compaction**

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

## Run the tests

```bash
python3 -m unittest discover -s tests -t .
```

## Project structure

```
main.py              entry point
tinydb/__init__.py   package exports
tinydb/store.py      KeyValueStore: append-only log, replay, compaction
tinydb/cli.py        one-shot commands and the interactive shell
tests/test_store.py  unit tests for the store, the log, compaction, and the CLI
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

**Trade-offs:** writes are fast because appending is O(1), but startup time grows with the size of the log, and every key has to fit in memory. Compaction fixes the first problem. Indexes in Phase 3 start on the second.

## Roadmap

1. Key-value store, CLI, append-only log, compaction
2. Object-oriented refactor: Storage, Table, Record, QueryParser, unit tests
3. Hash index, then B-tree index, sorting and search
4. Tables, schemas, mini-SQL parser (CREATE, INSERT, SELECT ... WHERE), write-ahead log
5. Requirements doc, architecture diagram, API spec
6. API keys, authentication, input validation, injection-safe parsing, encryption at rest
7. Benchmarks, design write-up, and a demo using a Medicare clients, plans, and enrollment periods dataset