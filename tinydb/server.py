"""HTTP API for tinydb, built by hand on Python's http.server.

    QUERY   /query     read SQL (SELECT, SHOW TABLES, DESCRIBE)  -> JSON
    POST    /execute   write SQL (CREATE, INSERT, UPDATE, ...)   -> JSON
    OPTIONS /query     which methods and body formats a path takes
    GET     /health    is the server up, and which version

Every request except GET /health and OPTIONS needs an API key:

    Authorization: Bearer tdb_...

A read key may only use QUERY /query. A write key may also use POST /execute.
Make a key with:  python3 main.py apikey create NAME --role read

The full contract is docs/api-spec.md. Run the server with:

    python3 main.py serve                  (the tinydb_data folder)
    python3 main.py --dir demo_data serve  (another folder)

To keep the data encrypted on disk, set a passphrase first (Phase 6, Lesson 5):

    export TINYDB_PASSPHRASE='a long passphrase you will remember'

For an encrypted folder without TINYDB_PASSPHRASE, the server asks for it.

http.server reads the request line ("QUERY /query HTTP/1.1") and the headers,
then calls the method named do_ plus the HTTP method: do_GET, do_POST, and
do_QUERY for QUERY. That's all it does. Routing, reading the body, checking
it, running the SQL and writing JSON back are this file's job.

Parameterized queries (Phase 7): send JSON instead of plain SQL, with a ?
for each value and the values in a list. They never become part of the SQL text:

    Content-Type: application/json
    {"sql": "SELECT * FROM clients WHERE name = ?", "params": ["O'Brien"]}

Limits that keep one client from tying up the server (Lesson 3):
    MAX_BODY         1 MB per request
    MAX_STATEMENTS   100 SQL statements per request
    REQUEST_TIMEOUT  10 seconds of silence and the connection is closed
    MAX_CONNECTIONS  32 connections at once; more get 503
"""

import argparse
import json
import socket
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import urlsplit

import tinydb
from tinydb.auth import KeyStore
from tinydb.database import Database, DatabaseError, passphrase_for
from tinydb.lexer import MAX_NUMBER_LENGTH, SqlError
from tinydb.schema import SchemaError
from tinydb.sql import Describe, Select, ShowTables, parse

MAX_BODY = 1024 * 1024  # 1 MB, the limit in the spec
MAX_STATEMENTS = 100
REQUEST_TIMEOUT = 10.0  # seconds
MAX_CONNECTIONS = 32
SQL_TYPE = "application/sql"
JSON_TYPE = "application/json"  # {"sql": "...", "params": [...]}
BODY_TYPES = (SQL_TYPE, JSON_TYPE)
ACCEPT_QUERY = ", ".join(BODY_TYPES)  # the Accept-Query header: what QUERY /query takes
READS = (Select, ShowTables, Describe)  # the only statements QUERY may run

# Sent with every JSON response. JSON is data, so a browser must never run it or show it in a frame.
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

# path -> the methods it takes (OPTIONS works on every path, so it isn't listed)
ROUTES = {
    "/query": ("QUERY",),
    "/execute": ("POST",),
    "/health": ("GET",),
}


class ApiError(Exception):
    """A request the server refuses. Becomes a JSON error response."""

    def __init__(self, status, kind, message, headers=None, results=None):
        super().__init__(message)
        self.status = status
        self.kind = kind  # the "type" in the JSON, like "WrongMethod"
        self.message = message
        self.headers = headers or {}
        self.results = results or []  # statements that ran before the error


def number_text(text):
    """json.loads calls this for every whole number. Refuse absurdly long ones before int() sees them.

    On Python 3.9, int() of a million-digit string takes seconds: a cheap way to stall a server.
    """
    if len(text) > MAX_NUMBER_LENGTH:
        raise ValueError(f"A number in the JSON is too long (over {MAX_NUMBER_LENGTH} characters)")
    return int(text)


def no_constants(name):
    """json.loads calls this for NaN, Infinity and -Infinity, which real JSON doesn't have."""
    raise ValueError(f"{name} isn't valid JSON")


