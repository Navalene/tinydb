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


class TableSearchTests(unittest.TestCase):
    """Indexes and searching. Every search is checked with and without an index."""

    CLIENTS = {
        "c1": {"name": "Ada", "plan": "PPO", "age": 70},
        "c2": {"name": "Bob", "plan": "HMO", "age": 66},
        "c3": {"name": "Cy", "plan": "PPO", "age": 81},
        "c4": {"name": "Di", "age": 66},
        "note": "not a client",
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "clients.jsonl")
        self.table = Table.open(self.path)
        for key, value in self.CLIENTS.items():
            self.table.set(key, value)

    def tearDown(self):
        self.tmpdir.cleanup()

    def reopen(self):
        return Table.open(self.path)

    def keys_of(self, records):
        return [record.key for record in records]

    def test_find_without_an_index_scans(self):
        self.assertEqual(self.keys_of(self.table.find("plan", "PPO")), ["c1", "c3"])
        self.assertEqual(self.table.last_plan, "full scan")

    def test_find_with_each_kind_of_index(self):
        for kind in ("hash", "btree"):
            self.assertTrue(self.table.create_index("plan", kind))
            self.assertEqual(self.keys_of(self.table.find("plan", "PPO")), ["c1", "c3"])
            self.assertEqual(self.table.last_plan, f"{kind} index on plan")

    def test_find_matches_numbers_not_text(self):
        self.assertEqual(self.keys_of(self.table.find("age", 66)), ["c2", "c4"])
        self.assertEqual(self.table.find("age", "66"), [])

    def test_find_no_matches(self):
        self.table.create_index("plan")
        self.assertEqual(self.table.find("plan", "PFFS"), [])
        self.assertEqual(self.table.find("nothing", "here"), [])

    def test_indexes_list(self):
        self.assertEqual(self.table.indexes(), [])
        self.table.create_index("plan")
        self.table.create_index("age", "btree")
        self.assertEqual(self.table.indexes(), [("age", "btree"), ("plan", "hash")])

    def test_create_same_index_twice_returns_false(self):
        self.assertTrue(self.table.create_index("plan"))
        self.assertFalse(self.table.create_index("plan"))
        self.assertTrue(self.table.create_index("plan", "btree"))
        self.assertEqual(self.table.indexes(), [("plan", "btree")])

    def test_bad_index_requests(self):
        with self.assertRaises(ValueError):
            self.table.create_index("plan", "bitmap")
        with self.assertRaises(ValueError):
            self.table.create_index("")
        with self.assertRaises(ValueError):
            self.table.find("", "x")

    def test_drop_index(self):
        self.table.create_index("plan")
        self.assertTrue(self.table.drop_index("plan"))
        self.assertFalse(self.table.drop_index("plan"))
        self.table.find("plan", "PPO")
        self.assertEqual(self.table.last_plan, "full scan")

    def test_index_follows_set_and_delete(self):
        self.table.create_index("plan")
        self.table.set("c2", {"name": "Bob", "plan": "PPO", "age": 66})
        self.table.set("c5", {"name": "Eve", "plan": "HMO"})
        self.table.delete("c1")
        self.assertEqual(self.keys_of(self.table.find("plan", "PPO")), ["c2", "c3"])
        self.assertEqual(self.keys_of(self.table.find("plan", "HMO")), ["c5"])

    def test_indexes_survive_restart(self):
        self.table.create_index("plan")
        self.table.create_index("age", "btree")
        self.table.drop_index("plan")
        reopened = self.reopen()
        self.assertEqual(reopened.indexes(), [("age", "btree")])
        self.assertEqual(self.keys_of(reopened.find("age", 66)), ["c2", "c4"])
        self.assertEqual(reopened.last_plan, "btree index on age")

    def test_index_entries_in_the_log(self):
        self.table.create_index("age", "btree")
        self.table.drop_index("age")
        self.assertEqual(read_log(self.path)[-2:], [
            {"op": "index", "field": "age", "kind": "btree"},
            {"op": "drop_index", "field": "age"},
        ])

    def test_compact_keeps_indexes(self):
        self.table.create_index("plan")
        self.table.compact()
        self.assertEqual(read_log(self.path)[0], {"op": "index", "field": "plan", "kind": "hash"})
        self.assertEqual(self.table.log_entries, 6)
        self.assertEqual(self.reopen().indexes(), [("plan", "hash")])

    def test_clear_keeps_indexes_but_empties_them(self):
        self.table.create_index("plan")
        self.table.clear()
        self.assertEqual(self.table.find("plan", "PPO"), [])
        self.table.set("c9", {"plan": "PPO"})
        self.assertEqual(self.keys_of(self.table.find("plan", "PPO")), ["c9"])
        self.assertEqual(self.reopen().indexes(), [("plan", "hash")])

    def test_find_range_with_and_without_btree(self):
        expected = ["c2", "c4", "c1"]
        self.assertEqual(self.keys_of(self.table.find_range("age", 65, 75)), expected)
        self.assertEqual(self.table.last_plan, "full scan")
        self.table.create_index("age", "hash")
        self.assertEqual(self.keys_of(self.table.find_range("age", 65, 75)), expected)
        self.assertEqual(self.table.last_plan, "full scan")
        self.table.create_index("age", "btree")
        self.assertEqual(self.keys_of(self.table.find_range("age", 65, 75)), expected)
        self.assertEqual(self.table.last_plan, "btree index on age")

    def test_find_range_open_ended(self):
        for _ in range(2):
            self.assertEqual(self.keys_of(self.table.find_range("age", low=70)), ["c1", "c3"])
            self.assertEqual(self.keys_of(self.table.find_range("age", high=66)), ["c2", "c4"])
            self.table.create_index("age", "btree")

    def test_sort_by_with_and_without_btree(self):
        expected = ["c2", "c4", "c1", "c3"]
        self.assertEqual(self.keys_of(self.table.sort_by("age")), expected)
        self.assertEqual(self.table.last_plan, "full scan + merge sort")
        self.table.create_index("age", "btree")
        self.assertEqual(self.keys_of(self.table.sort_by("age")), expected)
        self.assertEqual(self.table.last_plan, "btree index on age")

    def test_sort_by_reverse_keeps_ties_sorted_by_key(self):
        expected = ["c3", "c1", "c2", "c4"]
        self.assertEqual(self.keys_of(self.table.sort_by("age", reverse=True)), expected)
        self.table.create_index("age", "btree")
        self.assertEqual(self.keys_of(self.table.sort_by("age", reverse=True)), expected)

    def test_sort_by_text_field_skips_records_without_it(self):
        self.assertEqual(self.keys_of(self.table.sort_by("plan")), ["c2", "c1", "c3"])


if __name__ == "__main__":
    unittest.main()
