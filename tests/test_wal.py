"""Unit tests for WriteAheadLog: transactions and crash recovery.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.storage import CorruptLogError
from tinydb.wal import WriteAheadLog


def line(entry):
    return json.dumps(entry) + "\n"


class WriteAheadLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "test.jsonl")
        self.wal = WriteAheadLog(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def replayed(self):
        """Replay the log with a new WriteAheadLog and return every entry applied."""
        seen = []
        WriteAheadLog(self.path).replay(seen.append)
        return seen

    def test_transaction_is_wrapped_in_begin_and_commit(self):
        self.wal.append_transaction([{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])
        lines = [json.loads(text) for text in self.read().splitlines()]
        self.assertEqual(lines[0], {"op": "begin", "tx": 1})
        self.assertEqual(lines[-1], {"op": "commit", "tx": 1})
        self.assertEqual(self.wal.entry_count, 4)

    def test_committed_transaction_is_replayed(self):
        self.wal.append_transaction([{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])
        self.assertEqual(self.replayed(), [{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])

    def test_single_entry_needs_no_begin_or_commit(self):
        self.wal.append_transaction([{"op": "set", "key": "a"}])
        self.assertEqual(self.read(), line({"op": "set", "key": "a"}))

    def test_empty_transaction_writes_nothing(self):
        self.wal.append_transaction([])
        self.assertFalse(os.path.exists(self.path))

    def test_lines_outside_transactions_still_work(self):
        # This is what every Phase 1-3 log looks like.
        self.write(line({"op": "set", "key": "a"}) + line({"op": "delete", "key": "a"}))
        self.assertEqual(self.replayed(), [{"op": "set", "key": "a"}, {"op": "delete", "key": "a"}])

    def test_unfinished_transaction_is_rolled_back_and_trimmed(self):
        kept = line({"op": "set", "key": "a"})
        self.write(kept + line({"op": "begin", "tx": 1}) + line({"op": "set", "key": "b"}))
        self.assertEqual(self.replayed(), [{"op": "set", "key": "a"}])
        self.assertEqual(self.read(), kept)

    def test_transaction_cut_off_mid_line_is_rolled_back(self):
        kept = line({"op": "set", "key": "a"})
        self.write(kept + line({"op": "begin", "tx": 1}) + line({"op": "set", "key": "b"}) + '{"op": "com')
        self.assertEqual(self.replayed(), [{"op": "set", "key": "a"}])
        self.assertEqual(self.read(), kept)

    def test_appending_after_recovery_works(self):
        self.write(line({"op": "begin", "tx": 1}) + line({"op": "set", "key": "lost"}))
        wal = WriteAheadLog(self.path)
        wal.replay(lambda entry: None)
        wal.append_transaction([{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])
        self.assertEqual(self.replayed(), [{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])

    def test_transaction_numbers_continue_after_reopening(self):
        self.wal.append_transaction([{"op": "set", "key": "a"}, {"op": "set", "key": "b"}])
        wal = WriteAheadLog(self.path)
        wal.replay(lambda entry: None)
        self.assertEqual(wal.next_tx, 2)

    def test_commit_without_begin_is_corruption(self):
        self.write(line({"op": "commit", "tx": 1}) + line({"op": "set", "key": "a"}))
        with self.assertRaises(CorruptLogError):
            self.replayed()

    def test_bad_line_inside_committed_transaction_is_corruption(self):
        self.write(line({"op": "begin", "tx": 1}) + "not json\n" + line({"op": "commit", "tx": 1}))
        with self.assertRaises(CorruptLogError):
            self.replayed()

    def test_apply_error_in_committed_transaction_is_corruption(self):
        self.write(line({"op": "begin", "tx": 1}) + line({"op": "set"}) + line({"op": "commit", "tx": 1}))

        def apply(entry):
            entry["key"]  # raises KeyError for the bad entry

        with self.assertRaises(CorruptLogError):
            WriteAheadLog(self.path).replay(apply)

    def test_entry_count_leaves_out_rolled_back_lines(self):
        self.write(line({"op": "set", "key": "a"}) + line({"op": "begin", "tx": 1}) + line({"op": "set", "key": "b"}))
        wal = WriteAheadLog(self.path)
        wal.replay(lambda entry: None)
        self.assertEqual(wal.entry_count, 1)


if __name__ == "__main__":
    unittest.main()
