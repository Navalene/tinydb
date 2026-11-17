"""Table: a named collection of Records, saved through a Storage log.

The Table keeps every live Record in a dict in memory, so reads are O(1).
Every change is first appended to the Storage log, then applied in memory.
On startup the Table asks Storage to replay the log and rebuilds the dict.
"""

import os

from tinydb.record import Record
from tinydb.storage import Storage

SET = "set"
DELETE = "delete"


class Table:
    """A set of Records keyed by Record.key."""

    def __init__(self, name, storage):
        self.name = name
        self.storage = storage
        self._records = {}
        self.storage.replay(self._apply)

    @classmethod
    def open(cls, path):
        """Open the table stored at path. The file name becomes the table name."""
        name = os.path.splitext(os.path.basename(path))[0]
        return cls(name, Storage(path))

    # ---- replaying the log ----

    def _apply(self, entry):
        """Apply one log entry to the in-memory records. Raises if the entry is invalid."""
        if not isinstance(entry, dict):
            raise ValueError("entry is not a JSON object")
        op = entry["op"]
        if op == SET:
            record = Record.from_dict(entry)
            self._records[record.key] = record
        elif op == DELETE:
            key = entry["key"]
            Record.check_key(key)
            self._records.pop(key, None)
        else:
            raise ValueError(f"unknown op {op!r}")

    # ---- changing data ----

    def set(self, key, value):
        """Create or overwrite a record. Returns the new Record."""
        record = Record(key, value)
        self.storage.append({"op": SET, **record.to_dict()})
        self._records[key] = record
        return record

    def delete(self, key):
        """Remove a record. Returns True if it existed, False otherwise."""
        Record.check_key(key)
        if key not in self._records:
            return False
        self.storage.append({"op": DELETE, "key": key})
        del self._records[key]
        return True

    def clear(self):
        """Remove every record and shrink the log to empty."""
        self._records = {}
        self.compact()

    def compact(self):
        """Rewrite the log so it holds one set entry per live record."""
        self.storage.rewrite({"op": SET, **record.to_dict()} for record in self)

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
