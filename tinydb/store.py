"""A persistent key-value store backed by an append-only JSON-lines log.
 
Every change is written as one new line at the end of the log file, for example:
 
    {"op": "set", "key": "name", "value": "Ada"}
    {"op": "delete", "key": "name"}
 
Nothing already on disk is ever rewritten. On startup the store replays the
log from top to bottom to rebuild the current state in memory. A record only
counts once its line ends with a newline, so a line torn by a crash halfway
through a write is ignored and trimmed off.
 
Because old values pile up in the log, compact() rewrites it to hold only the
live keys. It writes a new file and atomically swaps it in with os.replace(),
so a crash during compaction leaves either the old log or the new one.
"""
 
import json
import os
import tempfile
 
SET = "set"
DELETE = "delete"
 
 
class KeyValueStore:
    """Stores string keys mapped to JSON-compatible values."""
 
    def __init__(self, path="tinydb.jsonl"):
        self.path = path
        self._data = {}
        self.log_entries = 0
        self._replay()
 
    # ---- reading the log ----
 
    def _replay(self):
        """Rebuild the in-memory state by applying every record in the log, in order."""
        self._data = {}
        self.log_entries = 0
        if not os.path.exists(self.path):
            return
 
        with open(self.path, "rb") as f:
            raw = f.read()
 
        lines = raw.split(b"\n")
        valid_bytes = 0
        for number, line in enumerate(lines, start=1):
            is_last = number == len(lines)
            if is_last:
                # Text after the final newline was never finished: a torn write.
                break
            if line.strip() == b"":
                valid_bytes += len(line) + 1
                continue
            try:
                record = json.loads(line.decode("utf-8"))
                self._apply(record)
            except (ValueError, KeyError, TypeError) as e:
                if number == len(lines) - 1:
                    # The last complete line is garbage: treat it as torn too.
                    break
                raise ValueError(f"Log file {self.path!r} is corrupted at line {number}: {e}") from e
            valid_bytes += len(line) + 1
            self.log_entries += 1
 
        if valid_bytes < len(raw):
            # Trim the torn tail so the next append starts on a clean line.
            with open(self.path, "r+b") as f:
                f.truncate(valid_bytes)
                f.flush()
                os.fsync(f.fileno())
 
    def _apply(self, record):
        """Apply one log record to the in-memory dict."""
        if not isinstance(record, dict):
            raise ValueError("record is not a JSON object")
        op, key = record["op"], record["key"]
        if not isinstance(key, str):
            raise TypeError("record key is not a string")
        if op == SET:
            self._data[key] = record["value"]
        elif op == DELETE:
            self._data.pop(key, None)
        else:
            raise ValueError(f"unknown op {op!r}")
 
    # ---- writing the log ----
 
    def _append(self, record):
        """Write one record as a new line at the end of the log and force it to disk."""
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.log_entries += 1
 
    def compact(self):
        """Rewrite the log so it holds one set record per live key, dropping old history."""
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tinydb-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for key in sorted(self._data):
                    record = {"op": SET, "key": key, "value": self._data[key]}
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        self.log_entries = len(self._data)
 
    # ---- validation ----
 
    @staticmethod
    def _check_key(key):
        if not isinstance(key, str):
            raise TypeError("Key must be a string")
        if key == "":
            raise ValueError("Key must not be empty")
 
    # ---- public API ----
 
    def set(self, key, value):
        """Create or overwrite a key."""
        self._check_key(key)
        try:
            json.dumps(value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"Value is not JSON-serializable: {e}") from e
        self._append({"op": SET, "key": key, "value": value})
        self._data[key] = value
 
    def get(self, key, default=None):
        """Return the value for key, or default if the key doesn't exist."""
        self._check_key(key)
        return self._data.get(key, default)
 
    def delete(self, key):
        """Remove a key. Returns True if it existed, False otherwise."""
        self._check_key(key)
        if key not in self._data:
            return False
        self._append({"op": DELETE, "key": key})
        del self._data[key]
        return True
 
    def exists(self, key):
        """Return True if the key is in the store."""
        self._check_key(key)
        return key in self._data
 
    def keys(self):
        """Return all keys in sorted order."""
        return sorted(self._data)
 
    def clear(self):
        """Remove every key and shrink the log to empty."""
        self._data = {}
        self.compact()
 
    def __len__(self):
        return len(self._data)
 
    def __contains__(self, key):
        return isinstance(key, str) and key in self._data
 