"""Unit tests for the query planner's range plans: B-tree ranges use both ends.

Phase 7's benchmark found that WHERE deadline >= X AND deadline <= Y read most
of the table, because only the first condition reached the B-tree. These tests
pin the fix down.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import os
import tempfile
import unittest

from tinydb.database import Database, range_bounds
from tinydb.sql import Condition, parse


class RangeBoundsTest(unittest.TestCase):
    def test_both_ends(self):
        conditions = [Condition("age", ">=", 65), Condition("age", "<=", 70)]
        self.assertEqual(range_bounds(conditions, "age"), (65, 70))

    def test_one_end(self):
        self.assertEqual(range_bounds([Condition("age", ">", 65)], "age"), (65, None))
        self.assertEqual(range_bounds([Condition("age", "<", 70)], "age"), (None, 70))

    def test_tightest_bound_wins(self):
        conditions = [Condition("age", ">", 60), Condition("age", ">=", 65),
                      Condition("age", "<", 90), Condition("age", "<=", 80)]
        self.assertEqual(range_bounds(conditions, "age"), (65, 80))

    def test_other_columns_and_equality_are_ignored(self):
        conditions = [Condition("plan", ">", "A"), Condition("age", "=", 66), Condition("age", "<", 70)]
        self.assertEqual(range_bounds(conditions, "age"), (None, 70))


class RangePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.db = Database(os.path.join(self.tmpdir.name, "data"))
        self.db.run("CREATE TABLE clients (id INT PRIMARY KEY, name TEXT, age INT, birth DATE);")
        rows = ", ".join(f"({i}, 'c{i}', {60 + i % 40}, '19{40 + i % 20}-01-01')" for i in range(1, 201))
        self.db.run(f"INSERT INTO clients VALUES {rows};")

    def tearDown(self):
        self.tmpdir.cleanup()

    def candidates(self, sql):
        """How many rows the plan hands to the WHERE check, before filtering."""
        statement = parse(sql)[0]
        rows, _ = self.db._candidates(self.db.table("clients"), self.db.schema("clients"), list(statement.where))
        return len(rows)

    def ids(self, sql):
        return [row[0] for row in self.db.run(sql)[0].rows]

    def test_btree_reads_only_the_window(self):
        sql = "SELECT id FROM clients WHERE age >= 70 AND age <= 72"
        self.assertEqual(self.candidates(sql), 200)  # no index: every row
        self.db.run("CREATE INDEX ON clients (age);")
        self.assertEqual(self.candidates(sql), 15)  # 3 ages x 5 clients each
        self.assertEqual(self.db.run(sql)[0].plan, "btree index on age")

    def test_same_answers_with_and_without_the_index(self):
        queries = [
            "SELECT id FROM clients WHERE age >= 70 AND age <= 72",
            "SELECT id FROM clients WHERE age > 70 AND age < 72",
            "SELECT id FROM clients WHERE age < 72 AND age > 70",
            "SELECT id FROM clients WHERE age > 60 AND age >= 95 AND age < 99",
            "SELECT id FROM clients WHERE age >= 98",
            "SELECT id FROM clients WHERE age <= 61",
            "SELECT id FROM clients WHERE age >= 80 AND age <= 70",
            "SELECT id FROM clients WHERE age >= 70 AND age <= 72 AND name = 'c11'",
        ]
        before = [self.ids(sql) for sql in queries]
        self.db.run("CREATE INDEX ON clients (age);")
        self.assertEqual([self.ids(sql) for sql in queries], before)

    def test_strict_bounds_leave_out_the_edges(self):
        self.db.run("CREATE INDEX ON clients (age);")
        ages = [row[1] for row in self.db.run("SELECT id, age FROM clients WHERE age > 70 AND age < 72")[0].rows]
        self.assertEqual(set(ages), {71})

    def test_empty_window(self):
        self.db.run("CREATE INDEX ON clients (age);")
        self.assertEqual(self.candidates("SELECT id FROM clients WHERE age >= 80 AND age <= 70"), 0)

    def test_dates(self):
        sql = "SELECT id FROM clients WHERE birth >= '1950-01-01' AND birth <= '1951-01-01'"
        before = self.ids(sql)
        self.db.run("CREATE INDEX ON clients (birth);")
        self.assertEqual(self.ids(sql), before)
        self.assertEqual(self.candidates(sql), 20)  # 2 years x 10 clients each

    def test_hash_index_still_cannot_do_ranges(self):
        self.db.run("CREATE INDEX ON clients USING HASH (age);")
        self.assertEqual(self.db.run("SELECT id FROM clients WHERE age >= 70 AND age <= 72")[0].plan, "full scan")


if __name__ == "__main__":
    unittest.main()
