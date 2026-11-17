"""Unit tests for the tinydb key-value store and CLI.
 
Run from the project root: python3 -m unittest discover -s tests -t .
"""
 
import io
import json
import os
import tempfile
import unittest
 
from tinydb.cli import parse_value, run_command
from tinydb.store import KeyValueStore
 
 
def read_log(path):
    """Return the log file's records as a list of dicts."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
 
 
class KeyValueStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "test.jsonl")
        self.store = KeyValueStore(self.path)
 
    def tearDown(self):
        self.tmpdir.cleanup()
 
    def test_new_store_is_empty(self):
        self.assertEqual(len(self.store), 0)
        self.assertEqual(self.store.keys(), [])
 
    def test_set_and_get(self):
        self.store.set("name", "Ada")
        self.assertEqual(self.store.get("name"), "Ada")
 
    def test_get_missing_returns_default(self):
        self.assertIsNone(self.store.get("missing"))
        self.assertEqual(self.store.get("missing", "fallback"), "fallback")
 
    def test_set_overwrites(self):
        self.store.set("x", 1)
        self.store.set("x", 2)
        self.assertEqual(self.store.get("x"), 2)
        self.assertEqual(len(self.store), 1)
 
    def test_stores_json_types(self):
        self.store.set("int", 42)
        self.store.set("list", [1, 2, 3])
        self.store.set("dict", {"a": True})
        self.store.set("none", None)
        self.store.set("multiline", "line one\nline two")
        reopened = KeyValueStore(self.path)
        self.assertEqual(reopened.get("int"), 42)
        self.assertEqual(reopened.get("list"), [1, 2, 3])
        self.assertEqual(reopened.get("dict"), {"a": True})
        self.assertTrue(reopened.exists("none"))
        self.assertEqual(reopened.get("multiline"), "line one\nline two")
 
    def test_delete(self):
        self.store.set("x", 1)
        self.assertTrue(self.store.delete("x"))
        self.assertFalse(self.store.exists("x"))
        self.assertFalse(self.store.delete("x"))
 
    def test_keys_sorted(self):
        for key in ["b", "c", "a"]:
            self.store.set(key, key)
        self.assertEqual(self.store.keys(), ["a", "b", "c"])
 
    def test_contains(self):
        self.store.set("a", 1)
        self.assertIn("a", self.store)
        self.assertNotIn("b", self.store)
 
    def test_invalid_keys_rejected(self):
        with self.assertRaises(TypeError):
            self.store.set(123, "x")
        with self.assertRaises(ValueError):
            self.store.set("", "x")
 
    def test_unserializable_value_rejected(self):
        with self.assertRaises(TypeError):
            self.store.set("bad", object())
        self.assertFalse(self.store.exists("bad"))
        self.assertFalse(os.path.exists(self.path))
 
 
class AppendOnlyLogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "test.jsonl")
        self.store = KeyValueStore(self.path)
 
    def tearDown(self):
        self.tmpdir.cleanup()
 
    def test_every_change_appends_one_line(self):
        self.store.set("x", 1)
        self.store.set("x", 2)
        self.store.delete("x")
        self.assertEqual(read_log(self.path), [
            {"op": "set", "key": "x", "value": 1},
            {"op": "set", "key": "x", "value": 2},
            {"op": "delete", "key": "x"},
        ])
        self.assertEqual(self.store.log_entries, 3)
 
    def test_existing_lines_are_never_rewritten(self):
        self.store.set("a", 1)
        with open(self.path, "rb") as f:
            before = f.read()
        self.store.set("b", 2)
        with open(self.path, "rb") as f:
            after = f.read()
        self.assertTrue(after.startswith(before))
 
    def test_delete_of_missing_key_writes_nothing(self):
        self.store.delete("ghost")
        self.assertFalse(os.path.exists(self.path))
 
    def test_replay_rebuilds_state(self):
        self.store.set("name", "Ada")
        self.store.set("age", 36)
        self.store.set("age", 37)
        self.store.set("temp", True)
        self.store.delete("temp")
        reopened = KeyValueStore(self.path)
        self.assertEqual(reopened.get("name"), "Ada")
        self.assertEqual(reopened.get("age"), 37)
        self.assertFalse(reopened.exists("temp"))
        self.assertEqual(reopened.log_entries, 5)
 
    def test_torn_last_line_is_ignored(self):
        self.store.set("safe", "kept")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"op": "set", "key": "torn", "val')
        reopened = KeyValueStore(self.path)
        self.assertEqual(reopened.get("safe"), "kept")
        self.assertFalse(reopened.exists("torn"))
 
    def test_torn_line_is_trimmed_so_new_writes_work(self):
        self.store.set("safe", "kept")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"op": "set", "key": "torn", "val')
        reopened = KeyValueStore(self.path)
        reopened.set("after", "crash")
        again = KeyValueStore(self.path)
        self.assertEqual(again.get("safe"), "kept")
        self.assertEqual(again.get("after"), "crash")
        self.assertEqual(len(read_log(self.path)), 2)
 
    def test_garbage_last_complete_line_is_ignored(self):
        self.store.set("safe", "kept")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        reopened = KeyValueStore(self.path)
        self.assertEqual(reopened.keys(), ["safe"])
 
    def test_corruption_in_the_middle_raises(self):
        self.store.set("a", 1)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"op": "set", "key": "b", "value": 2}) + "\n")
        with self.assertRaises(ValueError):
            KeyValueStore(self.path)
 
    def test_unknown_op_in_the_middle_raises(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"op": "explode", "key": "a"}) + "\n")
            f.write(json.dumps({"op": "set", "key": "b", "value": 2}) + "\n")
        with self.assertRaises(ValueError):
            KeyValueStore(self.path)
 
    def test_empty_file_loads_as_empty_store(self):
        open(self.path, "w").close()
        self.assertEqual(len(KeyValueStore(self.path)), 0)
 
 
class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmpdir.name, "test.jsonl")
        self.store = KeyValueStore(self.path)
 
    def tearDown(self):
        self.tmpdir.cleanup()
 
    def test_compact_keeps_only_live_keys(self):
        for i in range(10):
            self.store.set("counter", i)
        self.store.set("gone", True)
        self.store.delete("gone")
        self.assertEqual(self.store.log_entries, 12)
        self.store.compact()
        self.assertEqual(read_log(self.path), [{"op": "set", "key": "counter", "value": 9}])
        self.assertEqual(self.store.log_entries, 1)
 
    def test_compact_shrinks_file(self):
        for i in range(50):
            self.store.set("k", i)
        size_before = os.path.getsize(self.path)
        self.store.compact()
        self.assertLess(os.path.getsize(self.path), size_before)
 
    def test_data_survives_compaction_and_restart(self):
        self.store.set("a", 1)
        self.store.set("b", [1, 2])
        self.store.set("a", 2)
        self.store.compact()
        self.store.set("c", "after")
        reopened = KeyValueStore(self.path)
        self.assertEqual(reopened.get("a"), 2)
        self.assertEqual(reopened.get("b"), [1, 2])
        self.assertEqual(reopened.get("c"), "after")
 
    def test_compact_leaves_no_temp_files(self):
        self.store.set("k", "v")
        self.store.compact()
        self.assertEqual(os.listdir(self.tmpdir.name), ["test.jsonl"])
 
    def test_clear_empties_store_and_log(self):
        self.store.set("a", 1)
        self.store.set("b", 2)
        self.store.clear()
        self.assertEqual(len(self.store), 0)
        self.assertEqual(read_log(self.path), [])
        self.assertEqual(len(KeyValueStore(self.path)), 0)
 
 
class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = KeyValueStore(os.path.join(self.tmpdir.name, "cli.jsonl"))
 
    def tearDown(self):
        self.tmpdir.cleanup()
 
    def run_cli(self, *words):
        out = io.StringIO()
        code = run_command(self.store, list(words), out=out)
        return code, out.getvalue().strip()
 
    def test_parse_value(self):
        self.assertEqual(parse_value("42"), 42)
        self.assertEqual(parse_value("[1, 2]"), [1, 2])
        self.assertEqual(parse_value("hello"), "hello")
 
    def test_set_then_get(self):
        self.assertEqual(self.run_cli("set", "name", "Ada"), (0, "OK"))
        self.assertEqual(self.run_cli("get", "name"), (0, "Ada"))
 
    def test_multi_word_value(self):
        self.run_cli("set", "greeting", "hello", "world")
        self.assertEqual(self.run_cli("get", "greeting"), (0, "hello world"))
 
    def test_get_missing(self):
        code, output = self.run_cli("get", "nope")
        self.assertEqual(code, 1)
        self.assertIn("Key not found", output)
 
    def test_delete(self):
        self.run_cli("set", "x", "1")
        self.assertEqual(self.run_cli("delete", "x"), (0, "Deleted"))
        self.assertEqual(self.run_cli("delete", "x")[0], 1)
 
    def test_exists_keys_count(self):
        self.run_cli("set", "b", "2")
        self.run_cli("set", "a", "1")
        self.assertEqual(self.run_cli("exists", "a"), (0, "true"))
        self.assertEqual(self.run_cli("keys"), (0, "a\nb"))
        self.assertEqual(self.run_cli("count"), (0, "2"))
 
    def test_compact(self):
        self.run_cli("set", "x", "1")
        self.run_cli("set", "x", "2")
        self.run_cli("set", "x", "3")
        self.assertEqual(self.run_cli("compact"), (0, "Compacted log from 3 to 1 entries"))
        self.assertEqual(self.run_cli("get", "x"), (0, "3"))
 
    def test_clear(self):
        self.run_cli("set", "a", "1")
        self.assertEqual(self.run_cli("clear"), (0, "Cleared"))
        self.assertEqual(self.run_cli("keys"), (0, "(empty)"))
 
    def test_usage_errors(self):
        self.assertEqual(self.run_cli("set", "only_key")[0], 2)
        self.assertEqual(self.run_cli("get")[0], 2)
        self.assertEqual(self.run_cli("bogus")[0], 2)
 
 
if __name__ == "__main__":
    unittest.main()
 