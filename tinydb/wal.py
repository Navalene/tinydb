"""WriteAheadLog: the Storage log plus transactions, so a group of changes is all-or-nothing.

One line in the log is already safe: a crash either finishes the line or leaves
it torn, and a torn line is ignored. But INSERT with three rows writes three
lines. If the power goes out after the second one, two rows exist and one
doesn't. A transaction fixes that by wrapping the lines:

    {"op": "begin", "tx": 4}
    {"op": "set", "key": "1", "value": {...}}
    {"op": "set", "key": "2", "value": {...}}
    {"op": "set", "key": "3", "value": {...}}
    {"op": "commit", "tx": 4}

Every change is written to the log, and forced to disk, before it changes
anything in memory. That's what "write-ahead" means. On startup, replay
recovers like this:

- A line on its own, outside begin/commit, counts as its own transaction.
  Every Phase 1-3 log looks like that, so old logs still open.
- Lines between begin and commit are held back, and only applied once the
  commit line is read.
- If the log ends before the commit, the crash hit in the middle of the
  transaction. Those lines are thrown away and trimmed off the file, as if
  the transaction never started.
"""

import json
import os

from tinydb.storage import CorruptLogError, Storage

BEGIN = "begin"
COMMIT = "commit"


class WriteAheadLog(Storage):
    """A Storage log that can also write and recover transactions."""

    def __init__(self, path):
        super().__init__(path)
        self.next_tx = 1  # number for the next transaction, so begin and commit can be matched

    def replay(self, apply):
        """Call apply(entry) on every committed entry in the log, oldest first.

        A bad line at the very end is treated as torn, like in Storage. An
        unfinished transaction at the end is rolled back (skipped and trimmed).
        A bad line anywhere earlier raises CorruptLogError.
        """
        self.entry_count = 0
        if not os.path.exists(self.path):
            return

        with open(self.path, "rb") as f:
            raw = f.read()

        lines = raw.split(b"\n")
        complete = lines[:-1]  # lines[-1] is a torn write, or b"" if there isn't one
        position = 0  # bytes read so far
        valid_bytes = 0  # bytes up to the end of the last thing that was fully applied
        valid_count = 0  # lines up to that same point
        count = 0
        pending = None  # entries of the open transaction, or None when there isn't one
        tx = None  # number of the open transaction
        for number, line in enumerate(complete, start=1):
            position += len(line) + 1
            if line.strip() == b"":
                if pending is None:
                    valid_bytes = position
                continue
            last = number == len(complete)
            try:
                entry = json.loads(line.decode("utf-8"))
                if not isinstance(entry, dict):
                    raise ValueError("entry is not a JSON object")
                op = entry.get("op")
                if op == BEGIN:
                    if pending is not None:
                        raise ValueError("begin inside another transaction")
                    tx = entry["tx"]
                    pending = []
                elif op == COMMIT:
                    if pending is None or entry["tx"] != tx:
                        raise ValueError("commit without a matching begin")
                    self._apply_committed(apply, pending, number)
                    pending = None
                elif pending is not None:
                    pending.append(entry)
                else:
                    apply(entry)
            except CorruptLogError:
                raise
            except (ValueError, KeyError, TypeError) as e:
                if last:
                    break
                raise CorruptLogError(f"Log file {self.path!r} is corrupted at line {number}: {e}") from e
            count += 1
            if isinstance(entry.get("tx"), int):
                self.next_tx = max(self.next_tx, entry["tx"] + 1)
            if pending is None:
                valid_bytes = position
                valid_count = count

        self.entry_count = valid_count
        if valid_bytes < len(raw):
            self._truncate(valid_bytes)

    def _apply_committed(self, apply, entries, number):
        """Apply a committed transaction. Its commit line is complete, so any error is corruption."""
        try:
            for entry in entries:
                apply(entry)
        except (ValueError, KeyError, TypeError) as e:
            raise CorruptLogError(f"Log file {self.path!r} has a bad transaction ending at line {number}: {e}") from e

    def append_transaction(self, entries):
        """Write entries as one all-or-nothing transaction, then force it to disk once.

        A single entry needs no begin/commit, because one line is already all-or-nothing.
        """
        entries = list(entries)
        if not entries:
            return
        if len(entries) == 1:
            self.append(entries[0])
            return
        tx = self.next_tx
        self.next_tx += 1
        lines = [{"op": BEGIN, "tx": tx}] + entries + [{"op": COMMIT, "tx": tx}]
        text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in lines)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        self.entry_count += len(lines)
