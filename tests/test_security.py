"""Security tests: SQL injection attempts, input limits, and attacks on the HTTP server.

Each test is an attack. It passes when the attack fails.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import contextlib
import io
import os
import socket
import tempfile
import threading
import time
import unittest

from tests.test_server import ServerTestCase
from tinydb.auth import KeyStore
from tinydb.database import Database
from tinydb.lexer import SqlError
from tinydb.schema import INT_MAX, MAX_NAME, Column, Schema, SchemaError
from tinydb.server import ApiServer


class TempDatabaseTestCase(unittest.TestCase):
    """A Database in a temporary folder that's deleted after each test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        self.db = Database(os.path.join(self.tmpdir, "data"))


class InjectionTests(ServerTestCase):
    """Text inside quotes is always data. It can never become SQL."""

    def names(self):
        _, _, body = self.query("SELECT name FROM clients")
        return [row[0] for row in body["results"][0]["rows"]]

    def test_classic_or_1_equals_1_is_just_text(self):
        # An app that escapes quotes correctly sends: 'x'' OR ''1''=''1'
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'x'' OR ''1''=''1')")
        _, _, body = self.query("SELECT id FROM clients WHERE name = 'x'' OR ''1''=''1'")
        self.assertEqual(body["results"][0]["rows"], [[3]])  # one row, not every row
        self.assertIn("x' OR '1'='1", self.names())

    def test_drop_table_inside_a_value_is_just_text(self):
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'Bobby''); DROP TABLE clients; --')")
        self.assertEqual(self.db.table_names(), ["clients"])
        self.assertIn("Bobby'); DROP TABLE clients; --", self.names())

    def test_unescaped_quote_breaks_the_sql_instead_of_changing_it(self):
        # An app that forgot to escape sends this. tinydb has no OR, so it can't be widened.
        reply = self.query("SELECT * FROM clients WHERE name = 'x' OR '1'='1'")
        self.assertError(reply, 400, "SqlError")

    def test_stacked_write_in_a_query_runs_nothing(self):
        reply = self.query("SELECT * FROM clients WHERE id = 1; DROP TABLE clients")
        self.assertError(reply, 400, "WrongMethod")
        self.assertEqual(self.db.table_names(), ["clients"])

    def test_comment_inside_a_value_is_just_text(self):
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'Cy -- not a comment')")
        self.assertIn("Cy -- not a comment", self.names())

    def test_unicode_lookalike_keyword_is_refused(self):
        self.assertError(self.query("ＳＥＬＥＣＴ * FROM clients"), 400, "SqlError")  # full-width letters

    def test_nul_and_control_characters_are_just_text(self):
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'a\x00b\x1b[2J')")
        self.assertIn("a\x00b\x1b[2J", self.names())

    def test_cannot_reach_the_keys_file(self):
        # _keys.json lives in the same folder as the tables. Names starting with _ are tinydb's own.
        self.assertError(self.execute("CREATE TABLE _keys (id INT PRIMARY KEY)"), 400, "DatabaseError")
        self.assertError(self.query("SELECT * FROM _keys"), 400, "DatabaseError")


class NameTests(TempDatabaseTestCase):
    """Names become file names, so they're checked before they touch the disk."""
    def test_path_traversal_in_sql_is_a_syntax_error(self):
        with self.assertRaises(SqlError):
            self.db.run("CREATE TABLE ../../evil (id INT PRIMARY KEY)")

    def test_path_traversal_in_code_is_refused(self):
        with self.assertRaises(SchemaError):
            self.db.create_table("../evil", Schema((Column("id", "INT", primary_key=True),)))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "evil.jsonl")))

    def test_long_names_are_refused(self):
        with self.assertRaises(SchemaError):
            self.db.run(f"CREATE TABLE {'a' * (MAX_NAME + 1)} (id INT PRIMARY KEY)")
        self.db.run(f"CREATE TABLE {'a' * MAX_NAME} (id INT PRIMARY KEY)")  # exactly at the limit is fine

    def test_long_column_names_are_refused(self):
        with self.assertRaises(SchemaError):
            self.db.run(f"CREATE TABLE t ({'c' * (MAX_NAME + 1)} INT PRIMARY KEY)")


