"""BTree: a balanced search tree that keeps its keys in sorted order.

A binary search tree node holds 1 key and has 2 children. A B-tree node holds
many keys (here, up to 2*t - 1) and has one more child than it has keys:

                    [ 30 | 60 ]
                   /     |     \\
        [ 10 | 20 ]  [ 40 | 50 ]  [ 70 | 80 | 90 ]

Inside a node the keys are sorted. Everything in the child to the left of a key
is smaller than it, and everything to the right is bigger. Every leaf sits at
the same depth, so the tree never gets lopsided, and get/put/remove are all
O(log n). Databases use B-trees because a node full of keys fits one disk page,
so even a huge table is only a few pages deep.

The BTree here is a sorted map: put(key, value), get(key), remove(key), and
items(low, high) to read a range of keys in order.
"""

from tinydb.algorithms import binary_search

_NOT_FOUND = object()


class _Node:
    """One node: sorted keys, a value for each key, and children (none in a leaf)."""

    def __init__(self):
        self.keys = []
        self.values = []
        self.children = []

    @property
    def is_leaf(self):
        return not self.children


class BTree:
    """A sorted map from keys to values, kept balanced as a B-tree."""

    def __init__(self, min_degree=16):
        if min_degree < 2:
            raise ValueError("min_degree must be at least 2")
        self.t = min_degree  # every node except the root holds t-1 to 2t-1 keys
        self.root = _Node()
        self._size = 0

    @property
    def max_keys(self):
        return 2 * self.t - 1

    # ---- searching ----

    def get(self, key, default=None):
        """Return the value stored under key, or default if there isn't one."""
        node = self.root
        while True:
            i = binary_search(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                return node.values[i]
            if node.is_leaf:
                return default
            node = node.children[i]  # key can only be in the child at position i

    # ---- adding ----

    def put(self, key, value):
        """Store value under key, replacing any value already there."""
        if len(self.root.keys) == self.max_keys:
            # The root is full. Split it, and the tree grows one level taller.
            old_root = self.root
            self.root = _Node()
            self.root.children.append(old_root)
            self._split_child(self.root, 0)
        self._put_into(self.root, key, value)

    def _put_into(self, node, key, value):
        """Walk down from node to the right leaf. Every node we enter has room."""
        while True:
            i = binary_search(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                node.values[i] = value  # key already here: just replace the value
                return
            if node.is_leaf:
                node.keys.insert(i, key)
                node.values.insert(i, value)
                self._size += 1
                return
            if len(node.children[i].keys) == self.max_keys:
                # Split a full child before going into it, so there's always room.
                self._split_child(node, i)
                if key == node.keys[i]:
                    node.values[i] = value
                    return
                if key > node.keys[i]:
                    i += 1  # key belongs in the new right half
            node = node.children[i]

    def _split_child(self, parent, i):
        """Split the full child at parent.children[i] into two halves.

        Its middle key moves up into parent, between the two halves.
        """
        t = self.t
        child = parent.children[i]
        right = _Node()
        middle_key, middle_value = child.keys[t - 1], child.values[t - 1]

        right.keys, child.keys = child.keys[t:], child.keys[: t - 1]
        right.values, child.values = child.values[t:], child.values[: t - 1]
        if not child.is_leaf:
            right.children, child.children = child.children[t:], child.children[:t]

        parent.keys.insert(i, middle_key)
        parent.values.insert(i, middle_value)
        parent.children.insert(i + 1, right)

    # ---- removing ----

    def remove(self, key):
        """Remove key. Returns True if it was there, False otherwise."""
        found = self._remove_from(self.root, key)
        if not self.root.keys and not self.root.is_leaf:
            # The root gave its last key to a merge. Its only child becomes the
            # root, and the tree shrinks one level.
            self.root = self.root.children[0]
        if found:
            self._size -= 1
        return found

    def _remove_from(self, node, key):
        """Remove key from the subtree under node. Every node we enter has at least t keys,
        so taking one key out can't leave it with too few (except the root)."""
        i = binary_search(node.keys, key)
        found_here = i < len(node.keys) and node.keys[i] == key

        if node.is_leaf:
            if found_here:
                del node.keys[i]
                del node.values[i]
            return found_here

        if found_here:
            self._remove_from_inner_node(node, i)
            return True

        # The key, if it's anywhere, is under children[i]. Make sure that child
        # has a spare key before going down into it.
        if len(node.children[i].keys) < self.t:
            i = self._give_child_a_spare_key(node, i)
        return self._remove_from(node.children[i], key)

    def _remove_from_inner_node(self, node, i):
        """Remove node.keys[i] when node is not a leaf."""
        key = node.keys[i]
        left, right = node.children[i], node.children[i + 1]
        if len(left.keys) >= self.t:
            # Replace the key with the biggest key on its left, then remove that one.
            node.keys[i], node.values[i] = self._last_item(left)
            self._remove_from(left, node.keys[i])
        elif len(right.keys) >= self.t:
            # Or with the smallest key on its right.
            node.keys[i], node.values[i] = self._first_item(right)
            self._remove_from(right, node.keys[i])
        else:
            # Both sides are at the minimum. Merge them, with the key in the middle,
            # then remove the key from the merged node.
            self._merge_children(node, i)
            self._remove_from(left, key)

    def _give_child_a_spare_key(self, node, i):
        """Make node.children[i] hold at least t keys. Returns the child's new position."""
        has_left = i > 0
        has_right = i < len(node.children) - 1
        if has_left and len(node.children[i - 1].keys) >= self.t:
            self._borrow_from_left(node, i)
            return i
        if has_right and len(node.children[i + 1].keys) >= self.t:
            self._borrow_from_right(node, i)
            return i
        if has_right:
            self._merge_children(node, i)
            return i
        self._merge_children(node, i - 1)
        return i - 1

    def _borrow_from_left(self, node, i):
        """Rotate one key from the left sibling, through the parent, into children[i]."""
        child, sibling = node.children[i], node.children[i - 1]
        child.keys.insert(0, node.keys[i - 1])
        child.values.insert(0, node.values[i - 1])
        node.keys[i - 1] = sibling.keys.pop()
        node.values[i - 1] = sibling.values.pop()
        if not sibling.is_leaf:
            child.children.insert(0, sibling.children.pop())

    def _borrow_from_right(self, node, i):
        """Rotate one key from the right sibling, through the parent, into children[i]."""
        child, sibling = node.children[i], node.children[i + 1]
        child.keys.append(node.keys[i])
        child.values.append(node.values[i])
        node.keys[i] = sibling.keys.pop(0)
        node.values[i] = sibling.values.pop(0)
        if not sibling.is_leaf:
            child.children.append(sibling.children.pop(0))

    def _merge_children(self, node, i):
        """Merge children[i], keys[i] and children[i + 1] into one node."""
        left = node.children[i]
        right = node.children.pop(i + 1)
        left.keys.append(node.keys.pop(i))
        left.values.append(node.values.pop(i))
        left.keys.extend(right.keys)
        left.values.extend(right.values)
        left.children.extend(right.children)

    @staticmethod
    def _first_item(node):
        while not node.is_leaf:
            node = node.children[0]
        return node.keys[0], node.values[0]

    @staticmethod
    def _last_item(node):
        while not node.is_leaf:
            node = node.children[-1]
        return node.keys[-1], node.values[-1]

    # ---- reading in order ----

    def items(self, low=None, high=None):
        """Yield (key, value) pairs in sorted key order, from low to high inclusive.

        Leave low or high as None for no limit on that side.
        """
        return self._walk(self.root, low, high)

    def _walk(self, node, low, high):
        # Skip keys (and the children left of them) that are below low.
        start = 0 if low is None else binary_search(node.keys, low)
        for i in range(start, len(node.keys)):
            if not node.is_leaf:
                yield from self._walk(node.children[i], low, high)
            if high is not None and node.keys[i] > high:
                return  # this key and everything after it is too big
            yield node.keys[i], node.values[i]
        if not node.is_leaf:
            yield from self._walk(node.children[-1], low, high)

    @property
    def height(self):
        """How many levels the tree has. A tree that's just a root has height 1."""
        levels, node = 1, self.root
        while not node.is_leaf:
            node = node.children[0]
            levels += 1
        return levels

    # ---- Python data model ----

    def __len__(self):
        return self._size

    def __contains__(self, key):
        return self.get(key, _NOT_FOUND) is not _NOT_FOUND

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def __repr__(self):
        return f"BTree(size={self._size}, height={self.height}, min_degree={self.t})"
