"""Unit tests for Record.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import dataclasses
import unittest

from tinydb.record import Record


class RecordTests(unittest.TestCase):
    def test_holds_key_and_value(self):
        record = Record("name", "Ada")
        self.assertEqual(record.key, "name")
        self.assertEqual(record.value, "Ada")

    def test_value_defaults_to_none(self):
        self.assertIsNone(Record("empty").value)

    def test_records_with_same_data_are_equal(self):
        self.assertEqual(Record("a", 1), Record("a", 1))
        self.assertNotEqual(Record("a", 1), Record("a", 2))

    def test_is_frozen(self):
        record = Record("a", 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            # On purpose: this line should fail. "type: ignore" tells VS Code not to flag it.
            record.value = 2  # type: ignore

    def test_rejects_bad_keys(self):
        with self.assertRaises(TypeError):
            # On purpose: 123 is not a string, so this should fail.
            Record(123, "x")  # type: ignore
        with self.assertRaises(ValueError):
            Record("", "x")

    def test_rejects_unserializable_value(self):
        with self.assertRaises(TypeError):
            Record("bad", object())

    def test_value_is_stored_as_json_would_load_it(self):
        self.assertEqual(Record("t", (1, 2)).value, [1, 2])
        self.assertEqual(Record("d", {1: "one"}).value, {"1": "one"})

    def test_to_dict_and_from_dict_round_trip(self):
        record = Record("plans", ["Part A", "Part B"])
        self.assertEqual(record.to_dict(), {"key": "plans", "value": ["Part A", "Part B"]})
        self.assertEqual(Record.from_dict(record.to_dict()), record)

    def test_from_dict_ignores_extra_fields(self):
        record = Record.from_dict({"op": "set", "key": "a", "value": 1})
        self.assertEqual(record, Record("a", 1))

    def test_from_dict_requires_value(self):
        with self.assertRaises(KeyError):
            Record.from_dict({"key": "a"})


if __name__ == "__main__":
    unittest.main()
