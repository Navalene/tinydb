# tinydb HTTP API specification

| | |
|---|---|
| **Document** | HTTP API specification |
| **Version** | 0.6 (Phase 6). Designed in Phase 5, built in Phase 6 (`tinydb/server.py`). |
| **Date** | 2026-11-19 |
| **Related** | [requirements.md](requirements.md) (FR-24 to FR-28), [architecture.md](architecture.md), [openapi.yaml](openapi.yaml) |

## 1. Overview

The API sends tinydb's mini-SQL over HTTP and returns JSON. The HTTP method tells you whether a request can change data:

| Method and path | Use | Changes data? |
|---|---|---|
| `QUERY /query` | Read: `SELECT`, `SHOW TABLES`, `DESCRIBE` | Never |
| `POST /execute` | Write: `CREATE TABLE`, `DROP TABLE`, `CREATE INDEX`, `INSERT`, `UPDATE`, `DELETE` | Yes |
| `OPTIONS /query`, `OPTIONS /execute` | Ask which methods and body formats a path accepts | Never |
| `GET /health` | Check the server is running. Not a query: it reads no table data. | Never |

**Base URL:** `http://127.0.0.1:8000`. The server only listens on this computer unless it's started with another host.

**Starting it:**

```bash
python3 main.py apikey create dashboard --role read      # prints a key once
python3 main.py apikey create intake-app --role write
python3 main.py serve                                    # or: python3 main.py --dir demo_data serve --port 9000
```

The examples below use `$READ_KEY` and `$WRITE_KEY` for those two keys.

### Why QUERY

