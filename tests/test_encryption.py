"""Tests for encryption at rest: encrypted logs, encrypted databases, and attacks on the files.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import json
import os
import tempfile
import unittest

from tinydb.crypto import Cipher
from tinydb.database import CATALOG, CRYPTO_FILE, Database, DatabaseError
from tinydb.storage import CorruptLogError, Storage
from tinydb.wal import WriteAheadLog

PASSPHRASE = "correct horse battery staple"
FAST = 1000  # PBKDF2 rounds for tests

SETUP = """
CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, medicaid BOOL);
INSERT INTO clients VALUES (1, 'Ada Reyes', TRUE), (2, 'Bob Okafor', FALSE), (3, 'Cy Patel', TRUE);
CREATE TABLE plans (code TEXT PRIMARY KEY, name TEXT);
INSERT INTO plans VALUES ('H1001-002', 'Sunshine Advantage');
"""


class TempFolderTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        self.dir = os.path.join(self.tmpdir, "data")

    def read(self, name):
        with open(os.path.join(self.dir, name), "rb") as f:
            return f.read()

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(data)


class EncryptedStorageTests(TempFolderTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.dir)
        self.cipher = Cipher.from_passphrase(PASSPHRASE, b"s" * 16, FAST)
        self.path = os.path.join(self.dir, "log.jsonl")

    def entries(self, storage):
        found = []
        storage.replay(found.append)
        return found

    def test_round_trip(self):
        storage = Storage(self.path, self.cipher)
        storage.append({"op": "set", "key": "1", "value": "Ada"})
        storage.append({"op": "delete", "key": "1"})
        self.assertEqual(self.entries(Storage(self.path, self.cipher)),
                         [{"op": "set", "key": "1", "value": "Ada"}, {"op": "delete", "key": "1"}])

    def test_file_holds_no_plain_text(self):
        Storage(self.path, self.cipher).append({"op": "set", "key": "1", "value": "Ada Reyes"})
        data = self.read("log.jsonl")
        self.assertNotIn(b"Ada", data)
        self.assertNotIn(b"op", data)
        self.assertEqual(data.count(b"\n"), 1)  # still one entry per line

    def test_rewrite_is_encrypted_too(self):
        storage = Storage(self.path, self.cipher)
        storage.rewrite([{"op": "set", "key": "k", "value": "secret"}])
        self.assertNotIn(b"secret", self.read("log.jsonl"))
        self.assertEqual(self.entries(Storage(self.path, self.cipher)), [{"op": "set", "key": "k", "value": "secret"}])

    def test_torn_last_line_is_still_trimmed(self):
        storage = Storage(self.path, self.cipher)
        storage.append({"op": "set", "key": "1", "value": "a"})
        good = self.read("log.jsonl")
        with open(self.path, "ab") as f:
            f.write(b"AQIDBAUGBwgJ")  # half a line, no newline: a crash mid-write
        self.assertEqual(len(self.entries(Storage(self.path, self.cipher))), 1)
        self.assertEqual(self.read("log.jsonl"), good)

    def test_wal_transaction_is_encrypted(self):
        wal = WriteAheadLog(self.path, self.cipher)
        wal.append_transaction([{"op": "set", "key": "1", "value": "a"}, {"op": "set", "key": "2", "value": "b"}])
        self.assertNotIn(b"begin", self.read("log.jsonl"))
        self.assertEqual(len(self.entries(WriteAheadLog(self.path, self.cipher))), 2)

    def test_unfinished_encrypted_transaction_is_rolled_back(self):
        wal = WriteAheadLog(self.path, self.cipher)
        wal.append({"op": "set", "key": "0", "value": "kept"})
        kept = self.read("log.jsonl")
        with open(self.path, "a", encoding="utf-8") as f:  # begin and one entry, but the commit never arrives
            f.write(wal.encode({"op": "begin", "tx": 9}) + "\n")
            f.write(wal.encode({"op": "set", "key": "1", "value": "lost"}) + "\n")
        self.assertEqual(self.entries(WriteAheadLog(self.path, self.cipher)), [{"op": "set", "key": "0", "value": "kept"}])
        self.assertEqual(self.read("log.jsonl"), kept)

    def test_plain_storage_is_unchanged(self):
        Storage(self.path).append({"op": "set", "key": "1", "value": "Ada"})
        self.assertEqual(json.loads(self.read("log.jsonl")), {"op": "set", "key": "1", "value": "Ada"})


class EncryptedFolderTestCase(TempFolderTestCase):
    """An encrypted database with two tables, made fresh for each test."""

    def open(self, passphrase=PASSPHRASE):
        return Database(self.dir, passphrase=passphrase, iterations=FAST)

    def setUp(self):
        super().setUp()
        self.db = self.open()
        self.db.run(SETUP)


class EncryptedDatabaseTests(EncryptedFolderTestCase):
    def names(self, db):
        return [row[0] for row in db.run("SELECT name FROM clients")[0].rows]

    def test_reopen_with_the_passphrase(self):
        reopened = self.open()
        self.assertTrue(reopened.encrypted)
        self.assertEqual(self.names(reopened), ["Ada Reyes", "Bob Okafor", "Cy Patel"])
        self.assertEqual(reopened.table_names(), ["clients", "plans"])

    def test_no_file_holds_plain_text(self):
        for name in os.listdir(self.dir):
            data = self.read(name)
            for secret in (b"Ada", b"Okafor", b"medicaid", b"Sunshine", b"H1001", b"PRIMARY"):
                self.assertNotIn(secret, data, f"{secret!r} found in {name}")

    def test_crypto_file_holds_no_secret(self):
        settings = json.loads(self.read(CRYPTO_FILE))
        self.assertEqual(sorted(settings), ["check", "iterations", "kdf", "salt"])
        self.assertNotIn(PASSPHRASE, json.dumps(settings))
        self.assertEqual(settings["iterations"], FAST)

    def test_wrong_passphrase_is_refused_and_changes_nothing(self):
        before = {name: self.read(name) for name in os.listdir(self.dir)}
        with self.assertRaises(DatabaseError):
            self.open("not the right passphrase")
        self.assertEqual({name: self.read(name) for name in os.listdir(self.dir)}, before)

    def test_missing_passphrase_is_refused(self):
        with self.assertRaises(DatabaseError):
            Database(self.dir)

    def test_writes_after_reopening_work(self):
        reopened = self.open()
        reopened.run("UPDATE clients SET name = 'Ada R.' WHERE id = 1; DELETE FROM clients WHERE id = 3")
        self.assertEqual(self.names(self.open()), ["Ada R.", "Bob Okafor"])

    def test_indexes_work_encrypted(self):
        self.db.run("CREATE INDEX ON clients (name)")
        result = self.open().run("SELECT id FROM clients WHERE name = 'Bob Okafor'")[0]
        self.assertEqual((result.rows, result.plan), (((2,),), "btree index on name"))


class FileAttackTests(EncryptedFolderTestCase):
    """Someone has the files but not the passphrase."""

    def lines(self, name):
        return self.read(name).split(b"\n")[:-1]

    def test_changed_line_is_caught(self):
        lines = self.lines("clients.jsonl")
        lines[0] = lines[0][:20] + (b"A" if lines[0][20:21] != b"A" else b"B") + lines[0][21:]
        self.write("clients.jsonl", b"\n".join(lines) + b"\n")
        with self.assertRaises(CorruptLogError):
            self.open()

    def test_line_moved_to_another_table_is_caught(self):
        # Copy a plans line into the clients file: it decrypts fine on its own, but it was sealed as plans.jsonl.
        clients, plans = self.lines("clients.jsonl"), self.lines("plans.jsonl")
        self.write("clients.jsonl", b"\n".join([plans[0]] + clients) + b"\n")
        with self.assertRaises(CorruptLogError):
            self.open()

    def test_changed_last_line_looks_like_a_torn_write(self):
        # A limit worth knowing: a bad LAST line is treated as a crash mid-write and trimmed,
        # so someone who can edit the files can make the newest change disappear.
        lines = self.lines("plans.jsonl")
        self.assertEqual(len(lines), 1)
        self.write("plans.jsonl", lines[0][:-5] + b"AAAAA\n")
        self.assertEqual(self.open().run("SELECT * FROM plans")[0].rows, ())

    def test_deleting_the_crypto_file_does_not_unlock_anything(self):
        os.remove(os.path.join(self.dir, CRYPTO_FILE))
        with self.assertRaises(DatabaseError):
            self.open()  # the passphrase would make a new salt, and the old catalog is in the way
        with self.assertRaises((CorruptLogError, ValueError, KeyError)):
            Database(self.dir)  # and without encryption the lines aren't JSON


class MixingTests(TempFolderTestCase):
    def test_passphrase_on_a_plain_folder_is_refused(self):
        Database(self.dir).run("CREATE TABLE t (id INT PRIMARY KEY); INSERT INTO t VALUES (1)")
        before = self.read(CATALOG)
        with self.assertRaises(DatabaseError):
            Database(self.dir, passphrase=PASSPHRASE, iterations=FAST)
        self.assertEqual(self.read(CATALOG), before)
        self.assertFalse(os.path.exists(os.path.join(self.dir, CRYPTO_FILE)))

    def test_plain_folder_still_opens_without_a_passphrase(self):
        Database(self.dir).run("CREATE TABLE t (id INT PRIMARY KEY)")
        self.assertFalse(Database(self.dir).encrypted)

    def test_short_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            Database(self.dir, passphrase="short", iterations=FAST)


if __name__ == "__main__":
    unittest.main()