def sql_and_params(media_type, text):
    """Split a request body into (sql, params). Plain SQL has no params."""
    if media_type == SQL_TYPE:
        return text, ()
    try:
        body = json.loads(text, parse_int=number_text, parse_constant=no_constants)
    except ValueError as e:  # bad JSON, a huge number, or NaN
        raise ApiError(400, "BadRequest", f"The body isn't valid JSON: {e}") from None
    if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
        raise ApiError(400, "BadRequest", 'Send a JSON object like {"sql": "SELECT * FROM t WHERE id = ?", "params": [4]}.')
    unknown = sorted(set(body) - {"sql", "params"})
    if unknown:
        raise ApiError(400, "BadRequest", f"Unknown field {unknown[0]!r}. The JSON takes sql and params.")
    params = body.get("params", [])
    if not isinstance(params, list):
        raise ApiError(400, "BadRequest", "params must be a list, like [4] or [\"O'Brien\", 65].")
    return body["sql"], params


def statement_word(statement):
    """The SQL word a statement starts with, for messages: Select -> SELECT."""
    names = {"CreateTable": "CREATE TABLE", "DropTable": "DROP TABLE", "CreateIndex": "CREATE INDEX",
             "ShowTables": "SHOW TABLES"}
    name = type(statement).__name__
    return names.get(name, name.upper())


def result_to_json(result):
    """Turn a Result into the JSON object the spec describes."""
    return {
        "message": result.message,
        "columns": list(result.columns),
        "rows": [list(row) for row in result.rows],
        "plan": result.plan,
    }


def error_kind(error):
    """The JSON error type for an error raised by the parser or the database."""
    if isinstance(error, SqlError):
        return "SqlError"
    if isinstance(error, SchemaError):
        return "SchemaError"
    return "DatabaseError"