class NumberTests(TempDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.db.run("CREATE TABLE t (id INT PRIMARY KEY, f FLOAT)")

    def test_huge_number_is_refused_quickly(self):
        start = time.time()
        with self.assertRaises(SqlError):
            self.db.run("INSERT INTO t VALUES (" + "9" * 500000 + ", 1.0)")
        self.assertLess(time.time() - start, 1)

    def test_int_must_fit_in_64_bits(self):
        self.db.run(f"INSERT INTO t VALUES ({INT_MAX}, 1.0)")
        with self.assertRaises(SchemaError):
            self.db.run(f"INSERT INTO t VALUES ({INT_MAX + 1}, 1.0)")

    def test_long_float_is_refused(self):
        with self.assertRaises(SqlError):
            self.db.run("INSERT INTO t VALUES (1, " + "9" * 400 + ".0)")  # would be inf as a float

    def test_infinity_never_reaches_a_float_column(self):
        # SQL can't make inf any more, but code that calls Schema directly still can't store it.
        with self.assertRaises(SchemaError):
            self.db.schema("t").column("f").convert(float("inf"))


class RequestLimitTests(ServerTestCase):
    def test_too_many_statements(self):
        reply = self.query("SHOW TABLES;" * 101)
        self.assertError(reply, 400, "BadRequest")
        self.assertEqual(self.query("SHOW TABLES;" * 100)[0], 200)

    def test_two_content_lengths_are_refused(self):
        reply = self.raw(b"QUERY /query HTTP/1.1\r\nContent-Length: 11\r\nContent-Length: 0\r\n" + self.auth() + b"\r\nSHOW TABLES")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_content_length_and_transfer_encoding_are_refused(self):
        reply = self.raw(b"QUERY /query HTTP/1.1\r\nContent-Length: 11\r\nTransfer-Encoding: chunked\r\n" + self.auth()
                         + b"\r\nSHOW TABLES")
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])

    def test_cut_off_body_runs_nothing(self):
        # The client says the body is 32 bytes, sends only "DELETE FROM clients", and hangs up.
        body = b"DELETE FROM clients WHERE id = 2"
        request = (b"POST /execute HTTP/1.1\r\nContent-Length: " + str(len(body)).encode() + b"\r\n" + self.auth()
                   + b"\r\n" + body[:19])  # "DELETE FROM clients"
        reply = self.raw(request, hang_up=True)
        self.assertIn(b" 400 ", reply.split(b"\r\n")[0])
        self.assertEqual(len(self.db.table("clients")), 2)  # nothing was deleted

    def auth(self):
        return b"Authorization: Bearer " + self.write_key.encode() + b"\r\nContent-Type: application/sql\r\n"

    def raw(self, data, hang_up=False):
        """Send raw bytes, for requests http.client refuses to make. Returns everything the server sent back."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(data)
            if hang_up:
                sock.shutdown(socket.SHUT_WR)  # "I'm done sending"
            chunks = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\r\n\r\n" in b"".join(chunks) and not hang_up:
                    break
            return b"".join(chunks)


class HeaderTests(ServerTestCase):
    def test_security_headers(self):
        _, response, _ = self.query("SHOW TABLES")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy", ""))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_security_headers_on_errors_too(self):
        _, response, _ = self.query("SHOW TABLES", key=None)
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_server_header_hides_the_python_version(self):
        _, response, _ = self.request("GET", "/health", key=None)
        self.assertNotIn("Python", response.getheader("Server", ""))


class SlowClientTests(TempDatabaseTestCase):
    """A server with a short timeout and only 2 connection slots, so the tests run fast."""

    def setUp(self):
        super().setUp()
        self.keys = KeyStore.in_folder(self.db.directory)
        self.key = self.keys.create("test", "write")[1]
        self.server = ApiServer(self.db, self.keys, port=0, quiet=True, max_connections=2, request_timeout=0.3)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def test_silent_client_is_disconnected(self):
        sock = self.connect()
        sock.sendall(b"QUERY /query HTTP/1.1\r\n")  # then nothing more, like a "slowloris" attack
        start = time.time()
        self.assertEqual(sock.recv(1000), b"")  # b"" means the server closed the connection
        self.assertLess(time.time() - start, 3)

    def test_slow_body_gets_408(self):
        sock = self.connect()
        sock.sendall(b"QUERY /query HTTP/1.1\r\nContent-Type: application/sql\r\nContent-Length: 50\r\n"
                     b"Authorization: Bearer " + self.key.encode() + b"\r\n\r\nSHOW")  # 4 of 50 bytes, then nothing
        self.assertIn(b" 408 ", sock.recv(1000))

    def test_too_many_connections_get_503(self):
        first, second = self.connect(), self.connect()
        time.sleep(0.05)  # let the server accept both
        third = self.connect()
        self.assertIn(b"503", third.recv(1000))
        first.close()
        second.close()

    def test_slots_are_freed_when_connections_close(self):
        for _ in range(5):
            sock = self.connect()
            sock.sendall(b"GET /health HTTP/1.1\r\nConnection: close\r\n\r\n")
            self.assertIn(b"200", sock.recv(1000))
            sock.close()
            time.sleep(0.02)  # give the server a moment to free the slot


class LogTests(ServerTestCase):
    def test_control_characters_in_the_path_are_escaped_in_the_log(self):
        self.server.quiet = False
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                sock.sendall(b"GET /\x1b[2Jhello HTTP/1.1\r\nConnection: close\r\n\r\n")
                sock.recv(1000)
            time.sleep(0.05)
        self.server.quiet = True
        self.assertNotIn("\x1b", log.getvalue())
        self.assertIn("\\x1b[2Jhello", log.getvalue())


if __name__ == "__main__":
    unittest.main()
