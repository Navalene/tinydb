"""HashTable: a hash map built by hand, using separate chaining.

Python's dict is already a hash table. This one is written out so you can see
how it works inside, and it's what HashIndex (index.py) stores its data in.

The idea: keep a list of "buckets". To find a key's bucket, turn the key into a
number with hash() and take the remainder after dividing by the bucket count.
Two keys can land in the same bucket (a "collision"), so each bucket is a small
list of (key, value) pairs, and we check each pair in it.
"""

_NOT_FOUND = object()  # a marker no caller can pass in by accident


class HashTable:
    """A map from keys to values. Average O(1) put, get and remove."""

    INITIAL_CAPACITY = 8
    MAX_LOAD_FACTOR = 0.75  # grow once there are more than 0.75 pairs per bucket

    def __init__(self, capacity=INITIAL_CAPACITY):
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._buckets = [[] for _ in range(capacity)]
        self._size = 0

    # ---- finding a key's bucket ----

    def _bucket_for(self, key):
        """Return the bucket (a list of pairs) that key belongs in."""
        return self._buckets[hash(key) % len(self._buckets)]

    # ---- changing data ----

    def put(self, key, value):
        """Store value under key, replacing any value already there."""
        bucket = self._bucket_for(key)
        for i, (existing_key, _) in enumerate(bucket):
            if existing_key == key:
                bucket[i] = (key, value)
                return
        bucket.append((key, value))
        self._size += 1
        if self.load_factor > self.MAX_LOAD_FACTOR:
            self._resize(len(self._buckets) * 2)

    def remove(self, key):
        """Remove key. Returns True if it was there, False otherwise."""
        bucket = self._bucket_for(key)
        for i, (existing_key, _) in enumerate(bucket):
            if existing_key == key:
                del bucket[i]
                self._size -= 1
                return True
        return False

    def _resize(self, capacity):
        """Move every pair into a new, bigger list of buckets."""
        old_pairs = list(self.items())
        self._buckets = [[] for _ in range(capacity)]
        for key, value in old_pairs:
            # The bucket count changed, so every key's bucket number changes too.
            self._bucket_for(key).append((key, value))

    # ---- reading data ----

    def get(self, key, default=None):
        """Return the value stored under key, or default if there isn't one."""
        for existing_key, value in self._bucket_for(key):
            if existing_key == key:
                return value
        return default

    def items(self):
        """Yield every (key, value) pair, in no particular order."""
        for bucket in self._buckets:
            for pair in bucket:
                yield pair

    @property
    def capacity(self):
        """How many buckets there are right now."""
        return len(self._buckets)

    @property
    def load_factor(self):
        """Average number of pairs per bucket."""
        return self._size / len(self._buckets)

    # ---- Python data model ----

    def __len__(self):
        return self._size

    def __contains__(self, key):
        return self.get(key, _NOT_FOUND) is not _NOT_FOUND

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def __repr__(self):
        return f"HashTable(size={self._size}, capacity={self.capacity})"
