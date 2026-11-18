"""Unit tests for HashTable.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import random
import unittest

from tinydb.hashtable import HashTable


class Collider:
    """A key whose hash is always 1, so every Collider lands in the same bucket."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return 1

    def __eq__(self, other):
        return isinstance(other, Collider) and other.name == self.name


class HashTableTests(unittest.TestCase):
    def setUp(self):
        self.table = HashTable()

    def test_new_table_is_empty(self):
        self.assertEqual(len(self.table), 0)
        self.assertEqual(list(self.table.items()), [])
        self.assertEqual(repr(self.table), "HashTable(size=0, capacity=8)")

    def test_put_and_get(self):
        self.table.put("plan", "PPO")
        self.assertEqual(self.table.get("plan"), "PPO")
        self.assertEqual(len(self.table), 1)

    def test_get_missing_returns_default(self):
        self.assertIsNone(self.table.get("nope"))
        self.assertEqual(self.table.get("nope", 0), 0)

    def test_put_replaces_existing_value(self):
        self.table.put("x", 1)
        self.table.put("x", 2)
        self.assertEqual(self.table.get("x"), 2)
        self.assertEqual(len(self.table), 1)

    def test_remove(self):
        self.table.put("x", 1)
        self.assertTrue(self.table.remove("x"))
        self.assertFalse(self.table.remove("x"))
        self.assertNotIn("x", self.table)
        self.assertEqual(len(self.table), 0)

    def test_contains_even_when_value_is_none(self):
        self.table.put("empty", None)
        self.assertIn("empty", self.table)
        self.assertNotIn("other", self.table)

    def test_any_hashable_key_works(self):
        self.table.put(42, "number")
        self.table.put((3, "PPO"), "tuple")
        self.assertEqual(self.table.get(42), "number")
        self.assertEqual(self.table.get((3, "PPO")), "tuple")

    def test_collisions_share_a_bucket(self):
        a, b = Collider("a"), Collider("b")
        self.table.put(a, 1)
        self.table.put(b, 2)
        self.assertEqual(self.table.get(Collider("a")), 1)
        self.assertEqual(self.table.get(Collider("b")), 2)
        self.assertTrue(self.table.remove(a))
        self.assertEqual(self.table.get(b), 2)

    def test_grows_when_load_factor_passes_limit(self):
        for n in range(6):
            self.table.put(n, n)
        self.assertEqual(self.table.capacity, 8)  # 6 / 8 = 0.75, not over the limit yet
        self.table.put(6, 6)
        self.assertEqual(self.table.capacity, 16)  # 7 / 8 was too full, so it doubled
        self.assertLessEqual(self.table.load_factor, HashTable.MAX_LOAD_FACTOR)

    def test_everything_still_found_after_growing(self):
        for n in range(1000):
            self.table.put(f"key{n}", n)
        self.assertEqual(len(self.table), 1000)
        for n in range(1000):
            self.assertEqual(self.table.get(f"key{n}"), n)

    def test_iterating_gives_every_key(self):
        for key in ("a", "b", "c"):
            self.table.put(key, key.upper())
        self.assertEqual(sorted(self.table), ["a", "b", "c"])
        self.assertEqual(sorted(self.table.items()), [("a", "A"), ("b", "B"), ("c", "C")])

    def test_capacity_must_be_positive(self):
        with self.assertRaises(ValueError):
            HashTable(0)

    def test_behaves_like_a_dict(self):
        rng = random.Random(3)
        expected = {}
        for _ in range(2000):
            key = rng.randint(0, 100)
            if rng.random() < 0.3:
                self.assertEqual(self.table.remove(key), key in expected)
                expected.pop(key, None)
            else:
                value = rng.random()
                self.table.put(key, value)
                expected[key] = value
        self.assertEqual(dict(self.table.items()), expected)
        self.assertEqual(len(self.table), len(expected))


if __name__ == "__main__":
    unittest.main()
