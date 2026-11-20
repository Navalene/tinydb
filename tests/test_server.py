"""Tests for the HTTP API: a real server on a free port, and real HTTP requests to it.

Each test has two keys: self.read_key and self.write_key. Requests use the
write key unless a test says otherwise.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import http.client
import json
import os
import tempfile
import threading
import unittest
from typing import Any, Optional, Tuple

import tinydb
from tinydb.auth import KeyStore
from tinydb.database import Database
from tinydb.server import MAX_BODY, ApiServer

SETUP = """
CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, birth_date DATE, zip TEXT);
INSERT INTO clients VALUES
    (1, 'Ada Reyes', '1956-02-11', '33101'),
    (2, 'Bob Okafor', '1959-07-30', '33125');
"""

SQL = {"Content-Type": "application/sql"}


class ServerTestCase(unittest.TestCase):
    """Starts a server on its own thread before each test, and stops it after."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.db = Database(os.path.join(self.tmpdir.name, "data"))
        self.db.run(SETUP)
        self.keys = KeyStore.in_folder(self.db.directory)
        self.read_key = self.keys.create("dashboard", "read")[1]
        self.write_key = self.keys.create("intake app", "write")[1]
        self.server = ApiServer(self.db, self.keys, port=0, quiet=True)  # port 0: the OS picks a free one
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmpdir.cleanup()

    def request(self, method, path, body=None, headers=None, key: Optional[str] = "write") -> Tuple[int, http.client.HTTPResponse, Any]:
        """Send one request. Returns (status, response, the JSON body, or None if it isn't JSON).

        key is "write" or "read" for one of the test's keys, any other text to send as the key, or None for no key.
        """
        headers = dict(headers or {})
        if key is not None:
            key = {"write": self.write_key, "read": self.read_key}.get(key, key)
            headers["Authorization"] = "Bearer " + key
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            data = body.encode("utf-8") if isinstance(body, str) else body
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            is_json = (response.getheader("Content-Type") or "").startswith("application/json")
            return response.status, response, (json.loads(raw) if is_json else None)
        finally:
            connection.close()

    def query(self, sql, key: Optional[str] = "read"):
        return self.request("QUERY", "/query", sql, SQL, key=key)

    def execute(self, sql, key: Optional[str] = "write"):
        return self.request("POST", "/execute", sql, SQL, key=key)

    def assertError(self, reply, status, kind):
        """Check a reply is a JSON error with this status and type."""
        self.assertEqual(reply[0], status)
        self.assertEqual(reply[2]["error"]["type"], kind)


