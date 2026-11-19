"""Unit tests for API keys: making, storing, checking and revoking them.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import io
import json
import os
import stat
import tempfile
import unittest

from tinydb.auth import KEY_PREFIX, KEYS_FILE, KeyStore, KeyStoreError, generate_key, hash_key, main


class KeyTests(unittest.TestCase):
    def test_key_format(self):
        key = generate_key()
        self.assertTrue(key.startswith(KEY_PREFIX))
        self.assertGreaterEqual(len(key), len(KEY_PREFIX) + 43)  # 32 bytes in base64 is 43 characters

    def test_keys_are_random(self):
        self.assertEqual(len({generate_key() for _ in range(100)}), 100)

    def test_hash_is_sha256_hex(self):
        self.assertEqual(hash_key("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


class KeyStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.dir = os.path.join(self.tmpdir.name, "data")
        self.store = KeyStore.in_folder(self.dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def path(self):
        return os.path.join(self.dir, KEYS_FILE)


class KeyStoreTests(KeyStoreTestCase):
    def test_starts_empty_without_a_file(self):
        self.assertEqual(len(self.store), 0)
        self.assertFalse(os.path.exists(self.path()))

    def test_create_and_check(self):
        api_key, key = self.store.create("dashboard", "read")
        self.assertEqual(self.store.check(key), api_key)
        self.assertEqual((api_key.name, api_key.role), ("dashboard", "read"))
        self.assertFalse(api_key.can_write())

    def test_write_role(self):
        api_key, _ = self.store.create("intake", "write")
        self.assertTrue(api_key.can_write())

    def test_wrong_key(self):
        self.store.create("dashboard", "read")
        self.assertIsNone(self.store.check(generate_key()))
        self.assertIsNone(self.store.check(""))

    def test_check_finds_the_right_one_of_many(self):
        made = [self.store.create(f"app{i}", "read") for i in range(5)]
        for api_key, key in made:
            self.assertEqual(self.store.check(key), api_key)

    def test_ids_are_unique(self):
        ids = [self.store.create(f"app{i}", "read")[0].id for i in range(20)]
        self.assertEqual(len(set(ids)), 20)

    def test_bad_role(self):
        with self.assertRaises(KeyStoreError):
            self.store.create("dashboard", "admin")

    def test_needs_a_name(self):
        with self.assertRaises(KeyStoreError):
            self.store.create("   ", "read")

    def test_revoke(self):
        api_key, key = self.store.create("dashboard", "read")
        self.assertTrue(self.store.revoke(api_key.id))
        self.assertIsNone(self.store.check(key))
        self.assertFalse(self.store.revoke(api_key.id))


class KeyFileTests(KeyStoreTestCase):
    def test_file_never_holds_the_key(self):
        api_key, key = self.store.create("dashboard", "read")
        with open(self.path(), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(key, text)
        self.assertIn(api_key.hash, text)
        self.assertEqual(json.loads(text)[0]["role"], "read")

    def test_file_is_private(self):
        self.store.create("dashboard", "read")
        mode = stat.S_IMODE(os.stat(self.path()).st_mode)
        self.assertEqual(mode, 0o600)  # only you can read or write it

    def test_keys_survive_a_restart(self):
        api_key, key = self.store.create("dashboard", "write")
        self.assertEqual(KeyStore.in_folder(self.dir).check(key), api_key)

    def test_sees_changes_made_by_another_process(self):
        _, key = self.store.create("dashboard", "read")
        other = KeyStore.in_folder(self.dir)
        new_id = other.create("report", "read")[0].id
        self.assertIn(new_id, [k.id for k in self.store.keys()])
        other.revoke(self.store.check(key).id)  # type: ignore
        self.assertIsNone(self.store.check(key))


class CommandTests(KeyStoreTestCase):
    def run_main(self, *args):
        out = io.StringIO()
        code = main(["--dir", self.dir] + list(args), out)
        return code, out.getvalue()

    def test_create_prints_the_key_once(self):
        code, text = self.run_main("create", "dashboard", "--role", "read")
        self.assertEqual(code, 0)
        key = [word for word in text.split() if word.startswith(KEY_PREFIX)][0]
        self.assertIsNotNone(self.store.check(key))

    def test_list_never_shows_keys_or_hashes(self):
        api_key, key = self.store.create("dashboard", "read")
        code, text = self.run_main("list")
        self.assertEqual(code, 0)
        self.assertIn(api_key.id, text)
        self.assertIn("dashboard", text)
        self.assertNotIn(key, text)
        self.assertNotIn(api_key.hash, text)

    def test_list_empty(self):
        self.assertEqual(self.run_main("list"), (0, "(no keys)\n"))

    def test_revoke(self):
        api_key, _ = self.store.create("dashboard", "read")
        self.assertEqual(self.run_main("revoke", api_key.id)[0], 0)
        self.assertEqual(self.run_main("revoke", api_key.id)[0], 1)


if __name__ == "__main__":
    unittest.main()
