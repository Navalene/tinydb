"""Unit tests for Storage, the append-only log file.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.storage import CorruptLogError, Storage


def read_log(path):
    """Return the log file's entries as a list of dicts."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.path = os.path.join(self.tmpdir.name, "test.jsonl")
        self.storage = Storage(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def replayed(self, storage=None):
        """Replay the log and return every entry it passed to apply."""
        seen = []
        (storage or Storage(self.path)).replay(seen.append)
        return seen

    def test_missing_file_replays_nothing(self):
        self.assertEqual(self.replayed(), [])
        self.assertFalse(os.path.exists(self.path))

    def test_empty_file_replays_nothing(self):
        open(self.path, "w").close()
        self.assertEqual(self.replayed(), [])

    def test_append_writes_one_line_per_entry(self):
        self.storage.append({"op": "set", "key": "x", "value": 1})
        self.storage.append({"op": "delete", "key": "x"})
        self.assertEqual(read_log(self.path), [
            {"op": "set", "key": "x", "value": 1},
            {"op": "delete", "key": "x"},
        ])
        self.assertEqual(self.storage.entry_count, 2)

    def test_existing_lines_are_never_rewritten(self):
        self.storage.append({"n": 1})
        with open(self.path, "rb") as f:
            before = f.read()
        self.storage.append({"n": 2})
        with open(self.path, "rb") as f:
            self.assertTrue(f.read().startswith(before))

    def test_replay_returns_entries_in_order(self):
        for n in range(3):
            self.storage.append({"n": n})
        fresh = Storage(self.path)
        self.assertEqual(self.replayed(fresh), [{"n": 0}, {"n": 1}, {"n": 2}])
        self.assertEqual(fresh.entry_count, 3)

    def test_unicode_survives(self):
        self.storage.append({"name": "José ✓"})
        self.assertEqual(self.replayed(), [{"name": "José ✓"}])

    def test_torn_last_line_is_ignored_and_trimmed(self):
        self.storage.append({"n": 1})
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"n": 2, "tor')
        self.assertEqual(self.replayed(), [{"n": 1}])
        Storage(self.path).append({"n": 3})
        self.assertEqual(read_log(self.path), [{"n": 1}, {"n": 3}])

    def test_garbage_last_complete_line_is_ignored(self):
        self.storage.append({"n": 1})
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        self.assertEqual(self.replayed(), [{"n": 1}])
        self.assertEqual(read_log(self.path), [{"n": 1}])

    def test_corruption_in_the_middle_raises(self):
        self.storage.append({"n": 1})
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        self.storage.append({"n": 2})
        with self.assertRaises(CorruptLogError):
            self.replayed()

    def test_corrupt_log_error_is_a_value_error(self):
        self.assertTrue(issubclass(CorruptLogError, ValueError))

    def test_apply_error_on_last_line_counts_as_torn(self):
        self.storage.append({"n": 1})
        self.storage.append({"n": "bad"})

        def apply(entry):
            if not isinstance(entry["n"], int):
                raise ValueError("n must be an int")

        fresh = Storage(self.path)
        fresh.replay(apply)
        self.assertEqual(fresh.entry_count, 1)
        self.assertEqual(read_log(self.path), [{"n": 1}])

    def test_apply_error_in_the_middle_raises(self):
        self.storage.append({"n": "bad"})
        self.storage.append({"n": 1})

        def apply(entry):
            if not isinstance(entry["n"], int):
                raise ValueError("n must be an int")

        with self.assertRaises(CorruptLogError):
            Storage(self.path).replay(apply)

    def test_rewrite_replaces_the_whole_log(self):
        for n in range(5):
            self.storage.append({"n": n})
        self.storage.rewrite([{"n": 4}])
        self.assertEqual(read_log(self.path), [{"n": 4}])
        self.assertEqual(self.storage.entry_count, 1)

    def test_rewrite_with_nothing_leaves_an_empty_log(self):
        self.storage.append({"n": 1})
        self.storage.rewrite([])
        self.assertEqual(read_log(self.path), [])
        self.assertEqual(self.storage.entry_count, 0)

    def test_rewrite_leaves_no_temp_files(self):
        self.storage.append({"n": 1})
        self.storage.rewrite([{"n": 1}])
        self.assertEqual(os.listdir(self.tmpdir.name), ["test.jsonl"])

    def test_failed_rewrite_keeps_the_old_log(self):
        self.storage.append({"n": 1})
        with self.assertRaises(TypeError):
            self.storage.rewrite([{"n": object()}])
        self.assertEqual(read_log(self.path), [{"n": 1}])
        self.assertEqual(os.listdir(self.tmpdir.name), ["test.jsonl"])


if __name__ == "__main__":
    unittest.main()

    

