"""Table: a named collection of Records, saved through a Storage log.

The Table keeps every live Record in a dict in memory, so reads by key are O(1).
Every change is first appended to the Storage log, then applied in memory.
On startup the Table asks Storage to replay the log and rebuilds the dict.

Phase 3 adds indexes on fields inside the values, and three ways to search:
find (field equals a value), find_range (field between two values) and
sort_by. Each one uses an index when there is one that helps, and falls back
to checking every record when there isn't. last_plan says which happened.

Phase 4 opens tables with a WriteAheadLog instead of a plain Storage, and adds
set_many and delete_many, which change many records in one transaction.
"""

import os

from tinydb.algorithms import merge_sort
from tinydb.index import MISSING, BTreeIndex, HashIndex, field_value, index_key
from tinydb.record import Record
from tinydb.wal import WriteAheadLog

SET = "set"
DELETE = "delete"
INDEX = "index"
DROP_INDEX = "drop_index"

INDEX_KINDS = {"hash": HashIndex, "btree": BTreeIndex}


class Table:
    """A set of Records keyed by Record.key."""

    def __init__(self, name, storage):
        self.name = name
        self.storage = storage
        self._records = {}
        self._indexes = {}  # field name -> HashIndex or BTreeIndex
        self.last_plan = ""  # how the last search ran, e.g. "hash index on plan"
        self.storage.replay(self._apply)

    @classmethod
    def open(cls, path):
        """Open the table stored at path. The file name becomes the table name."""
        name = os.path.splitext(os.path.basename(path))[0]
        return cls(name, WriteAheadLog(path))

    # ---- replaying the log ----

    def _apply(self, entry):
        """Apply one log entry to the in-memory records. Raises if the entry is invalid."""
        if not isinstance(entry, dict):
            raise ValueError("entry is not a JSON object")
        op = entry["op"]
        if op == SET:
            self._put(Record.from_dict(entry))
        elif op == DELETE:
            key = entry["key"]
            Record.check_key(key)
            self._drop(key)
        elif op == INDEX:
            self._build_index(entry["field"], entry["kind"])
        elif op == DROP_INDEX:
            field = entry["field"]
            self._check_field(field)
            self._indexes.pop(field, None)
        else:
            raise ValueError(f"unknown op {op!r}")

    def _put(self, record):
        """Store a record in memory and keep every index up to date."""
        old = self._records.get(record.key)
        for index in self._indexes.values():
            if old is not None:
                index.remove(old)
            index.add(record)
        self._records[record.key] = record

    def _drop(self, key):
        """Remove a record from memory and from every index."""
        old = self._records.pop(key, None)
        if old is not None:
            for index in self._indexes.values():
                index.remove(old)

    # ---- changing data ----

    def set(self, key, value):
        """Create or overwrite a record. Returns the new Record."""
        record = Record(key, value)
        self.storage.append({"op": SET, **record.to_dict()})
        self._put(record)
        return record

    def set_many(self, pairs):
        """Create or overwrite many records in one all-or-nothing transaction.

        pairs is a list of (key, value). Every record is checked before anything
        is written, so one bad value means nothing changes. Needs a WriteAheadLog.
        """
        records = [Record(key, value) for key, value in pairs]
        self.storage.append_transaction([{"op": SET, **record.to_dict()} for record in records])
        for record in records:
            self._put(record)
        return records

    def delete_many(self, keys):
        """Remove many records in one transaction. Returns how many existed."""
        for key in keys:
            Record.check_key(key)
        found = [key for key in dict.fromkeys(keys) if key in self._records]
        self.storage.append_transaction([{"op": DELETE, "key": key} for key in found])
        for key in found:
            self._drop(key)
        return len(found)

    def delete(self, key):
        """Remove a record. Returns True if it existed, False otherwise."""
        Record.check_key(key)
        if key not in self._records:
            return False
        self.storage.append({"op": DELETE, "key": key})
        self._drop(key)
        return True

    def clear(self):
        """Remove every record and shrink the log. Indexes stay, but empty."""
        self._records = {}
        self._indexes = {field: INDEX_KINDS[kind](field) for field, kind in self.indexes()}
        self.compact()

    def compact(self):
        """Rewrite the log so it holds one entry per index and one per live record."""
        self.storage.rewrite(self._snapshot())

    def _snapshot(self):
        for field, kind in self.indexes():
            yield {"op": INDEX, "field": field, "kind": kind}
        for record in self:
            yield {"op": SET, **record.to_dict()}

    # ---- indexes ----

    def create_index(self, field, kind="hash"):
        """Index a field. Returns False if that exact index already exists.

        kind is "hash" (equality only) or "btree" (equality, ranges and sorting).
        Asking for a different kind on an indexed field replaces the old index.
        """
        self._check_field(field)
        if kind not in INDEX_KINDS:
            raise ValueError(f"Unknown index kind: {kind}. Use hash or btree")
        current = self._indexes.get(field)
        if current is not None and current.kind == kind:
            return False
        self.storage.append({"op": INDEX, "field": field, "kind": kind})
        self._build_index(field, kind)
        return True

    def drop_index(self, field):
        """Remove the index on a field. Returns True if there was one."""
        self._check_field(field)
        if field not in self._indexes:
            return False
        self.storage.append({"op": DROP_INDEX, "field": field})
        del self._indexes[field]
        return True

    def indexes(self):
        """Return (field, kind) for every index, sorted by field."""
        return [(field, self._indexes[field].kind) for field in sorted(self._indexes)]

    def _build_index(self, field, kind):
        """Make a new index and fill it from every record. O(n)."""
        self._check_field(field)
        if kind not in INDEX_KINDS:
            raise ValueError(f"unknown index kind {kind!r}")
        index = INDEX_KINDS[kind](field)
        for record in self._records.values():
            index.add(record)
        self._indexes[field] = index

    @staticmethod
    def _check_field(field):
        if not isinstance(field, str) or field == "":
            raise ValueError("Field must be a non-empty string")

    # ---- searching ----

    def find(self, field, value):
        """Return records whose value has field equal to value, sorted by key."""
        self._check_field(field)
        index = self._indexes.get(field)
        if index is not None:
            self.last_plan = f"{index.kind} index on {field}"
            return [self._records[key] for key in index.find(value)]
        self.last_plan = "full scan"
        target = index_key(value)
        return [record for record in self if self._key_of(record, field) == target]

    def find_range(self, field, low=None, high=None):
        """Return records whose field is between low and high (inclusive), in field order.

        Leave low or high as None for no limit on that side.
        """
        self._check_field(field)
        index = self._indexes.get(field)
        if isinstance(index, BTreeIndex):
            self.last_plan = f"btree index on {field}"
            return [self._records[key] for key in index.range(low, high)]
        # A hash index scatters values in no order, so it can't help with a range.
        self.last_plan = "full scan"
        low_key = None if low is None else index_key(low)
        high_key = None if high is None else index_key(high)
        matches = []
        for record in self:
            value = field_value(record, field)
            if value is MISSING:
                continue
            key = index_key(value)
            if low_key is not None and key < low_key:
                continue
            if high_key is not None and key > high_key:
                continue
            matches.append(record)
        return merge_sort(matches, key=lambda record: self._key_of(record, field))

    def sort_by(self, field, reverse=False):
        """Return every record that has field, sorted by it. Ties are sorted by key."""
        self._check_field(field)
        index = self._indexes.get(field)
        if isinstance(index, BTreeIndex):
            self.last_plan = f"btree index on {field}"
            return [self._records[key] for key in index.ordered(reverse)]
        self.last_plan = "full scan + merge sort"
        having = [record for record in self if self._key_of(record, field) is not MISSING]
        return merge_sort(having, key=lambda record: self._key_of(record, field), reverse=reverse)

    @staticmethod
    def _key_of(record, field):
        """index_key of the record's field value, or MISSING if it has no such field."""
        value = field_value(record, field)
        return MISSING if value is MISSING else index_key(value)

    # ---- reading data ----

    def get(self, key):
        """Return the Record for key, or None if there isn't one."""
        Record.check_key(key)
        return self._records.get(key)

    def exists(self, key):
        """Return True if a record with this key exists."""
        Record.check_key(key)
        return key in self._records

    def keys(self):
        """Return every key in sorted order."""
        return sorted(self._records)

    def records(self):
        """Return every Record, sorted by key."""
        return [self._records[key] for key in self.keys()]

    @property
    def log_entries(self):
        """How many lines the log file holds right now."""
        return self.storage.entry_count

    # ---- Python data model ----

    def __len__(self):
        return len(self._records)

    def __contains__(self, key):
        return isinstance(key, str) and key in self._records

    def __iter__(self):
        return iter(self.records())

    def __repr__(self):
        return f"Table(name={self.name!r}, records={len(self)})"
