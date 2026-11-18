"""Unit tests for Table.set_many and Table.delete_many, which use WAL transactions.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.table import Table
from tinydb.wal import WriteAheadLog


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "clients.jsonl")
        self.table = Table.open(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_open_uses_a_write_ahead_log(self):
        self.assertIsInstance(self.table.storage, WriteAheadLog)

    def test_set_many(self):
        records = self.table.set_many([("1", {"name": "Ada"}), ("2", {"name": "Bob"})])
        self.assertEqual([record.key for record in records], ["1", "2"])
        self.assertEqual(Table.open(self.path).records()[1].value, {"name": "Bob"})

    def test_set_many_updates_indexes(self):
        self.table.create_index("plan")
        self.table.set_many([("1", {"plan": "PPO"}), ("2", {"plan": "PPO"})])
        self.assertEqual([record.key for record in self.table.find("plan", "PPO")], ["1", "2"])

    def test_bad_value_means_nothing_is_written(self):
        with self.assertRaises(TypeError):
            self.table.set_many([("1", {"name": "Ada"}), ("2", object())])
        self.assertEqual(len(self.table), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_delete_many(self):
        self.table.set_many([("1", 1), ("2", 2), ("3", 3)])
        self.assertEqual(self.table.delete_many(["1", "3", "9"]), 2)
        self.assertEqual(self.table.keys(), ["2"])
        self.assertEqual(Table.open(self.path).keys(), ["2"])

    def test_delete_many_counts_each_key_once(self):
        self.table.set("1", 1)
        self.assertEqual(self.table.delete_many(["1", "1"]), 1)

    def test_crash_mid_transaction_loses_the_whole_transaction(self):
        self.table.set("0", "kept")
        self.table.set_many([("1", "a"), ("2", "b"), ("3", "c")])
        with open(self.path, encoding="utf-8") as f:
            lines = f.readlines()
        # Pretend the power went out before the commit line and part of the last row reached the disk.
        with open(self.path, "w", encoding="utf-8") as f:
            f.writelines(lines[:-2])
            f.write(lines[-2][:10])
        reopened = Table.open(self.path)
        self.assertEqual(reopened.keys(), ["0"])

    def test_compact_drops_begin_and_commit_lines(self):
        self.table.set_many([("1", "a"), ("2", "b")])
        self.table.compact()
        with open(self.path, encoding="utf-8") as f:
            ops = [json.loads(line)["op"] for line in f]
        self.assertEqual(ops, ["set", "set"])


if __name__ == "__main__":
    unittest.main()
