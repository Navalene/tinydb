"""Unit tests for the command-line interface.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import io
import os
import tempfile
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tinydb.cli import is_sql, main, run_command, run_sql
from tinydb.database import Database
from tinydb.table import Table


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "cli.jsonl")
        self.table = Table.open(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_cli(self, *words):
        out = io.StringIO()
        code = run_command(self.table, list(words), out=out)
        return code, out.getvalue().strip()

    def test_set_then_get(self):
        self.assertEqual(self.run_cli("set", "name", "Ada"), (0, "OK"))
        self.assertEqual(self.run_cli("get", "name"), (0, "Ada"))

    def test_json_values_print_as_json(self):
        self.run_cli("set", "plans", '["Part A", "Part B"]')
        self.assertEqual(self.run_cli("get", "plans"), (0, '["Part A", "Part B"]'))

    def test_multi_word_value(self):
        self.run_cli("set", "greeting", "hello", "world")
        self.assertEqual(self.run_cli("get", "greeting"), (0, "hello world"))

    def test_get_missing(self):
        self.assertEqual(self.run_cli("get", "nope"), (1, "Key not found: nope"))

    def test_delete(self):
        self.run_cli("set", "x", "1")
        self.assertEqual(self.run_cli("delete", "x"), (0, "Deleted"))
        self.assertEqual(self.run_cli("delete", "x")[0], 1)

    def test_exists_keys_count(self):
        self.run_cli("set", "b", "2")
        self.run_cli("set", "a", "1")
        self.assertEqual(self.run_cli("exists", "a"), (0, "true"))
        self.assertEqual(self.run_cli("exists", "z"), (0, "false"))
        self.assertEqual(self.run_cli("keys"), (0, "a\nb"))
        self.assertEqual(self.run_cli("count"), (0, "2"))

    def test_compact(self):
        for n in ("1", "2", "3"):
            self.run_cli("set", "x", n)
        self.assertEqual(self.run_cli("compact"), (0, "Compacted log from 3 to 1 entries"))
        self.assertEqual(self.run_cli("get", "x"), (0, "3"))

    def test_clear(self):
        self.run_cli("set", "a", "1")
        self.assertEqual(self.run_cli("clear"), (0, "Cleared"))
        self.assertEqual(self.run_cli("keys"), (0, "(empty)"))

    def test_usage_errors(self):
        self.assertEqual(self.run_cli("set", "only_key"), (2, "Usage: set <key> <value>"))
        self.assertEqual(self.run_cli("get"), (2, "Usage: get <key>"))
        self.assertEqual(self.run_cli("bogus")[0], 2)

    def test_blank_command_does_nothing(self):
        self.assertEqual(self.run_cli(), (0, ""))

    def test_main_one_shot_commands_share_the_file(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--db", self.path, "set", "name", "Ada"]), 0)
            self.assertEqual(main(["--db", self.path, "get", "name"]), 0)
        self.assertEqual(out.getvalue(), "OK\nAda\n")

    def test_main_reports_a_corrupt_log(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("garbage\n")
            f.write('{"op": "set", "key": "a", "value": 1}\n')
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["--db", self.path, "keys"]), 2)
        self.assertIn("corrupted at line 1", err.getvalue())


    # ---- Phase 3: index and search commands ----

    def add_clients(self):
        self.run_cli("set", "c1", '{"name": "Ada", "plan": "PPO", "age": 70}')
        self.run_cli("set", "c2", '{"name": "Bob", "plan": "HMO", "age": 66}')
        self.run_cli("set", "c3", '{"name": "Cy", "plan": "PPO", "age": 81}')

    def test_find_prints_records_and_plan(self):
        self.add_clients()
        self.assertEqual(self.run_cli("find", "plan", "HMO"), (0, (
            'c2: {"name": "Bob", "plan": "HMO", "age": 66}\n'
            "(1 record, full scan)"
        )))

    def test_find_nothing(self):
        self.assertEqual(self.run_cli("find", "plan", "PFFS"), (0, "(0 records, full scan)"))

    def test_index_commands(self):
        self.assertEqual(self.run_cli("indexes"), (0, "(no indexes)"))
        self.assertEqual(self.run_cli("index", "plan"), (0, "Created hash index on plan"))
        self.assertEqual(self.run_cli("index", "plan"), (0, "plan already has a hash index"))
        self.assertEqual(self.run_cli("index", "age", "btree"), (0, "Created btree index on age"))
        self.assertEqual(self.run_cli("indexes"), (0, "age (btree)\nplan (hash)"))
        self.assertEqual(self.run_cli("unindex", "plan"), (0, "Dropped index on plan"))
        self.assertEqual(self.run_cli("unindex", "plan"), (1, "No index on plan"))

    def test_find_uses_the_index(self):
        self.add_clients()
        self.run_cli("index", "plan")
        code, output = self.run_cli("find", "plan", "PPO")
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines()[-1], "(2 records, hash index on plan)")

    def test_range_and_sort(self):
        self.add_clients()
        self.run_cli("index", "age", "btree")
        _, output = self.run_cli("range", "age", "65", "75")
        self.assertEqual([line[:2] for line in output.splitlines()], ["c2", "c1", "(2"])
        self.assertTrue(output.endswith("btree index on age)"))
        _, output = self.run_cli("sort", "age", "desc")
        self.assertEqual([line[:2] for line in output.splitlines()], ["c3", "c1", "c2", "(3"])

    def test_main_index_survives_between_commands(self):
        with redirect_stdout(io.StringIO()) as out:
            main(["--db", self.path, "set", "c1", '{"plan": "PPO"}'])
            main(["--db", self.path, "index", "plan"])
            main(["--db", self.path, "find", "plan", "PPO"])
        self.assertTrue(out.getvalue().endswith("(1 record, hash index on plan)\n"))


class SqlCliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.dir = os.path.join(self.tmpdir.name, "data")
        self.kv_path = os.path.join(self.tmpdir.name, "kv.jsonl")
        self.db = Database(self.dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def sql(self, text):
        out = io.StringIO()
        code = run_sql(self.db, text, out)
        return code, out.getvalue().rstrip("\n")

    def piped(self, text):
        """Run the interactive prompt with text piped in, like python3 main.py < file.sql."""
        stdin = sys.stdin
        sys.stdin = io.StringIO(text)
        try:
            with redirect_stdout(io.StringIO()) as out:
                main(["--db", self.kv_path, "--dir", self.dir])
        finally:
            sys.stdin = stdin
        return out.getvalue()

    def test_is_sql(self):
        for text in ("SELECT * FROM clients", "select 1", "DELETE FROM clients", "anything;", "SHOW TABLES"):
            self.assertTrue(is_sql(text), text)
        for text in ("set name Ada", "delete name", "find plan PPO", ""):
            self.assertFalse(is_sql(text), text)

    def test_messages(self):
        self.assertEqual(self.sql("CREATE TABLE t (id INT PRIMARY KEY);"), (0, "Created table t"))
        self.assertEqual(self.sql("INSERT INTO t VALUES (1), (2);"), (0, "Inserted 2 rows"))
        self.assertEqual(self.sql("DELETE FROM t WHERE id = 1;"), (0, "Deleted 1 row (primary key lookup)"))

    def test_select_prints_a_table(self):
        self.sql("CREATE TABLE clients (id INT PRIMARY KEY, name TEXT, active BOOL);")
        self.sql("INSERT INTO clients VALUES (1, 'Ada', TRUE), (22, NULL, FALSE);")
        self.assertEqual(self.sql("SELECT * FROM clients;"), (0, "\n".join([
            "id | name | active",
            "---+------+-------",
            "1  | Ada  | true",
            "22 | NULL | false",
            "(2 rows, full scan)",
        ])))

    def test_errors_print_and_return_2(self):
        self.assertEqual(self.sql("SELECT * FROM nope;"), (2, "Error: No table named nope"))
        self.assertEqual(self.sql("SELEC * FROM nope;")[0], 2)

    def test_main_one_shot_sql(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--dir", self.dir, "CREATE TABLE t (id INT PRIMARY KEY);"]), 0)
            self.assertEqual(main(["--dir", self.dir, "SELECT", "*", "FROM", "t;"]), 0)
        self.assertEqual(out.getvalue(), "Created table t\nid\n--\n(0 rows, full scan)\n")

    def test_piped_script_mixes_sql_comments_and_key_value_commands(self):
        output = self.piped(
            "-- a comment\n"
            "CREATE TABLE t (id INT PRIMARY KEY,\n"
            "                name TEXT);\n"
            "INSERT INTO t VALUES (1, 'Ada');\n"
            "set greeting hi\n"
            "get greeting\n"
            "SELECT name FROM t\n"
        )
        self.assertEqual(output, "Created table t\nInserted 1 row\nOK\nhi\nname\n----\nAda\n(1 row, full scan)\n")

    def test_bad_sql_in_the_prompt_does_not_stop_it(self):
        output = self.piped("SELECT * FROM nope;\nset a 1\n")
        self.assertEqual(output, "Error: No table named nope\nOK\n")

if __name__ == "__main__":
    unittest.main()
