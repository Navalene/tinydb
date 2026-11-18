"""Unit tests for BTree.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import random
import unittest

from tinydb.btree import BTree


def check_tree(test, tree):
    """Fail the test unless tree follows every B-tree rule."""
    leaf_depths = set()

    def visit(node, depth, low, high):
        test.assertEqual(node.keys, sorted(node.keys), "keys inside a node must be sorted")
        test.assertEqual(len(node.keys), len(node.values))
        test.assertLessEqual(len(node.keys), 2 * tree.t - 1, "node has too many keys")
        if node is not tree.root:
            test.assertGreaterEqual(len(node.keys), tree.t - 1, "node has too few keys")
        for key in node.keys:
            test.assertTrue(low is None or key > low, "key is on the wrong side of its parent")
            test.assertTrue(high is None or key < high, "key is on the wrong side of its parent")
        if node.is_leaf:
            leaf_depths.add(depth)
            return
        test.assertEqual(len(node.children), len(node.keys) + 1)
        bounds = [low] + node.keys + [high]
        for i, child in enumerate(node.children):
            visit(child, depth + 1, bounds[i], bounds[i + 1])

    visit(tree.root, 1, None, None)
    test.assertEqual(len(leaf_depths), 1, "every leaf must be at the same depth")


class BTreeTests(unittest.TestCase):
    def setUp(self):
        # min_degree=2 keeps nodes tiny (1 to 3 keys), so splits happen early and often.
        self.tree = BTree(min_degree=2)

    def fill(self, keys):
        for key in keys:
            self.tree.put(key, f"v{key}")

    def test_new_tree_is_empty(self):
        self.assertEqual(len(self.tree), 0)
        self.assertEqual(list(self.tree.items()), [])
        self.assertEqual(self.tree.height, 1)
        self.assertEqual(repr(self.tree), "BTree(size=0, height=1, min_degree=2)")

    def test_put_and_get(self):
        self.tree.put(65, "Ada")
        self.assertEqual(self.tree.get(65), "Ada")
        self.assertIsNone(self.tree.get(70))
        self.assertEqual(self.tree.get(70, "none"), "none")

    def test_put_replaces_existing_value(self):
        self.tree.put("x", 1)
        self.tree.put("x", 2)
        self.assertEqual(self.tree.get("x"), 2)
        self.assertEqual(len(self.tree), 1)

    def test_root_splits_when_full(self):
        self.fill([10, 20, 30])
        self.assertEqual(self.tree.height, 1)  # 3 keys is the most a node can hold when t=2
        self.fill([40])
        self.assertEqual(self.tree.height, 2)
        self.assertEqual(self.tree.root.keys, [20])
        check_tree(self, self.tree)

    def test_replacing_a_key_that_moved_up(self):
        self.fill(range(10))
        for key in range(10):
            self.tree.put(key, "new")
        self.assertEqual(len(self.tree), 10)
        self.assertTrue(all(value == "new" for _, value in self.tree.items()))
        check_tree(self, self.tree)

    def test_items_come_out_sorted(self):
        keys = list(range(100))
        random.Random(4).shuffle(keys)
        self.fill(keys)
        self.assertEqual(list(self.tree), list(range(100)))
        check_tree(self, self.tree)

    def test_height_grows_slowly(self):
        self.tree = BTree(min_degree=16)
        self.fill(range(10000))
        # 10,000 keys, but at most 4 levels: each step down skips 15 to 31 children's worth of keys.
        self.assertLessEqual(self.tree.height, 4)
        check_tree(self, self.tree)

    def test_range_is_inclusive(self):
        self.fill(range(0, 100, 10))
        self.assertEqual([k for k, _ in self.tree.items(30, 60)], [30, 40, 50, 60])

    def test_range_bounds_between_keys(self):
        self.fill(range(0, 100, 10))
        self.assertEqual([k for k, _ in self.tree.items(25, 55)], [30, 40, 50])

    def test_range_with_one_side_open(self):
        self.fill(range(0, 100, 10))
        self.assertEqual([k for k, _ in self.tree.items(low=75)], [80, 90])
        self.assertEqual([k for k, _ in self.tree.items(high=15)], [0, 10])

    def test_range_with_nothing_in_it(self):
        self.fill(range(0, 100, 10))
        self.assertEqual(list(self.tree.items(41, 49)), [])
        self.assertEqual(list(self.tree.items(200, 300)), [])

    def test_range_matches_a_full_scan(self):
        rng = random.Random(5)
        keys = rng.sample(range(1000), 300)
        self.fill(keys)
        for _ in range(100):
            low, high = sorted(rng.sample(range(-10, 1010), 2))
            expected = sorted(k for k in keys if low <= k <= high)
            self.assertEqual([k for k, _ in self.tree.items(low, high)], expected)

    def test_contains(self):
        self.tree.put("plan", None)
        self.assertIn("plan", self.tree)
        self.assertNotIn("other", self.tree)

    def test_min_degree_must_be_at_least_2(self):
        with self.assertRaises(ValueError):
            BTree(min_degree=1)

    def test_remove_from_leaf(self):
        self.fill([10, 20, 30])
        self.assertTrue(self.tree.remove(20))
        self.assertEqual(list(self.tree), [10, 30])
        self.assertEqual(len(self.tree), 2)

    def test_remove_missing_returns_false(self):
        self.fill([10, 20])
        self.assertFalse(self.tree.remove(99))
        self.assertEqual(len(self.tree), 2)
        self.assertFalse(BTree().remove("anything"))

    def test_remove_key_from_inner_node(self):
        self.fill(range(20))
        root_key = self.tree.root.keys[0]
        self.assertTrue(self.tree.remove(root_key))
        self.assertNotIn(root_key, self.tree)
        self.assertEqual(list(self.tree), [k for k in range(20) if k != root_key])
        check_tree(self, self.tree)

    def test_tree_shrinks_when_emptied(self):
        self.fill(range(50))
        self.assertGreater(self.tree.height, 1)
        for key in range(50):
            self.assertTrue(self.tree.remove(key))
            check_tree(self, self.tree)
        self.assertEqual(len(self.tree), 0)
        self.assertEqual(self.tree.height, 1)

    def test_remove_in_random_order_keeps_tree_valid(self):
        rng = random.Random(6)
        keys = list(range(200))
        rng.shuffle(keys)
        self.fill(keys)
        rng.shuffle(keys)
        for n, key in enumerate(keys, start=1):
            self.assertTrue(self.tree.remove(key))
            self.assertEqual(len(self.tree), 200 - n)
            check_tree(self, self.tree)

    def test_behaves_like_a_sorted_dict(self):
        rng = random.Random(7)
        for min_degree in (2, 3, 5):
            tree = BTree(min_degree)
            expected = {}
            for _ in range(3000):
                key = rng.randint(0, 300)
                if rng.random() < 0.4:
                    self.assertEqual(tree.remove(key), key in expected)
                    expected.pop(key, None)
                else:
                    value = rng.random()
                    tree.put(key, value)
                    expected[key] = value
            check_tree(self, tree)
            self.assertEqual(list(tree.items()), sorted(expected.items()))
            self.assertEqual(len(tree), len(expected))


if __name__ == "__main__":
    unittest.main()