class QueryTests(ServerTestCase):
    def test_select(self):
        status, response, body = self.query("SELECT id, name FROM clients WHERE id = 2")
        self.assertEqual(status, 200)
        self.assertEqual(body["results"], [{"message": "1 row", "columns": ["id", "name"],
                                            "rows": [[2, "Bob Okafor"]], "plan": "primary key lookup"}])

    def test_json_headers(self):
        _, response, _ = self.query("SHOW TABLES")
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("Accept-Query"), "application/sql, application/json")

    def test_several_reads(self):
        _, _, body = self.query("SHOW TABLES; DESCRIBE clients;")
        self.assertEqual([r["message"] for r in body["results"]], ["1 table", "4 columns"])

    def test_dates_and_nulls_in_json(self):
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'Cy')")
        _, _, body = self.query("SELECT birth_date, zip FROM clients WHERE id >= 2")
        self.assertEqual(body["results"][0]["rows"], [["1959-07-30", "33125"], [None, None]])

    def test_write_refused_and_nothing_runs(self):
        reply = self.query("SELECT * FROM clients; DELETE FROM clients;")
        self.assertError(reply, 400, "WrongMethod")
        self.assertIn("POST /execute", reply[2]["error"]["message"])
        self.assertEqual(len(self.db.table("clients")), 2)

    def test_charset_is_allowed(self):
        status, _, _ = self.request("QUERY", "/query", "SHOW TABLES", {"Content-Type": "application/sql; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_unicode_text(self):
        self.execute("INSERT INTO clients (id, name) VALUES (3, 'José Peña')")
        _, _, body = self.query("SELECT name FROM clients WHERE id = 3")
        self.assertEqual(body["results"][0]["rows"], [["José Peña"]])


class ExecuteTests(ServerTestCase):
    def test_insert(self):
        status, _, body = self.execute("INSERT INTO clients (id, name) VALUES (3, 'Cy'), (4, 'Di')")
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["message"], "Inserted 2 rows")
        self.assertEqual(len(self.db.table("clients")), 4)

    def test_read_refused(self):
        reply = self.execute("SELECT * FROM clients")
        self.assertError(reply, 400, "WrongMethod")
        self.assertIn("QUERY /query", reply[2]["error"]["message"])

    def test_stops_at_first_error_and_reports_what_ran(self):
        reply = self.execute("CREATE TABLE notes (id INT PRIMARY KEY); INSERT INTO clients (id, name) VALUES (1, 'Dup');"
                             " INSERT INTO notes VALUES (1);")
        self.assertError(reply, 400, "DatabaseError")
        self.assertEqual([r["message"] for r in reply[2]["results"]], ["Created table notes"])
        self.assertEqual(len(self.db.table("notes")), 0)  # the third statement never ran

    def test_changes_are_saved(self):
        self.execute("UPDATE clients SET zip = '10001' WHERE id = 1")
        reopened = Database(self.db.directory)
        self.assertEqual(reopened.table("clients").get("1").value["zip"], "10001")


class SqlErrorTests(ServerTestCase):
    def test_sql_error(self):
        self.assertError(self.query("SELECT * FROM clients WHERE"), 400, "SqlError")

    def test_schema_error(self):
        reply = self.execute("INSERT INTO clients (id, name, birth_date) VALUES (3, 'Cy', '03/01/1958')")
        self.assertError(reply, 400, "SchemaError")
        self.assertIn("YYYY-MM-DD", reply[2]["error"]["message"])

    def test_database_error(self):
        self.assertError(self.query("SELECT * FROM nope"), 400, "DatabaseError")

    def test_empty_body(self):
        self.assertError(self.query("  -- just a comment\n"), 400, "EmptyRequest")

    def test_errors_have_no_results_key_when_nothing_ran(self):
        reply = self.execute("INSERT INTO nope VALUES (1)")
        self.assertNotIn("results", reply[2])


class RequestCheckTests(ServerTestCase):
    def test_unknown_path(self):
        self.assertError(self.request("QUERY", "/nope", "SHOW TABLES", SQL), 404, "NotFound")

    def test_wrong_method_for_path(self):
        reply = self.request("GET", "/query")
        self.assertError(reply, 405, "MethodNotAllowed")
        self.assertEqual(reply[1].getheader("Allow"), "QUERY, OPTIONS")

    def test_post_to_query_path(self):
        reply = self.request("POST", "/query", "SHOW TABLES", SQL)
        self.assertError(reply, 405, "MethodNotAllowed")
        self.assertEqual(reply[1].getheader("Connection"), "close")  # the unread body can't be reused

    def test_unknown_method(self):
        status, _, _ = self.request("PUT", "/query", "SHOW TABLES", SQL)
        self.assertEqual(status, 501)  # http.server answers this itself

    def test_query_string_is_ignored(self):
        status, _, _ = self.request("GET", "/health?verbose=1")
        self.assertEqual(status, 200)

    def test_wrong_content_type(self):
        reply = self.request("QUERY", "/query", "SHOW TABLES", {"Content-Type": "text/plain"})
        self.assertError(reply, 415, "UnsupportedMediaType")
        self.assertEqual(reply[1].getheader("Accept-Query"), "application/sql, application/json")

    def test_missing_content_type(self):
        self.assertError(self.request("POST", "/execute", "DROP TABLE clients"), 415, "UnsupportedMediaType")
        self.assertEqual(self.db.table_names(), ["clients"])

    def test_other_charset_refused(self):
        headers = {"Content-Type": "application/sql; charset=latin-1"}
        self.assertError(self.request("QUERY", "/query", "SHOW TABLES", headers), 415, "UnsupportedMediaType")

    def test_missing_content_length(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("QUERY", "/query")
        connection.putheader("Content-Type", "application/sql")
        connection.putheader("Authorization", "Bearer " + self.write_key)
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 411)
        self.assertEqual(json.loads(response.read())["error"]["type"], "LengthRequired")
        connection.close()

    def test_bad_content_length(self):
        headers = {"Content-Type": "application/sql", "Content-Length": "-5"}
        self.assertError(self.request("QUERY", "/query", None, headers), 400, "BadRequest")

    def test_body_too_large_is_refused_before_reading(self):
        headers = {"Content-Type": "application/sql", "Content-Length": str(MAX_BODY + 1)}
        reply = self.request("QUERY", "/query", None, headers)  # sends no body at all
        self.assertError(reply, 413, "TooLarge")
        self.assertEqual(reply[1].getheader("Connection"), "close")

    def test_body_at_the_limit_is_fine(self):
        sql = "SHOW TABLES;" + " " * (MAX_BODY - len("SHOW TABLES;"))
        status, _, _ = self.query(sql)
        self.assertEqual(status, 200)

    def test_body_not_utf8(self):
        self.assertError(self.request("QUERY", "/query", b"SELECT \xff", SQL), 400, "BadRequest")


class OptionsAndHealthTests(ServerTestCase):
    def test_options_query(self):
        status, response, body = self.request("OPTIONS", "/query", key=None)  # no key needed
        self.assertEqual(status, 204)
        self.assertIsNone(body)
        self.assertEqual(response.getheader("Allow"), "QUERY, OPTIONS")
        self.assertEqual(response.getheader("Accept-Query"), "application/sql, application/json")

    def test_options_execute(self):
        status, response, _ = self.request("OPTIONS", "/execute")
        self.assertEqual(status, 204)
        self.assertEqual(response.getheader("Allow"), "POST, OPTIONS")
        self.assertIsNone(response.getheader("Accept-Query"))

    def test_health(self):
        status, _, body = self.request("GET", "/health", key=None)  # no key needed
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok", "version": tinydb.__version__})


class InternalErrorTests(ServerTestCase):
    def test_bug_becomes_a_generic_500(self):
        def broken(statement):
            raise RuntimeError("secret detail from inside tinydb")

        self.db.execute = broken  # pretend the database has a bug
        reply = self.query("SHOW TABLES")
        self.assertError(reply, 500, "InternalError")
        self.assertNotIn("secret", json.dumps(reply[2]))


class AuthTests(ServerTestCase):
    def test_no_key(self):
        reply = self.query("SHOW TABLES", key=None)
        self.assertError(reply, 401, "Unauthorized")
        self.assertTrue(reply[1].getheader("WWW-Authenticate", "").startswith("Bearer"))

    def test_wrong_key(self):
        reply = self.query("SHOW TABLES", key="tdb_not-a-real-key")
        self.assertError(reply, 401, "Unauthorized")
        self.assertIn("invalid_token", reply[1].getheader("WWW-Authenticate", ""))
        self.assertNotIn("not-a-real-key", json.dumps(reply[2]))  # the key is never repeated back

    def test_wrong_scheme(self):
        headers = {"Authorization": "Basic " + self.write_key, **SQL}
        self.assertError(self.request("QUERY", "/query", "SHOW TABLES", headers, key=None), 401, "Unauthorized")

    def test_scheme_is_not_case_sensitive(self):
        headers = {"Authorization": "bearer " + self.read_key, **SQL}
        self.assertEqual(self.request("QUERY", "/query", "SHOW TABLES", headers, key=None)[0], 200)

    def test_read_key_can_query(self):
        self.assertEqual(self.query("SHOW TABLES", key="read")[0], 200)

    def test_write_key_can_query_too(self):
        self.assertEqual(self.query("SHOW TABLES", key="write")[0], 200)

    def test_read_key_cannot_execute(self):
        reply = self.execute("DELETE FROM clients", key="read")
        self.assertError(reply, 403, "Forbidden")
        self.assertEqual(len(self.db.table("clients")), 2)

    def test_no_key_on_execute_changes_nothing(self):
        self.assertError(self.execute("DROP TABLE clients", key=None), 401, "Unauthorized")
        self.assertEqual(self.db.table_names(), ["clients"])

    def test_key_is_checked_before_the_body(self):
        headers = {"Content-Type": "application/sql", "Content-Length": str(MAX_BODY + 1)}
        self.assertError(self.request("QUERY", "/query", None, headers, key=None), 401, "Unauthorized")

    def test_revoked_key_stops_working_right_away(self):
        other = KeyStore.in_folder(self.db.directory)  # like running revoke in another Terminal
        read_id = [k.id for k in other.keys() if k.role == "read"][0]
        self.assertTrue(other.revoke(read_id))
        self.assertError(self.query("SHOW TABLES", key="read"), 401, "Unauthorized")
        self.assertEqual(self.query("SHOW TABLES", key="write")[0], 200)

    def test_key_made_while_running_works(self):
        new_key = KeyStore.in_folder(self.db.directory).create("report", "read")[1]
        self.assertEqual(self.query("SHOW TABLES", key=new_key)[0], 200)


class ConnectionTests(ServerTestCase):
    def test_one_connection_many_requests(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer " + self.read_key, **SQL}
        for _ in range(3):
            connection.request("QUERY", "/query", body=b"SHOW TABLES", headers=headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        connection.close()

    def test_many_clients_at_once(self):
        errors = []

        def insert(first_id):
            for i in range(first_id, first_id + 10):
                status, _, body = self.execute(f"INSERT INTO clients (id, name) VALUES ({i}, 'Client {i}')")
                if status != 200:
                    errors.append(body)

        threads = [threading.Thread(target=insert, args=(n * 100,)) for n in range(1, 5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.db.table("clients")), 2 + 40)


if __name__ == "__main__":
    unittest.main()
