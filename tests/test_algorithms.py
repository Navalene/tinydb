"""Unit tests for binary_search and merge_sort.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import bisect
import random
import unittest

from tinydb.algorithms import binary_search, merge_sort


class BinarySearchTests(unittest.TestCase):
    def test_finds_position_of_present_value(self):
        self.assertEqual(binary_search([10, 20, 30, 40], 30), 2)

    def test_finds_insert_position_of_missing_value(self):
        items = [10, 20, 30, 40]
        self.assertEqual(binary_search(items, 5), 0)
        self.assertEqual(binary_search(items, 25), 2)
        self.assertEqual(binary_search(items, 99), 4)

    def test_empty_list(self):
        self.assertEqual(binary_search([], 7), 0)

    def test_duplicates_return_first_copy(self):
        self.assertEqual(binary_search([1, 2, 2, 2, 3], 2), 1)

    def test_works_on_strings(self):
        self.assertEqual(binary_search(["ada", "grace", "linus"], "grace"), 1)

    def test_matches_bisect_left(self):
        rng = random.Random(1)
        for _ in range(200):
            items = sorted(rng.randint(0, 50) for _ in range(rng.randint(0, 30)))
            target = rng.randint(-5, 55)
            self.assertEqual(binary_search(items, target), bisect.bisect_left(items, target))


class MergeSortTests(unittest.TestCase):
    def test_sorts_numbers(self):
        self.assertEqual(merge_sort([5, 2, 9, 1, 5, 6]), [1, 2, 5, 5, 6, 9])

    def test_empty_and_single(self):
        self.assertEqual(merge_sort([]), [])
        self.assertEqual(merge_sort([42]), [42])

    def test_returns_a_new_list(self):
        items = [3, 1, 2]
        result = merge_sort(items)
        self.assertEqual(items, [3, 1, 2])
        self.assertEqual(result, [1, 2, 3])

    def test_reverse(self):
        self.assertEqual(merge_sort([1, 3, 2], reverse=True), [3, 2, 1])

    def test_key_function(self):
        words = ["banana", "fig", "cherry"]
        self.assertEqual(merge_sort(words, key=len), ["fig", "banana", "cherry"])

    def test_stable_when_keys_tie(self):
        people = [("Ada", 70), ("Bob", 65), ("Cy", 70), ("Di", 65)]
        by_age = merge_sort(people, key=lambda person: person[1])
        self.assertEqual(by_age, [("Bob", 65), ("Di", 65), ("Ada", 70), ("Cy", 70)])

    def test_stable_in_reverse_too(self):
        people = [("Ada", 70), ("Bob", 65), ("Cy", 70), ("Di", 65)]
        by_age = merge_sort(people, key=lambda person: person[1], reverse=True)
        self.assertEqual(by_age, [("Ada", 70), ("Cy", 70), ("Bob", 65), ("Di", 65)])

    def test_matches_sorted(self):
        rng = random.Random(2)
        for _ in range(100):
            items = [rng.randint(0, 20) for _ in range(rng.randint(0, 40))]
            self.assertEqual(merge_sort(items), sorted(items))
            self.assertEqual(merge_sort(items, reverse=True), sorted(items, reverse=True))


if __name__ == "__main__":
    unittest.main()
