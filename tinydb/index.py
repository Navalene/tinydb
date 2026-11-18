"""Indexes: find records by a field inside their value without checking every record.

When values are JSON objects like {"name": "Ada", "plan": "PPO", "age": 70},
you can index one field, such as "plan". The index maps each value of that
field to the set of record keys that have it:

    "HMO" -> {"client2"}
    "PPO" -> {"client1", "client3"}

Two kinds of index, with the same methods:
    HashIndex   stores that map in a HashTable. Fastest for "plan equals PPO".
    BTreeIndex  stores it in a BTree, so it can also answer "age between 65 and 70"
                and list records sorted by the field.
"""

from typing import Any, Tuple

from tinydb.btree import BTree
from tinydb.hashtable import HashTable

MISSING = object()  # stands for "this record has no such field"


def field_value(record, field):
    """Return the record's value for field, or MISSING if it doesn't have one."""
    value = record.value
    if isinstance(value, dict) and field in value:
        return value[field]
    return MISSING


def index_key(value: Any) -> Tuple[Any, ...]:
    """Turn any JSON value into a key that can be hashed and compared with any other.

    Python refuses to compare 70 < "seventy", so each type gets a rank first:
    null, then true/false, then numbers, then strings, then lists, then objects.
    A key is a tuple of (rank, value), and tuples compare one item at a time.
    """
    if value is None:
        return (0,)
    if isinstance(value, bool):  # checked before numbers, because True is also an int
        return (1, value)
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    if isinstance(value, list):
        return (4, tuple(index_key(item) for item in value))
    if isinstance(value, dict):
        return (5, tuple(sorted((name, index_key(item)) for name, item in value.items())))
    raise TypeError(f"Not a JSON value: {value!r}")


class Index:
    """What both kinds of index share. Use HashIndex or BTreeIndex, not this."""

    kind = "?"

    def __init__(self, field, buckets):
        self.field = field
        # buckets maps index_key(field value) -> set of record keys.
        # It's a HashTable or a BTree. Both have get, put and remove.
        self._buckets = buckets

    def add(self, record):
        """Add a record to the index. Records without the field are skipped."""
        value = field_value(record, self.field)
        if value is MISSING:
            return
        bucket_key = index_key(value)
        keys = self._buckets.get(bucket_key)
        if keys is None:
            keys = set()
            self._buckets.put(bucket_key, keys)
        keys.add(record.key)

    def remove(self, record):
        """Take a record out of the index."""
        value = field_value(record, self.field)
        if value is MISSING:
            return
        bucket_key = index_key(value)
        keys = self._buckets.get(bucket_key)
        if keys is None:
            return
        keys.discard(record.key)
        if not keys:
            self._buckets.remove(bucket_key)  # no empty sets left lying around

    def find(self, value):
        """Return the keys of records whose field equals value, sorted."""
        keys = self._buckets.get(index_key(value))
        return sorted(keys) if keys else []

    def __len__(self):
        """How many different values the field has."""
        return len(self._buckets)

    def __repr__(self):
        return f"{type(self).__name__}(field={self.field!r}, values={len(self)})"


class HashIndex(Index):
    """An index for equality lookups: average O(1) to find a value."""

    kind = "hash"

    def __init__(self, field):
        super().__init__(field, HashTable())


class BTreeIndex(Index):
    """An index kept in sorted order: O(log n) lookups, plus ranges and sorting."""

    kind = "btree"

    def __init__(self, field, min_degree=16):
        self._tree = BTree(min_degree)
        super().__init__(field, self._tree)

    def range(self, low=None, high=None):
        """Return keys of records whose field is between low and high (inclusive).

        Results are in field order, and records that tie are sorted by key.
        Leave low or high as None for no limit on that side.
        """
        low_key = None if low is None else index_key(low)
        high_key = None if high is None else index_key(high)
        result = []
        for _, keys in self._tree.items(low_key, high_key):
            result.extend(sorted(keys))
        return result

    def ordered(self, reverse=False):
        """Return keys of every record that has the field, sorted by the field."""
        groups = [sorted(keys) for _, keys in self._tree.items()]
        if reverse:
            groups.reverse()  # biggest value first, but ties stay sorted by key
        return [key for group in groups for key in group]