class ApiHandler(BaseHTTPRequestHandler):
    """Handles one HTTP request. http.server makes a new one for every request."""

    server_version = "tinydb/" + tinydb.__version__
    sys_version = ""  # by default http.server also sends the Python version, which only helps attackers
    protocol_version = "HTTP/1.1"  # keep connections open between requests
    # Send each reply's headers and body right away (TCP_NODELAY). Without it, the Nagle
    # algorithm holds the body back until the client acknowledges the headers, and the
    # client waits up to 40 ms to do that. Phase 7's benchmark found this: 43 ms per QUERY.
    disable_nagle_algorithm = True

    def setup(self):
        """Runs before each connection is read. A client that goes quiet for request_timeout is disconnected."""
        super().setup()
        self.connection.settimeout(self.api.request_timeout)  # every read from the socket now gives up after this

    @property
    def api(self):
        """The ApiServer that received this request. It holds the database."""
        return cast(ApiServer, self.server)

    # ---- one method per HTTP method ----

    def do_QUERY(self):
        self.handle_request("QUERY")

    def do_POST(self):
        self.handle_request("POST")

    def do_GET(self):
        self.handle_request("GET")

    def do_OPTIONS(self):
        self.handle_request("OPTIONS")

    # ---- routing ----

    def handle_request(self, method):
        """Route the request, and turn any error into a JSON response."""
        self.body_read = False
        self.key = None  # the ApiKey that sent this request, once authenticate has checked it
        try:
            path = urlsplit(self.path).path
            if path not in ROUTES:
                raise ApiError(404, "NotFound", f"No such path: {path}. Use /query, /execute or /health.")
            allowed = ROUTES[path] + ("OPTIONS",)
            if method == "OPTIONS":
                self.send_options(path, allowed)
            elif method not in allowed:
                raise ApiError(405, "MethodNotAllowed", f"{path} takes {' or '.join(ROUTES[path])}, not {method}.",
                               headers={"Allow": ", ".join(allowed)})
            elif path == "/health":
                self.send_json(200, {"status": "ok", "version": tinydb.__version__})
            else:
                self.authenticate(path)
                if path == "/query":
                    self.send_json(200, {"results": self.run_sql(reads=True)}, {"Accept-Query": ACCEPT_QUERY})
                else:
                    self.send_json(200, {"results": self.run_sql(reads=False)})
        except ApiError as e:
            self.send_error_json(e)
        except Exception:
            # A bug. Say so without leaking a traceback to the client, but log it here.
            self.log_error("Internal error handling %s %s", method, self.path)
            if not self.api.quiet:
                traceback.print_exc()
            self.send_error_json(ApiError(500, "InternalError", "Something went wrong inside tinydb."))

    def send_options(self, path, allowed):
        headers = {"Allow": ", ".join(allowed)}
        if path == "/query":
            headers["Accept-Query"] = ACCEPT_QUERY
        self.send_response(204)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- API keys ----

    def authenticate(self, path):
        """Check the Authorization header (who are you?), then the key's role (may you do this?)."""
        header = self.headers.get("Authorization", "")
        scheme, _, key = header.partition(" ")
        if scheme.lower() != "bearer" or not key.strip():
            raise ApiError(401, "Unauthorized", "Send an API key: Authorization: Bearer <key>.",
                           headers={"WWW-Authenticate": 'Bearer realm="tinydb"'})
        api_key = self.api.keys.check(key.strip())
        if api_key is None:
            # Same answer for a wrong key and a revoked one, and the key is never repeated back.
            raise ApiError(401, "Unauthorized", "That API key isn't valid.",
                           headers={"WWW-Authenticate": 'Bearer realm="tinydb", error="invalid_token"'})
        self.key = api_key
        if path == "/execute" and not api_key.can_write():
            raise ApiError(403, "Forbidden", f"Key {api_key.id} is read-only, so it can't use POST /execute.")

    # ---- reading and checking the body ----

    def read_body(self):
        """Check the headers, then read the body. Returns (media type, body as text)."""
        content_type = self.headers.get("Content-Type", "")
        media_type, _, params = content_type.partition(";")
        media_type = media_type.strip().lower()
        charset = params.strip().lower()
        if media_type not in BODY_TYPES or charset not in ("", "charset=utf-8"):
            headers = {"Accept-Query": ACCEPT_QUERY} if self.command == "QUERY" else {}
            raise ApiError(415, "UnsupportedMediaType",
                           f"Send SQL as Content-Type: {SQL_TYPE}, or SQL with ? values as {JSON_TYPE}, "
                           f"not {content_type or 'nothing'}.", headers)

        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) > 1 or ("Transfer-Encoding" in self.headers and lengths):
            # Two ways to say where the body ends. If a proxy in front picks one and the server
            # the other, an attacker can hide a second request inside the body ("request smuggling").
            raise ApiError(400, "BadRequest", "Send exactly one Content-Length header, and no Transfer-Encoding.")
        length_text = lengths[0] if lengths else None
        if length_text is None:
            raise ApiError(411, "LengthRequired", "Send a Content-Length header with the size of the body.")
        if not (length_text.isascii() and length_text.isdigit()):
            raise ApiError(400, "BadRequest", f"Content-Length must be a whole number, not {length_text!r}.")
        length = int(length_text)
        if length > MAX_BODY:  # refused before reading a single byte of it
            raise ApiError(413, "TooLarge", f"The body is {length} bytes. The limit is {MAX_BODY}.")

        try:
            raw = self.rfile.read(length)
        except socket.timeout:
            raise ApiError(408, "Timeout", f"The body didn't arrive within {self.api.request_timeout} seconds.") from None
        if len(raw) < length:
            # The connection was cut off. Running part of the SQL could turn
            # "DELETE FROM clients WHERE id = 5" into "DELETE FROM clients".
            raise ApiError(400, "BadRequest", f"The body ended after {len(raw)} of {length} bytes. Nothing was run.")
        self.body_read = True
        try:
            return media_type, raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiError(400, "BadRequest", "The body must be UTF-8 text.") from None

    # ---- running SQL ----

    def run_sql(self, reads):
        """Parse the whole body, check every statement fits the method, then run them in order.

        reads=True is QUERY: only reads allowed. reads=False is POST: only writes allowed.
        """
        sql, params = sql_and_params(*self.read_body())
        try:
            statements = parse(sql, params)  # every ? is filled here, as a value, never as SQL text
        except (ValueError, TypeError) as e:
            raise ApiError(400, error_kind(e), str(e)) from None
        if not statements:
            raise ApiError(400, "EmptyRequest", "The body has no SQL statement in it.")
        if len(statements) > MAX_STATEMENTS:
            raise ApiError(400, "BadRequest", f"The body has {len(statements)} statements. The limit is {MAX_STATEMENTS}.")

        # Check everything before running anything, so a refused request changes nothing.
        for statement in statements:
            word = statement_word(statement)
            if reads and not isinstance(statement, READS):
                raise ApiError(400, "WrongMethod", f"{word} changes data, so send it with POST /execute. Nothing was run.")
            if not reads and isinstance(statement, READS):
                raise ApiError(400, "WrongMethod", f"{word} only reads, so send it with QUERY /query. Nothing was run.")

        results = []
        with self.api.lock:  # one request at a time touches the database
            for statement in statements:
                try:
                    results.append(result_to_json(self.api.database.execute(statement)))
                except (ValueError, TypeError) as e:
                    raise ApiError(400, error_kind(e), str(e), results=results) from None
        return results

    # ---- writing responses ----

    def send_json(self, status, body, headers=None):
        # allow_nan=False: NaN and Infinity aren't valid JSON, so fail loudly instead of sending them.
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, error):
        if not self.body_read:
            # The body (if any) is still waiting on the connection. Reading the next request
            # from there would treat the body as a request line, so close the connection.
            self.close_connection = True
        body = {"error": {"type": error.kind, "message": error.message}}
        if error.results:
            body["results"] = error.results
        self.send_json(error.status, body, error.headers)

    def log_message(self, format, *args):
        """http.server calls this to log each request. quiet=True turns it off (for the tests).

        The log line says which key sent the request (its id, never the key), so you can see who did what.
        """
        if not self.api.quiet:
            key = getattr(self, "key", None)
            who = f" key={key.id}" if key else ""
            line = (format % args) + who
            # A path can hold control characters, like the ones that recolor or clear a Terminal.
            # Show them as \x1b and so on, so a request can't rewrite the log you're reading.
            line = "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in line)
            super().log_message("%s", line)


