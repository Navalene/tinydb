"""Unit tests for Table.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.record import Record
from tinydb.storage import CorruptLogError, Storage
from tinydb.table import Table


def read_log(path):
    """Return the log file's entries as a list of dicts."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def value_of(table, key):
    """Return the value stored under key, failing the test if there's no record."""
    record = table.get(key)
    assert record is not None, f"no record for {key!r}"
    return record.value


class TableTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "clients.jsonl")
        self.table = Table.open(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def reopen(self):
        return Table.open(self.path)

    def test_open_names_table_after_file(self):
        self.assertEqual(self.table.name, "clients")
        self.assertEqual(repr(self.table), "Table(name='clients', records=0)")

    def test_new_table_is_empty(self):
        self.assertEqual(len(self.table), 0)
        self.assertEqual(self.table.keys(), [])
        self.assertEqual(self.table.records(), [])

    def test_set_returns_record_and_get_finds_it(self):
        record = self.table.set("name", "Ada")
        self.assertEqual(record, Record("name", "Ada"))
        self.assertEqual(self.table.get("name"), record)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.table.get("missing"))

    def test_set_overwrites(self):
        self.table.set("x", 1)
        self.table.set("x", 2)
        self.assertEqual(value_of(self.table, "x"), 2)
        self.assertEqual(len(self.table), 1)

    def test_delete(self):
        self.table.set("x", 1)
        self.assertTrue(self.table.delete("x"))
        self.assertFalse(self.table.exists("x"))
        self.assertFalse(self.table.delete("x"))

    def test_bad_input_is_rejected_and_nothing_is_written(self):
        with self.assertRaises(TypeError):
            self.table.set(123, "x")
        with self.assertRaises(ValueError):
            self.table.set("", "x")
        with self.assertRaises(TypeError):
            self.table.set("bad", object())
        with self.assertRaises(TypeError):
            self.table.get(5)
        self.assertFalse(os.path.exists(self.path))

    def test_records_and_iteration_are_sorted_by_key(self):
        for key in ["b", "c", "a"]:
            self.table.set(key, key.upper())
        self.assertEqual(self.table.keys(), ["a", "b", "c"])
        self.assertEqual([r.value for r in self.table], ["A", "B", "C"])
        self.assertEqual(self.table.records(), list(self.table))

    def test_contains(self):
        self.table.set("a", 1)
        self.assertIn("a", self.table)
        self.assertNotIn("b", self.table)
        self.assertNotIn(1, self.table)

    def test_every_change_appends_one_log_entry(self):
        self.table.set("x", 1)
        self.table.set("x", 2)
        self.table.delete("x")
        self.table.delete("ghost")
        self.assertEqual(read_log(self.path), [
            {"op": "set", "key": "x", "value": 1},
            {"op": "set", "key": "x", "value": 2},
            {"op": "delete", "key": "x"},
        ])
        self.assertEqual(self.table.log_entries, 3)

    def test_data_survives_restart(self):
        self.table.set("name", "Ada")
        self.table.set("plans", ["Part A", "Part B"])
        self.table.set("age", 36)
        self.table.set("age", 37)
        self.table.set("temp", True)
        self.table.delete("temp")
        reopened = self.reopen()
        self.assertEqual(value_of(reopened, "name"), "Ada")
        self.assertEqual(value_of(reopened, "plans"), ["Part A", "Part B"])
        self.assertEqual(value_of(reopened, "age"), 37)
        self.assertFalse(reopened.exists("temp"))
        self.assertEqual(reopened.log_entries, 6)

    def test_reads_a_phase1_log(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"op": "set", "key": "name", "value": "Ada"}\n')
            f.write('{"op": "set", "key": "gone", "value": 1}\n')
            f.write('{"op": "delete", "key": "gone"}\n')
        self.assertEqual(self.reopen().records(), [Record("name", "Ada")])

    def test_torn_last_line_is_ignored(self):
        self.table.set("safe", "kept")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"op": "set", "key": "torn", "val')
        reopened = self.reopen()
        self.assertEqual(reopened.keys(), ["safe"])
        reopened.set("after", "crash")
        self.assertEqual(self.reopen().keys(), ["after", "safe"])

    def test_unknown_op_as_last_line_is_ignored(self):
        self.table.set("a", 1)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"op": "explode", "key": "b"}) + "\n")
        self.assertEqual(self.reopen().keys(), ["a"])

    def test_unknown_op_in_the_middle_raises(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"op": "explode", "key": "a"}) + "\n")
            f.write(json.dumps({"op": "set", "key": "b", "value": 2}) + "\n")
        with self.assertRaises(CorruptLogError):
            self.reopen()

    def test_compact_keeps_only_live_records(self):
        for i in range(10):
            self.table.set("counter", i)
        self.table.set("gone", True)
        self.table.delete("gone")
        self.assertEqual(self.table.log_entries, 12)
        self.table.compact()
        self.assertEqual(read_log(self.path), [{"op": "set", "key": "counter", "value": 9}])
        self.assertEqual(self.table.log_entries, 1)

    def test_data_survives_compaction_and_restart(self):
        self.table.set("a", 1)
        self.table.set("b", [1, 2])
        self.table.set("a", 2)
        self.table.compact()
        self.table.set("c", "after")
        reopened = self.reopen()
        self.assertEqual([r.to_dict() for r in reopened], [
            {"key": "a", "value": 2},
            {"key": "b", "value": [1, 2]},
            {"key": "c", "value": "after"},
        ])

    def test_clear_empties_table_and_log(self):
        self.table.set("a", 1)
        self.table.clear()
        self.assertEqual(len(self.table), 0)
        self.assertEqual(read_log(self.path), [])
        self.assertEqual(len(self.reopen()), 0)

    def test_table_can_be_built_from_a_storage(self):
        table = Table("plans", Storage(self.path))
        table.set("a", 1)
        self.assertEqual(table.name, "plans")
        self.assertEqual(value_of(self.reopen(), "a"), 1)


if __name__ == "__main__":
    unittest.main()
