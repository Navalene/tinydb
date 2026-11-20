"""Unit tests for ? placeholders (parameterized queries): the parser, the Database and the HTTP API.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.database import Database
from tinydb.lexer import SqlError
from tinydb.schema import SchemaError
from tinydb.sql import Condition, Insert, Update, parse
from tests.test_server import ServerTestCase

JSON = {"Content-Type": "application/json"}

# Values an attacker might type into a search box, hoping they become SQL.
ATTACKS = [
    "x' OR '1'='1",
    "'; DROP TABLE clients; --",
    "Ada' --",
    "?",
]


class ParserTest(unittest.TestCase):
    def test_where_values(self):
        statement = parse("SELECT * FROM clients WHERE name = ? AND age > ?", ["O'Brien", 65])[0]
        self.assertEqual(statement.where, (Condition("name", "=", "O'Brien"), Condition("age", ">", 65)))

    def test_insert_and_update_values(self):
        insert = parse("INSERT INTO clients VALUES (?, ?), (?, ?)", [1, "Ada", 2, None])[0]
        self.assertIsInstance(insert, Insert)
        self.assertEqual(insert.rows, ((1, "Ada"), (2, None)))
        update = parse("UPDATE clients SET zip = ?, medicaid = ? WHERE id = ?", ["33101", True, 4])[0]
        self.assertIsInstance(update, Update)
        self.assertEqual(update.assignments, (("zip", "33101"), ("medicaid", True)))

    def test_values_are_used_in_order_across_statements(self):
        first, second = parse("SELECT * FROM a WHERE id = ?; SELECT * FROM b WHERE id = ?", [1, 2])
        self.assertEqual(first.where[0].value, 1)
        self.assertEqual(second.where[0].value, 2)

    def test_placeholders_and_literals_mix(self):
        statement = parse("SELECT * FROM t WHERE a = 1 AND b = ? AND c = 'x'", [2])[0]
        self.assertEqual([c.value for c in statement.where], [1, 2, "x"])

    def test_question_mark_inside_quotes_is_just_text(self):
        statement = parse("SELECT * FROM t WHERE note = 'why?'")[0]
        self.assertEqual(statement.where[0].value, "why?")

    def test_too_few_values(self):
        with self.assertRaisesRegex(SqlError, "Not enough values"):
            parse("SELECT * FROM t WHERE a = ? AND b = ?", [1])

    def test_too_many_values(self):
        with self.assertRaisesRegex(SqlError, "Too many values"):
            parse("SELECT * FROM t WHERE a = ?", [1, 2])

    def test_values_without_placeholders(self):
        with self.assertRaisesRegex(SqlError, "Too many values"):
            parse("SELECT * FROM t", [1])

    def test_placeholder_without_values(self):
        with self.assertRaisesRegex(SqlError, "Not enough values"):
            parse("SELECT * FROM t WHERE a = ?")

    def test_params_must_be_a_list(self):
        for bad in ("Ada", b"Ada", {"a": 1}):
            with self.assertRaisesRegex(SqlError, "must be a list"):
                parse("SELECT * FROM t WHERE a = ?", bad)  # type: ignore

    def test_only_plain_values(self):
        for bad in ([1, 2], {"a": 1}, object()):
            with self.assertRaisesRegex(SqlError, "Value 1"):
                parse("SELECT * FROM t WHERE a = ?", [bad])

    def test_names_cannot_be_placeholders(self):
        with self.assertRaisesRegex(SqlError, "Expected a table name"):
            parse("SELECT * FROM ? WHERE id = 1", ["clients"])
        with self.assertRaisesRegex(SqlError, "Expected a column name"):
            parse("SELECT * FROM t WHERE ? = 1", ["id"])

    def test_null_value_in_where_is_refused(self):
        with self.assertRaisesRegex(SqlError, "NULL can't be compared"):
            parse("SELECT * FROM t WHERE a = ?", [None])


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.db = Database(os.path.join(self.tmpdir.name, "data"))
        self.db.run("CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, born DATE);")
        self.db.run("INSERT INTO clients VALUES (?, ?, ?), (?, ?, ?)",
                    [1, "Eve O'Brien", "1950-05-09", 2, "Ada", "1956-03-14"])

    def tearDown(self):
        self.tmpdir.cleanup()

    def names(self, sql, params):
        return [row[0] for row in self.db.run(sql, params)[0].rows]

    def test_quotes_need_no_escaping(self):
        self.assertEqual(self.names("SELECT name FROM clients WHERE name = ?", ["Eve O'Brien"]), ["Eve O'Brien"])

    def test_attacks_are_just_values(self):
        for attack in ATTACKS:
            self.assertEqual(self.names("SELECT name FROM clients WHERE name = ?", [attack]), [])
        self.assertEqual(self.db.table_names(), ["clients"])
        self.assertEqual(len(self.db.table("clients")), 2)

    def test_attack_saved_as_text(self):
        self.db.run("INSERT INTO clients (id, name) VALUES (?, ?)", [3, ATTACKS[1]])
        self.assertEqual(self.names("SELECT name FROM clients WHERE id = ?", [3]), [ATTACKS[1]])
        self.assertEqual(self.db.table_names(), ["clients"])

    def test_values_are_still_type_checked(self):
        with self.assertRaises(SchemaError):
            self.db.run("SELECT name FROM clients WHERE id = ?", ["1 OR 1=1"])
        with self.assertRaises(SchemaError):
            self.db.run("INSERT INTO clients VALUES (?, ?, ?)", [3, "Cy", "03/01/1958"])

    def test_dates_and_ranges(self):
        self.assertEqual(self.names("SELECT name FROM clients WHERE born >= ? AND born < ?",
                                    ["1955-01-01", "1960-01-01"]), ["Ada"])


class ApiTest(ServerTestCase):
    def json_query(self, body, key="read"):
        return self.request("QUERY", "/query", json.dumps(body), JSON, key=key)

    def test_query_with_params(self):
        status, _, body = self.json_query({"sql": "SELECT name FROM clients WHERE id = ?", "params": [2]})
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["rows"], [["Bob Okafor"]])

    def test_params_are_optional(self):
        status, _, body = self.json_query({"sql": "SHOW TABLES"})
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["rows"], [["clients"]])

    def test_charset_is_allowed(self):
        body = json.dumps({"sql": "SHOW TABLES"})
        status, _, _ = self.request("QUERY", "/query", body, {"Content-Type": "application/json; charset=utf-8"}, key="read")
        self.assertEqual(status, 200)

    def test_attacks_find_nothing_and_change_nothing(self):
        for attack in ATTACKS:
            status, _, body = self.json_query({"sql": "SELECT id FROM clients WHERE name = ?", "params": [attack]})
            self.assertEqual(status, 200)
            self.assertEqual(body["results"][0]["rows"], [])
        self.assertEqual(self.db.table_names(), ["clients"])

    def test_execute_with_params(self):
        body = json.dumps({"sql": "INSERT INTO clients (id, name, zip) VALUES (?, ?, ?)",
                           "params": [3, "Eve O'Brien", "33139"]})
        status, _, reply = self.request("POST", "/execute", body, JSON)
        self.assertEqual(status, 200)
        self.assertEqual(reply["results"][0]["message"], "Inserted 1 row")
        self.assertEqual(self.db.run("SELECT name FROM clients WHERE id = 3")[0].rows, (("Eve O'Brien",),))

    def test_writes_still_need_post(self):
        reply = self.json_query({"sql": "DELETE FROM clients WHERE id = ?", "params": [1]})
        self.assertError(reply, 400, "WrongMethod")
        self.assertEqual(len(self.db.table("clients")), 2)

    def test_wrong_number_of_values(self):
        self.assertError(self.json_query({"sql": "SELECT * FROM clients WHERE id = ?", "params": []}), 400, "SqlError")
        self.assertError(self.json_query({"sql": "SELECT * FROM clients", "params": [1]}), 400, "SqlError")

    def test_bad_bodies(self):
        bad_bodies = [
            "not json",
            json.dumps(["SELECT 1"]),
            json.dumps({"params": [1]}),
            json.dumps({"sql": 5}),
            json.dumps({"sql": "SHOW TABLES", "parms": [1]}),
            json.dumps({"sql": "SELECT * FROM clients WHERE id = ?", "params": 1}),
            '{"sql": "SELECT * FROM clients WHERE id = ?", "params": [NaN]}',
            '{"sql": "SELECT * FROM clients WHERE id = ?", "params": [' + "9" * 5000 + "]}",
        ]
        for body in bad_bodies:
            with self.subTest(body=body[:40]):
                self.assertError(self.request("QUERY", "/query", body, JSON, key="read"), 400, "BadRequest")

    def test_nested_values_are_refused(self):
        reply = self.json_query({"sql": "SELECT * FROM clients WHERE id = ?", "params": [[1]]})
        self.assertError(reply, 400, "SqlError")


if __name__ == "__main__":
    unittest.main()
