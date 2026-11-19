"""Storage: the append-only JSON-lines log file on disk.

Storage only knows how to read and write lines of JSON. It has no idea what a
key, a value, or a "set" is. That's the Table's job. Keeping the two apart
means Phase 4's write-ahead log can replace or wrap this class without
touching Table.

Every change is one line at the end of the file, for example:

    {"op": "set", "key": "name", "value": "Ada"}
    {"op": "delete", "key": "name"}

A line only counts once it ends with a newline, so a line torn by a crash
halfway through a write is ignored and trimmed off.

Phase 6 adds encryption at rest. Give Storage a Cipher and every line is
encrypted before it's written and decrypted as it's read, so the file holds
lines like AWm3k...Qs== instead of JSON. Nothing else changes: a line that
won't decrypt is handled exactly like a line that isn't valid JSON.
"""

import json
import os
import tempfile


class CorruptLogError(ValueError):
    """Raised when a line in the middle of the log can't be read."""


class Storage:
    """Reads and writes one append-only log file."""

    def __init__(self, path, cipher=None):
        self.path = path
        self.entry_count = 0
        self.cipher = cipher  # a tinydb.crypto.Cipher, or None to write plain JSON
        # Each line is sealed with its file's name, so a line copied into another file won't decrypt.
        self._associated = os.path.basename(path).encode("utf-8")

    def encode(self, entry):
        """Turn an entry into one line of text, without the newline. Encrypted if there's a cipher."""
        text = json.dumps(entry, ensure_ascii=False)
        if self.cipher is None:
            return text
        return self.cipher.encrypt_text(text, self._associated)

    def decode(self, line):
        """Turn one line (bytes) back into an entry. Raises ValueError if it can't."""
        text = line.decode("utf-8")
        if self.cipher is not None:
            text = self.cipher.decrypt_text(text.strip(), self._associated)  # DecryptionError is a ValueError
        return json.loads(text)

    def replay(self, apply):
        """Call apply(entry) on every complete entry in the log, oldest first.

        If apply raises on the last complete line, that line is treated as torn.
        If it raises anywhere earlier, the log is corrupted and CorruptLogError is raised.
        """
        self.entry_count = 0
        if not os.path.exists(self.path):
            return

        with open(self.path, "rb") as f:
            raw = f.read()

        # Everything after the final newline is lines[-1]: a torn write, or b"" if none.
        lines = raw.split(b"\n")
        complete = lines[:-1]
        valid_bytes = 0
        for number, line in enumerate(complete, start=1):
            if line.strip() == b"":
                valid_bytes += len(line) + 1
                continue
            try:
                apply(self.decode(line))
            except (ValueError, KeyError, TypeError) as e:
                if number == len(complete):
                    break
                raise CorruptLogError(f"Log file {self.path!r} is corrupted at line {number}: {e}") from e
            valid_bytes += len(line) + 1
            self.entry_count += 1

        if valid_bytes < len(raw):
            self._truncate(valid_bytes)

    def _truncate(self, size):
        """Cut the file down to size bytes so the next append starts on a clean line."""
        with open(self.path, "r+b") as f:
            f.truncate(size)
            f.flush()
            os.fsync(f.fileno())

    def append(self, entry):
        """Write one entry as a new line at the end of the log and force it to disk."""
        line = self.encode(entry) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        self.entry_count += 1

    def rewrite(self, entries):
        """Atomically replace the whole log with entries (used by compaction)."""
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tinydb-", suffix=".tmp")
        count = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(self.encode(entry) + "\n")
                    count += 1
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        self.entry_count = count
