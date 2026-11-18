"""Unit tests for index_key, HashIndex and BTreeIndex.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import unittest

from tinydb.index import MISSING, BTreeIndex, HashIndex, field_value, index_key
from tinydb.record import Record

CLIENTS = [
    Record("c1", {"name": "Ada", "plan": "PPO", "age": 70}),
    Record("c2", {"name": "Bob", "plan": "HMO", "age": 66}),
    Record("c3", {"name": "Cy", "plan": "PPO", "age": 81}),
    Record("c4", {"name": "Di", "age": 66}),
    Record("note", "not a client"),
]


class FieldValueTests(unittest.TestCase):
    def test_reads_a_field(self):
        self.assertEqual(field_value(CLIENTS[0], "plan"), "PPO")

    def test_missing_field_or_non_object_value(self):
        self.assertIs(field_value(CLIENTS[3], "plan"), MISSING)
        self.assertIs(field_value(CLIENTS[4], "plan"), MISSING)

    def test_null_is_a_real_value(self):
        self.assertIsNone(field_value(Record("x", {"plan": None}), "plan"))


class IndexKeyTests(unittest.TestCase):
    def test_types_sort_in_rank_order(self):
        values = [{"a": 1}, [1], "abc", 70, True, None]
        ordered = sorted(values, key=index_key)
        self.assertEqual(ordered, [None, True, 70, "abc", [1], {"a": 1}])

    def test_same_type_sorts_normally(self):
        self.assertLess(index_key(65), index_key(70.5))
        self.assertLess(index_key("HMO"), index_key("PPO"))
        self.assertLess(index_key([1, 2]), index_key([1, 3]))

    def test_true_is_not_the_number_one(self):
        self.assertNotEqual(index_key(True), index_key(1))

    def test_int_and_float_with_same_value_match(self):
        self.assertEqual(index_key(70), index_key(70.0))
        self.assertEqual(hash(index_key(70)), hash(index_key(70.0)))

    def test_object_key_order_does_not_matter(self):
        self.assertEqual(index_key({"a": 1, "b": 2}), index_key({"b": 2, "a": 1}))

    def test_rejects_non_json_values(self):
        with self.assertRaises(TypeError):
            index_key(object())


class HashIndexTests(unittest.TestCase):
    KIND = "hash"

    def make_index(self, field):
        return HashIndex(field)

    def setUp(self):
        self.index = self.make_index("plan")
        for record in CLIENTS:
            self.index.add(record)

    def test_kind_and_repr(self):
        self.assertEqual(self.index.kind, self.KIND)
        self.assertEqual(repr(self.index), f"{type(self.index).__name__}(field='plan', values=2)")

    def test_find(self):
        self.assertEqual(self.index.find("PPO"), ["c1", "c3"])
        self.assertEqual(self.index.find("HMO"), ["c2"])

    def test_find_missing_value(self):
        self.assertEqual(self.index.find("PFFS"), [])

    def test_records_without_the_field_are_skipped(self):
        self.assertEqual(len(self.index), 2)  # "PPO" and "HMO"

    def test_remove(self):
        self.index.remove(CLIENTS[0])
        self.assertEqual(self.index.find("PPO"), ["c3"])

    def test_removing_last_record_drops_the_value(self):
        self.index.remove(CLIENTS[1])
        self.assertEqual(self.index.find("HMO"), [])
        self.assertEqual(len(self.index), 1)

    def test_removing_unindexed_record_does_nothing(self):
        self.index.remove(CLIENTS[4])
        self.index.remove(Record("c9", {"plan": "PFFS"}))
        self.assertEqual(len(self.index), 2)

    def test_numbers_and_floats_match(self):
        ages = self.make_index("age")
        for record in CLIENTS:
            ages.add(record)
        self.assertEqual(ages.find(66.0), ["c2", "c4"])


class BTreeIndexTests(HashIndexTests):
    """Runs every HashIndexTests test again on a BTreeIndex, plus the range tests below."""

    KIND = "btree"

    def make_index(self, field):
        return BTreeIndex(field, min_degree=2)

    def setUp(self):
        super().setUp()
        self.ages = self.make_index("age")
        for record in CLIENTS:
            self.ages.add(record)

    def test_range(self):
        self.assertEqual(self.ages.range(66, 70), ["c2", "c4", "c1"])

    def test_range_open_ended(self):
        self.assertEqual(self.ages.range(low=70), ["c1", "c3"])
        self.assertEqual(self.ages.range(high=66), ["c2", "c4"])

    def test_ordered(self):
        self.assertEqual(self.ages.ordered(), ["c2", "c4", "c1", "c3"])

    def test_ordered_reverse_keeps_ties_sorted_by_key(self):
        self.assertEqual(self.ages.ordered(reverse=True), ["c3", "c1", "c2", "c4"])


if __name__ == "__main__":
    unittest.main()