`QUERY` is a standard HTTP method, defined in [RFC 10008](https://www.rfc-editor.org/rfc/rfc10008) (June 2026). Like `GET`, it is **safe** (it never changes data) and **idempotent** (sending it twice gives the same effect as once), so clients may retry it and caches may store it. Unlike `GET`, it carries the query in the **request body**, so a long `SELECT` doesn't have to fit in a URL, isn't written to access logs as part of one, and needs no URL encoding.

Because QUERY must be safe, the server **parses the whole body first** and refuses the request if any statement would change data. Nothing runs in that case.

## 2. Requests

| Header | Required | Value |
|---|---|---|
| `Content-Type` | Yes, for `QUERY` and `POST` | `application/sql` (`charset=utf-8` may be added) |
| `Content-Length` | Yes, for `QUERY` and `POST` | Size of the body in bytes. At most **1,048,576** (1 MB). |
| `Authorization` | Yes, except for `GET /health` and `OPTIONS` | `Bearer <api key>`. A `read` key works on `/query`. A `write` key works on both paths. |

Send exactly one `Content-Length` and no `Transfer-Encoding`. The body must arrive in full: if the connection ends early, nothing runs.

The body is UTF-8 SQL text: one statement, or several (at most **100**) separated by `;`. Text values use single quotes (`'O''Brien'` for a quote inside text), and `--` starts a comment, the same as in the shell.

## 3. Responses

Every response body is JSON with `Content-Type: application/json; charset=utf-8`, except `204` responses, which have no body. Every JSON response also has `Cache-Control: no-store` (the data can change with the next write), `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (a browser must never run or frame it). The `Server` header names tinydb's version but not Python's.

### 3.1 Success

```json
{
  "results": [
    {
      "message": "2 rows",
      "columns": ["client_id", "reason", "deadline"],
      "rows": [
        [2, "Has Medicaid", "2026-12-15"],
        [5, "Moved to a new ZIP code", "2026-12-31"]
      ],
      "plan": "btree index on deadline + merge sort"
    }
  ]
}
```

`results` has one entry per statement, in order. Each entry is a `Result` from `Database.execute`:

| Field | Type | Meaning |
|---|---|---|
| `message` | string | What happened, like `2 rows` or `Inserted 3 rows` |
| `columns` | array of strings | Column names. Empty for statements that return no rows. |
| `rows` | array of arrays | One array per row, values in the same order as `columns` |
| `plan` | string | How the rows were found, like `primary key lookup`. Empty when no rows were searched. |

**Values in JSON:**

| SQL type | JSON | Example |
|---|---|---|
| `INT` | number | `70` |
| `FLOAT` | number | `29.5` |
| `TEXT` | string | `"Ada Reyes"` |
| `BOOL` | `true` or `false` | `true` |
| `DATE` | string, `YYYY-MM-DD` | `"2026-12-15"` |
| `NULL` | `null` | `null` |

### 3.2 Errors

```json
{
  "error": {
    "type": "SchemaError",
    "message": "Column birth_date is DATE, so use 'YYYY-MM-DD', not '03/01/1958'"
  }
}
```

`type` is a fixed word a program can check, and `message` is for people.

| Status | `type` | When |
|---|---|---|
| `400` | `SqlError` | The SQL can't be read, like `SELECT * FROM clients WHERE` |
| `400` | `SchemaError` | A value or column doesn't fit the table, like a bad DATE or `No column named nme` |
| `400` | `DatabaseError` | The statement can't run, like `No table named nope` or a duplicate primary key |
| `400` | `WrongMethod` | A write was sent with `QUERY`, or a read with `POST`. The message says which method to use. |
| `400` | `EmptyRequest` | The body holds no statement |
| `400` | `BadRequest` | The body isn't valid UTF-8, ended before `Content-Length` bytes, has over 100 statements, or the length headers are ambiguous (two `Content-Length`s, or one with `Transfer-Encoding`) |
| `401` | `Unauthorized` | Missing, wrong or revoked API key. Sent with `WWW-Authenticate: Bearer realm="tinydb"` (plus `error="invalid_token"` when a key was sent). |
| `403` | `Forbidden` | A `read` key was used with `POST /execute` |
| `404` | `NotFound` | No such path |
| `405` | `MethodNotAllowed` | The path doesn't take this method, like `GET /query`. Sent with an `Allow` header. |
| `408` | `Timeout` | The body didn't arrive within 10 seconds |
| `411` | `LengthRequired` | No `Content-Length` header |
| `413` | `TooLarge` | Body over 1 MB |
| `415` | `UnsupportedMediaType` | `Content-Type` isn't `application/sql`. Sent with `Accept-Query` (on `/query`). |
| `500` | `InternalError` | A bug in tinydb. The message is generic, so no internal details leak out. |
| `501` | (no JSON) | A method tinydb doesn't know at all, like `PUT`. `http.server` sends this itself. |
| `503` | `Busy` | 32 connections are already open. Sent with `Retry-After: 1`. |

## 4. Endpoints

### 4.1 `QUERY /query`

Runs one or more read statements: `SELECT`, `SHOW TABLES`, `DESCRIBE`.

```bash
curl -X QUERY http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $READ_KEY" \
  -H "Content-Type: application/sql" \
  --data-binary "SELECT client_id, reason, deadline FROM enrollment_periods
                 WHERE period = 'SEP' AND deadline >= '2026-11-18' AND deadline <= '2026-12-31'
                 ORDER BY deadline;"
```

`200 OK`, with the response shown in [3.1](#31-success). The response also includes `Accept-Query: application/sql`.

Two reads in one request:

```bash
curl -X QUERY http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $READ_KEY" \
  -H "Content-Type: application/sql" \
  --data-binary "SHOW TABLES; SELECT name, plan_code FROM clients WHERE id = 2;"
```

```json
{
  "results": [
    {"message": "3 tables", "columns": ["table"],
     "rows": [["clients"], ["enrollment_periods"], ["plans"]], "plan": ""},
    {"message": "1 row", "columns": ["name", "plan_code"],
     "rows": [["Bob Okafor", "H1001-002"]], "plan": "primary key lookup"}
  ]
}
```

A write sent with QUERY is refused, and nothing in the body runs:

```bash
curl -X QUERY http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $READ_KEY" \
  -H "Content-Type: application/sql" \
  --data-binary "SELECT * FROM clients; DELETE FROM clients;"
```

`400 Bad Request`

```json
{"error": {"type": "WrongMethod", "message": "DELETE changes data, so send it with POST /execute. Nothing was run."}}
```

To run a saved query from a file, use `--data-binary @sep_deadlines.sql`.

### 4.2 `POST /execute`

Runs one or more write statements: `CREATE TABLE`, `DROP TABLE`, `CREATE INDEX`, `INSERT`, `UPDATE`, `DELETE`.

```bash
curl -X POST http://127.0.0.1:8000/execute \
  -H "Authorization: Bearer $WRITE_KEY" \
  -H "Content-Type: application/sql" \
  --data-binary "INSERT INTO clients (id, name, birth_date, zip) VALUES
                 (6, 'Fay Nguyen', '1958-03-01', '33101'),
                 (7, 'Gus Moreau', '1960-08-19', '33125');"
```

`200 OK`

```json
{"results": [{"message": "Inserted 2 rows", "columns": [], "rows": [], "plan": ""}]}
```

**Atomicity.** Each statement is atomic: all of its rows are saved, or none are. A body with several statements runs them in order and **stops at the first error**. Statements before the error stay saved, because tinydb has no transactions that span statements (see [requirements](requirements.md#7-out-of-scope)). The error response lists what already ran:

`400 Bad Request`

```json
{
  "error": {"type": "DatabaseError", "message": "A row with id = 1 already exists"},
  "results": [{"message": "Created table notes", "columns": [], "rows": [], "plan": ""}]
}
```

A read sent with POST is refused before anything runs, with `WrongMethod` and the message `SELECT only reads, so send it with QUERY /query. Nothing was run.`

A `read` key sent with POST gets `403`, and nothing runs:

```json
{"error": {"type": "Forbidden", "message": "Key 3f9a1c2e is read-only, so it can't use POST /execute."}}
```

### 4.3 `OPTIONS /query` and `OPTIONS /execute`

Tells a client what a path accepts. No body is needed.

```bash
curl -i -X OPTIONS http://127.0.0.1:8000/query
```

```
HTTP/1.1 204 No Content
Allow: QUERY, OPTIONS
Accept-Query: application/sql
```

`OPTIONS /execute` answers `204` with `Allow: POST, OPTIONS`. `Accept-Query` is the header RFC 10008 defines for "this resource takes QUERY, with these body formats".

### 4.4 `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

`200 OK`

```json
{"status": "ok", "version": "0.6.0"}
```

`version` is `tinydb.__version__`. This endpoint needs no API key, so a monitor can check the server without one.

## 5. Security

| Protection | How |
|---|---|
| **API keys** | `secrets.token_urlsafe(32)` keys starting with `tdb_`. Only a SHA-256 hash is stored, in `<dir>/_keys.json` (mode 0600). Checked with `hmac.compare_digest`, comparing every stored hash without stopping early. |
| **Roles** | `read` keys: `QUERY /query` only. `write` keys: `/query` and `/execute`. The key is checked before the body is read. |
| **Revoking** | `python3 main.py apikey revoke <id>`. The server rereads the keys file when it changes, so the key stops working right away. |
| **Injection-safe parsing** | The body goes through the same lexer as the shell. Quoted text is always one value, so input can't change what a statement does. A write hidden after a read in a `QUERY` is refused before anything runs. |
| **Whole bodies only** | A body shorter than `Content-Length` runs nothing, so a dropped connection can't cut a `WHERE` off a `DELETE`. |
| **Limits** | 1 MB body, 100 statements, 10 seconds of silence, 32 connections, 64-character names, 30-character numbers, 64-bit INTs, finite FLOATs. |
| **No leaks** | A `500` never includes a traceback. Errors never repeat the API key. Control characters in a path are escaped in the server log. |
| **Encryption at rest** | Start the server with `TINYDB_PASSPHRASE` set (or type it at the prompt) and every log line in the folder is encrypted. See [architecture.md](architecture.md#8-server-and-security-design-phase-6). |
| **Local by default** | The server listens on `127.0.0.1`. Put HTTPS (a proxy such as nginx) in front before exposing it to a network, because API keys would otherwise travel in plain text. |

## 6. Not included yet

Joins, `OR` and `GROUP BY` (not in the SQL yet), parameterized queries (`?` placeholders, so apps never build SQL from strings), rate limiting failed keys, paging large results, `ETag` caching, and `Content-Location` links that RFC 10008 allows a server to send so a client can re-run a query with `GET`.
