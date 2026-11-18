"""Unit tests for Database: tables in a folder, and running SQL against them.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import os
import tempfile
import unittest

from tinydb.database import Database, DatabaseError
from tinydb.lexer import SqlError
from tinydb.schema import SchemaError

SETUP = """
CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, plan TEXT, age INT, premium FLOAT);
INSERT INTO clients VALUES
    (1, 'Ada', 'PPO', 70, 25.5),
    (2, 'Bob', 'HMO', 66, 0),
    (3, 'Cy', 'PPO', 81, 40),
    (4, 'Di', NULL, 64, NULL);
"""


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.dir = os.path.join(self.tmpdir.name, "data")
        self.db = Database(self.dir)
        self.db.run(SETUP)

    def tearDown(self):
        self.tmpdir.cleanup()

    def query(self, sql):
        """Run one statement and return its Result."""
        results = self.db.run(sql)
        self.assertEqual(len(results), 1)
        return results[0]

    def ids(self, sql):
        """Run a SELECT that starts with the id column and return the ids."""
        return [row[0] for row in self.query(sql).rows]


class TableTests(DatabaseTestCase):
    def test_folder_is_only_created_when_needed(self):
        empty = os.path.join(self.tmpdir.name, "empty")
        Database(empty)
        self.assertFalse(os.path.exists(empty))

    def test_files_on_disk(self):
        self.assertEqual(sorted(os.listdir(self.dir)), ["_catalog.jsonl", "clients.jsonl"])

    def test_create_table_twice(self):
        with self.assertRaises(DatabaseError):
            self.query("CREATE TABLE clients (id INT PRIMARY KEY)")

    def test_reserved_table_name(self):
        with self.assertRaises(DatabaseError):
            self.query("CREATE TABLE _catalog (id INT PRIMARY KEY)")

    def test_needs_a_primary_key(self):
        with self.assertRaises(SchemaError):
            self.query("CREATE TABLE notes (body TEXT)")

    def test_drop_table(self):
        self.assertEqual(self.query("DROP TABLE clients").message, "Dropped table clients")
        self.assertEqual(self.db.table_names(), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "clients.jsonl")))
        with self.assertRaises(DatabaseError):
            self.query("SELECT * FROM clients")

    def test_show_tables(self):
        self.query("CREATE TABLE plans (code TEXT PRIMARY KEY)")
        self.assertEqual(self.query("SHOW TABLES").rows, (("clients",), ("plans",)))

    def test_describe(self):
        self.query("CREATE INDEX ON clients (age)")
        result = self.query("DESCRIBE clients")
        self.assertEqual(result.columns, ("column", "type", "notes"))
        self.assertEqual(result.rows[0], ("id", "INT", "PRIMARY KEY"))
        self.assertEqual(result.rows[3], ("age", "INT", "btree index"))

    def test_everything_survives_reopening(self):
        self.query("CREATE INDEX ON clients (age)")
        db = Database(self.dir)
        self.assertEqual(db.table_names(), ["clients"])
        result = db.run("SELECT id FROM clients WHERE age > 65")[0]
        self.assertEqual(result.rows, ((1,), (2,), (3,)))
        self.assertEqual(result.plan, "btree index on age")


class InsertTests(DatabaseTestCase):
    def test_insert_message(self):
        self.assertEqual(self.query("INSERT INTO clients (id, name) VALUES (5, 'Eve')").message, "Inserted 1 row")

    def test_types_are_checked(self):
        with self.assertRaises(SchemaError):
            self.query("INSERT INTO clients VALUES (5, 'Eve', 'PPO', 'seventy', 10)")

    def test_not_null_is_checked(self):
        with self.assertRaises(SchemaError):
            self.query("INSERT INTO clients (id) VALUES (5)")

    def test_wrong_number_of_values(self):
        with self.assertRaises(DatabaseError):
            self.query("INSERT INTO clients VALUES (5, 'Eve')")

    def test_duplicate_primary_key(self):
        with self.assertRaises(DatabaseError):
            self.query("INSERT INTO clients (id, name) VALUES (1, 'Again')")

    def test_one_bad_row_means_no_rows_are_inserted(self):
        with self.assertRaises(DatabaseError):
            self.query("INSERT INTO clients (id, name) VALUES (5, 'Eve'), (6, 'Fay'), (5, 'Eve again')")
        self.assertEqual(self.ids("SELECT id FROM clients"), [1, 2, 3, 4])


class SelectTests(DatabaseTestCase):
    def test_select_star(self):
        result = self.query("SELECT * FROM clients")
        self.assertEqual(result.columns, ("id", "name", "plan", "age", "premium"))
        self.assertEqual(result.rows[0], (1, "Ada", "PPO", 70, 25.5))
        self.assertEqual(result.rows[3], (4, "Di", None, 64, None))
        self.assertEqual(result.message, "4 rows")

    def test_select_some_columns(self):
        self.assertEqual(self.query("SELECT name, age FROM clients WHERE id = 2").rows, (("Bob", 66),))

    def test_unknown_column(self):
        with self.assertRaises(SchemaError):
            self.query("SELECT zip FROM clients")
        with self.assertRaises(SchemaError):
            self.query("SELECT * FROM clients WHERE zip = '02134'")

    def test_where_comparisons(self):
        self.assertEqual(self.ids("SELECT id FROM clients WHERE plan = 'PPO'"), [1, 3])
        self.assertEqual(self.ids("SELECT id FROM clients WHERE plan != 'PPO'"), [2])  # NULL plan never matches
        self.assertEqual(self.ids("SELECT id FROM clients WHERE age < 66"), [4])
        self.assertEqual(self.ids("SELECT id FROM clients WHERE age >= 70"), [1, 3])
        self.assertEqual(self.ids("SELECT id FROM clients WHERE premium > 20"), [1, 3])

    def test_where_and(self):
        self.assertEqual(self.ids("SELECT id FROM clients WHERE plan = 'PPO' AND age < 80"), [1])

    def test_where_value_must_fit_the_column(self):
        with self.assertRaises(SchemaError):
            self.query("SELECT * FROM clients WHERE age = 'old'")

    def test_order_by_and_limit(self):
        self.assertEqual(self.ids("SELECT id FROM clients ORDER BY age"), [4, 2, 1, 3])
        self.assertEqual(self.ids("SELECT id FROM clients ORDER BY age DESC LIMIT 2"), [3, 1])

    def test_nulls_sort_last_or_first_when_desc(self):
        self.assertEqual(self.ids("SELECT id FROM clients ORDER BY plan"), [2, 1, 3, 4])
        self.assertEqual(self.ids("SELECT id FROM clients ORDER BY plan DESC"), [4, 1, 3, 2])

    def test_from_a_missing_table(self):
        with self.assertRaises(DatabaseError):
            self.query("SELECT * FROM plans")


class PlanTests(DatabaseTestCase):
    def plan(self, sql):
        return self.query(sql).plan

    def test_full_scan_without_an_index(self):
        self.assertEqual(self.plan("SELECT * FROM clients WHERE plan = 'PPO'"), "full scan")

    def test_primary_key_lookup(self):
        self.assertEqual(self.plan("SELECT * FROM clients WHERE age > 1 AND id = 3"), "primary key lookup")
        self.assertEqual(self.ids("SELECT id FROM clients WHERE id = 99"), [])

    def test_hash_index_for_equals(self):
        self.query("CREATE INDEX ON clients USING HASH (plan)")
        self.assertEqual(self.plan("SELECT * FROM clients WHERE plan = 'PPO'"), "hash index on plan")
        # A hash index can't help with a range.
        self.assertEqual(self.plan("SELECT * FROM clients WHERE plan > 'A'"), "full scan")

    def test_btree_index_for_ranges(self):
        self.query("CREATE INDEX ON clients (age)")
        self.assertEqual(self.plan("SELECT * FROM clients WHERE age > 66"), "btree index on age")
        # The index finds age >= 66, then the strict > check removes Bob.
        self.assertEqual(self.ids("SELECT id FROM clients WHERE age > 66"), [1, 3])

    def test_order_by_adds_a_sort_step(self):
        self.assertEqual(self.plan("SELECT * FROM clients ORDER BY age"), "full scan + merge sort")

    def test_index_on_missing_column(self):
        with self.assertRaises(SchemaError):
            self.query("CREATE INDEX ON clients (zip)")

    def test_index_twice(self):
        self.query("CREATE INDEX ON clients (age)")
        self.assertIn("already has", self.query("CREATE INDEX ON clients (age)").message)


class UpdateAndDeleteTests(DatabaseTestCase):
    def test_update(self):
        result = self.query("UPDATE clients SET plan = 'HMO', premium = 30 WHERE plan = 'PPO'")
        self.assertEqual(result.message, "Updated 2 rows")
        self.assertEqual(self.query("SELECT plan, premium FROM clients WHERE id = 3").rows, (("HMO", 30.0),))

    def test_update_to_null(self):
        self.query("UPDATE clients SET plan = NULL WHERE id = 1")
        self.assertEqual(self.query("SELECT plan FROM clients WHERE id = 1").rows, ((None,),))

    def test_update_keeps_indexes_right(self):
        self.query("CREATE INDEX ON clients (age)")
        self.query("UPDATE clients SET age = 90 WHERE id = 1")
        self.assertEqual(self.ids("SELECT id FROM clients WHERE age >= 85"), [1])

    def test_update_checks_types(self):
        with self.assertRaises(SchemaError):
            self.query("UPDATE clients SET age = 'old'")

    def test_cannot_update_primary_key(self):
        with self.assertRaises(DatabaseError):
            self.query("UPDATE clients SET id = 9 WHERE id = 1")

    def test_delete_with_where(self):
        self.assertEqual(self.query("DELETE FROM clients WHERE age > 65").message, "Deleted 3 rows")
        self.assertEqual(self.ids("SELECT id FROM clients"), [4])

    def test_delete_everything(self):
        self.query("DELETE FROM clients")
        self.assertEqual(self.query("SELECT * FROM clients").rows, ())

    def test_changes_survive_reopening(self):
        self.query("UPDATE clients SET age = 71 WHERE id = 1")
        self.query("DELETE FROM clients WHERE id = 2")
        rows = Database(self.dir).run("SELECT id, age FROM clients")[0].rows
        self.assertEqual(rows, ((1, 71), (3, 81), (4, 64)))


class ScriptTests(DatabaseTestCase):
    def test_run_returns_one_result_per_statement(self):
        results = self.db.run("SELECT id FROM clients WHERE id = 1; SELECT id FROM clients WHERE id = 2;")
        self.assertEqual([result.rows for result in results], [((1,),), ((2,),)])

    def test_syntax_error_runs_nothing(self):
        with self.assertRaises(SqlError):
            self.db.run("DELETE FROM clients; SELECT FROM")
        self.assertEqual(len(self.db.table("clients")), 4)


if __name__ == "__main__":
    unittest.main()
