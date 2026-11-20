"""Unit tests for sample.py: the made-up Medicare data used by benchmarks and the demo.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import io
import os
import tempfile
import unittest

from tinydb.database import Database
from tinydb.sample import PLANS, generate, insert_statements, load, main, sql_value
from tinydb.sql import parse


class SqlValueTest(unittest.TestCase):
    def test_each_type(self):
        self.assertEqual(sql_value(None), "NULL")
        self.assertEqual(sql_value(True), "TRUE")
        self.assertEqual(sql_value(False), "FALSE")
        self.assertEqual(sql_value(42), "42")
        self.assertEqual(sql_value(4.5), "4.5")
        self.assertEqual(sql_value("Ada"), "'Ada'")

    def test_quote_inside_text_is_doubled(self):
        self.assertEqual(sql_value("O'Brien"), "'O''Brien'")


class GenerateTest(unittest.TestCase):
    def test_sizes(self):
        data = generate(clients=50)
        self.assertEqual(len(data["plans"]), len(PLANS))
        self.assertEqual(len(data["clients"]), 50)
        self.assertGreaterEqual(len(data["enrollment_periods"]), 50)  # one or two each
        self.assertLessEqual(len(data["enrollment_periods"]), 100)

    def test_same_seed_same_rows(self):
        self.assertEqual(generate(30, seed=1), generate(30, seed=1))

    def test_different_seed_different_rows(self):
        self.assertNotEqual(generate(30, seed=1)["clients"], generate(30, seed=2)["clients"])

    def test_rows_make_sense(self):
        data = generate(200)
        client_ids = {row[0] for row in data["clients"]}
        plan_codes = {row[0] for row in data["plans"]}
        for client in data["clients"]:
            self.assertTrue(client[4] is None or client[4] in plan_codes)
        for period in data["enrollment_periods"]:
            self.assertIn(period[1], client_ids)
            self.assertLess(period[4], period[5])  # starts before its deadline (ISO dates sort as text)


class InsertStatementsTest(unittest.TestCase):
    def test_batches(self):
        rows = generate(25)["clients"]
        statements = insert_statements("clients", rows, batch=10)
        self.assertEqual(len(statements), 3)  # 10 + 10 + 5
        self.assertEqual(sum(len(parse(s)[0].rows) for s in statements), 25)

    def test_statements_parse(self):
        for table, rows in generate(20).items():
            for statement in insert_statements(table, rows):
                self.assertEqual(parse(statement)[0].table, table)


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.dir = os.path.join(self.tmpdir.name, "data")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_fills_every_table(self):
        db = Database(self.dir)
        counts = load(db, clients=40, batch=15)
        self.assertEqual(counts["clients"], 40)
        for table, count in counts.items():
            self.assertEqual(len(db.table(table)), count)

    def test_rows_survive_a_restart(self):
        counts = load(Database(self.dir), clients=40)
        reopened = Database(self.dir)
        result = reopened.run("SELECT id FROM enrollment_periods WHERE period = 'SEP';")[0]
        self.assertLessEqual(len(result.rows), counts["enrollment_periods"])
        self.assertEqual(len(reopened.table("clients")), 40)

    def test_main_makes_a_folder(self):
        out = io.StringIO()
        self.assertEqual(main(["--dir", self.dir, "--clients", "30"], out=out), 0)
        self.assertIn("clients", out.getvalue())
        self.assertEqual(len(Database(self.dir).table("clients")), 30)

    def test_main_refuses_a_folder_with_data(self):
        main(["--dir", self.dir, "--clients", "5"], out=io.StringIO())
        out = io.StringIO()
        self.assertEqual(main(["--dir", self.dir, "--clients", "5"], out=out), 1)
        self.assertIn("isn't empty", out.getvalue())

    def test_main_refuses_zero_clients(self):
        out = io.StringIO()
        self.assertEqual(main(["--dir", self.dir, "--clients", "0"], out=out), 1)
        self.assertFalse(os.path.exists(self.dir))


if __name__ == "__main__":
    unittest.main()