BUSY_BODY = b'{"error": {"type": "Busy", "message": "Too many connections. Try again soon."}}'
BUSY_RESPONSE = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json; charset=utf-8\r\n"
                 b"Content-Length: " + str(len(BUSY_BODY)).encode() + b"\r\nRetry-After: 1\r\nConnection: close\r\n\r\n"
                 + BUSY_BODY)


class ApiServer(ThreadingHTTPServer):
    """Listens for connections and answers each request on its own thread with an ApiHandler.

    port=0 lets the operating system pick a free port (the tests use that).
    Call serve_forever() to start answering requests.
    """

    daemon_threads = True  # don't wait for open connections when the server stops

    def __init__(self, database, keys, host="127.0.0.1", port=8000, quiet=False,
                 max_connections=MAX_CONNECTIONS, request_timeout=REQUEST_TIMEOUT):
        super().__init__((host, port), ApiHandler)
        self.database = database
        self.keys = keys  # a KeyStore
        self.slots = threading.BoundedSemaphore(max_connections)  # one slot per open connection
        self.request_timeout = request_timeout
        self.lock = threading.Lock()  # requests run on many threads, but the database isn't thread-safe
        self.quiet = quiet

    def process_request(self, request, client_address):
        """Called for each new connection. Refuse it with 503 if MAX_CONNECTIONS are already open."""
        if not self.slots.acquire(blocking=False):
            try:
                cast(socket.socket, request).sendall(BUSY_RESPONSE)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        """Runs on the connection's own thread. Frees its slot when the connection closes."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        """Called when a connection breaks, for example a client hangs up mid-request."""
        if not self.quiet:
            super().handle_error(request, client_address)


def main(argv=None):
    arg_parser = argparse.ArgumentParser(prog="tinydb serve", description="Run the tinydb HTTP API.")
    arg_parser.add_argument("--dir", default="tinydb_data", help="folder for SQL tables (default: tinydb_data)")
    arg_parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1, this computer only)")
    arg_parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    options = arg_parser.parse_args(argv)

    try:
        # From the environment or a hidden prompt, so it's never on a command line or saved in a file.
        database = Database(options.dir, passphrase=passphrase_for(options.dir))
    except (DatabaseError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    keys = KeyStore.in_folder(options.dir)
    server = ApiServer(database, keys, options.host, options.port)
    host, port = server.server_address[:2]
    state = "encrypted" if database.encrypted else "NOT encrypted"
    print(f"tinydb {tinydb.__version__} serving {options.dir} ({state}) on http://{host}:{port}  (Ctrl-C to stop)")
    if not len(keys):
        print(f"No API keys yet, so every request will get 401. Make one with:\n"
              f"    python3 main.py --dir {options.dir} apikey create me --role write")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
