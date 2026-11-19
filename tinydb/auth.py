"""API keys: who may use the HTTP API, and what they may do.

A key is a long random string, like tdb_Xy3k...  The server never stores the
key itself. It stores a SHA-256 hash of it, so someone who copies the keys file
still can't use any key. When a request arrives, the server hashes the key the
client sent and looks for a matching hash.

Each key has a role:

    read    QUERY /query only (SELECT, SHOW TABLES, DESCRIBE)
    write   QUERY /query and POST /execute

The keys file is JSON, by default tinydb_data/_keys.json:

    [{"id": "3f9a1c2e", "name": "dashboard", "role": "read",
      "hash": "9b74c9...", "created": "2026-11-19"}]

Manage keys from Terminal:

    python3 main.py apikey create dashboard --role read
    python3 main.py apikey list
    python3 main.py apikey revoke 3f9a1c2e

(Add --dir right after main.py for a folder other than tinydb_data.)
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass

KEY_PREFIX = "tdb_"
KEYS_FILE = "_keys.json"
READ = "read"
WRITE = "write"
ROLES = (READ, WRITE)


class KeyStoreError(ValueError):
    """Raised for a bad key command, like an unknown role."""


@dataclass(frozen=True)
class ApiKey:
    """One key as it's stored: everything except the key itself."""

    id: str  # short and not secret, so you can name a key in logs and when revoking it
    name: str  # who or what the key is for, like "dashboard"
    role: str  # READ or WRITE
    hash: str  # SHA-256 of the key, as hex
    created: str  # date, YYYY-MM-DD

    def can_write(self):
        return self.role == WRITE


def generate_key():
    """A new random key. 32 random bytes is 256 bits, far too many to guess."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key):
    """The SHA-256 hash of a key, as 64 hex characters."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class KeyStore:
    """The keys file. Create, list, revoke and check keys."""

    def __init__(self, path):
        self.path = path
        self._keys = []
        self._seen = None  # (modified time, size) of the file when it was last read
        self._lock = threading.Lock()  # the server checks keys from many threads
        self._reload_if_changed()

    @classmethod
    def in_folder(cls, directory):
        """The KeyStore that lives in a database folder."""
        return cls(os.path.join(directory, KEYS_FILE))

    # ---- reading and writing the file ----

    def _reload_if_changed(self):
        """Read the file again if it changed, so a key revoked in Terminal stops working right away."""
        try:
            info = os.stat(self.path)
        except FileNotFoundError:
            self._keys, self._seen = [], None
            return
        seen = (info.st_mtime_ns, info.st_size)
        if seen == self._seen:
            return
        with open(self.path, encoding="utf-8") as f:
            self._keys = [ApiKey(**entry) for entry in json.load(f)]
        self._seen = seen

    def _save(self):
        """Replace the file atomically, readable only by you (like Storage.rewrite)."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tinydb-", suffix=".tmp")  # mode 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([asdict(key) for key in self._keys], f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        info = os.stat(self.path)
        self._seen = (info.st_mtime_ns, info.st_size)

    # ---- managing keys ----

    def create(self, name, role):
        """Make a new key. Returns (ApiKey, the key itself). The key is shown once and never stored."""
        if role not in ROLES:
            raise KeyStoreError(f"Role must be {' or '.join(ROLES)}, not {role!r}")
        name = name.strip()
        if not name:
            raise KeyStoreError("Give the key a name, like dashboard")
        with self._lock:
            self._reload_if_changed()
            key = generate_key()
            used = {k.id for k in self._keys}
            key_id = secrets.token_hex(4)
            while key_id in used:
                key_id = secrets.token_hex(4)
            api_key = ApiKey(key_id, name, role, hash_key(key), datetime.date.today().isoformat())
            self._keys.append(api_key)
            self._save()
        return api_key, key

    def revoke(self, key_id):
        """Delete a key so it stops working. Returns False if there's no key with that id."""
        with self._lock:
            self._reload_if_changed()
            kept = [k for k in self._keys if k.id != key_id]
            if len(kept) == len(self._keys):
                return False
            self._keys = kept
            self._save()
        return True

    def keys(self):
        with self._lock:
            self._reload_if_changed()
            return list(self._keys)

    def __len__(self):
        return len(self.keys())

    # ---- checking a key ----

    def check(self, presented):
        """Return the ApiKey that matches the key a client sent, or None.

        Every stored hash is compared, with hmac.compare_digest, even after a
        match is found. So the time this takes doesn't depend on which key
        matched or how much of a hash was right.
        """
        presented_hash = hash_key(presented)
        found = None
        with self._lock:
            self._reload_if_changed()
            for key in self._keys:
                if hmac.compare_digest(key.hash, presented_hash):
                    found = key
        return found


def main(argv=None, out=None):
    out = out or sys.stdout
    arg_parser = argparse.ArgumentParser(prog="tinydb apikey", description="Manage tinydb API keys.")
    arg_parser.add_argument("--dir", default="tinydb_data", help="database folder (default: tinydb_data)")
    commands = arg_parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="make a new key")
    create.add_argument("name", help="who or what the key is for, like dashboard")
    create.add_argument("--role", choices=ROLES, default=READ, help="read (default) or write")
    commands.add_parser("list", help="list keys (never the keys themselves)")
    revoke = commands.add_parser("revoke", help="delete a key so it stops working")
    revoke.add_argument("id", help="the key's id, from list")
    options = arg_parser.parse_args(argv)

    store = KeyStore.in_folder(options.dir)
    if options.command == "create":
        api_key, key = store.create(options.name, options.role)
        print(f"Created {api_key.role} key {api_key.id} for {api_key.name}:", file=out)
        print(f"\n    {key}\n", file=out)
        print("Copy it now. It's stored only as a hash, so it can't be shown again.", file=out)
        return 0
    if options.command == "list":
        keys = store.keys()
        if not keys:
            print("(no keys)", file=out)
        for k in keys:
            print(f"{k.id}  {k.role:<5}  {k.created}  {k.name}", file=out)
        return 0
    if store.revoke(options.id):
        print(f"Revoked key {options.id}", file=out)
        return 0
    print(f"No key with id {options.id}", file=out)
    return 1


if __name__ == "__main__":
    sys.exit(main())
