"""Unit tests for tinydb.crypto: encryption at rest from the standard library.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import hashlib
import hmac
import unittest

from tinydb.crypto import (
    NONCE_SIZE,
    TAG_SIZE,
    VERSION,
    Cipher,
    DecryptionError,
    derive_keys,
    keystream,
    make_salt,
    xor,
)

PASSPHRASE = "correct horse battery staple"
FAST = 1000  # PBKDF2 rounds for tests. The real default is much slower on purpose.


def make_cipher(passphrase=PASSPHRASE, salt=b"s" * 16):
    return Cipher.from_passphrase(passphrase, salt, iterations=FAST)


class KeyTests(unittest.TestCase):
    def test_pbkdf2_matches_the_published_test_vector(self):
        # From RFC 7914 section 11: PBKDF2-HMAC-SHA256, "passwd", "salt", 1 round, first 32 bytes.
        expected = bytes.fromhex("55ac046e56e3089fec1691c22544b605f94185216dde0465e68b9d57c20dacbc")
        self.assertEqual(hashlib.pbkdf2_hmac("sha256", b"passwd", b"salt", 1, dklen=32), expected)

    def test_same_passphrase_and_salt_give_the_same_keys(self):
        salt = make_salt()
        self.assertEqual(derive_keys(PASSPHRASE, salt, FAST), derive_keys(PASSPHRASE, salt, FAST))

    def test_different_salt_gives_different_keys(self):
        self.assertNotEqual(derive_keys(PASSPHRASE, make_salt(), FAST), derive_keys(PASSPHRASE, make_salt(), FAST))

    def test_the_two_keys_are_different(self):
        encryption_key, mac_key = derive_keys(PASSPHRASE, make_salt(), FAST)
        self.assertEqual((len(encryption_key), len(mac_key)), (32, 32))
        self.assertNotEqual(encryption_key, mac_key)

    def test_short_passphrase_is_refused(self):
        with self.assertRaises(ValueError):
            derive_keys("hunter2", make_salt(), FAST)

    def test_salts_are_random(self):
        self.assertEqual(len({make_salt() for _ in range(50)}), 50)

    def test_keys_must_be_32_bytes(self):
        with self.assertRaises(ValueError):
            Cipher(b"short", b"m" * 32)


class KeystreamTests(unittest.TestCase):
    def test_keystream_is_hmac_of_nonce_and_counter(self):
        key, nonce = b"k" * 32, b"n" * NONCE_SIZE
        block0 = hmac.new(key, nonce + (0).to_bytes(8, "big"), hashlib.sha256).digest()
        block1 = hmac.new(key, nonce + (1).to_bytes(8, "big"), hashlib.sha256).digest()
        self.assertEqual(keystream(key, nonce, 40), (block0 + block1)[:40])

    def test_xor_twice_gives_the_data_back(self):
        data, stream = b"Ada Reyes", b"\x01\x02\x03\x04\x05\x06\x07\x08\x09"
        self.assertEqual(xor(xor(data, stream), stream), data)

    def test_xor_keeps_leading_zero_bytes(self):
        self.assertEqual(xor(b"\x00\x00a", b"\x00\x00\x00"), b"\x00\x00a")


class EncryptTests(unittest.TestCase):
    def setUp(self):
        self.cipher = make_cipher()

    def test_round_trip(self):
        for data in (b"", b"x", b"Ada Reyes, born 1956-02-11", bytes(range(256)) * 10):
            self.assertEqual(self.cipher.decrypt(self.cipher.encrypt(data)), data)

    def test_layout(self):
        data = b"hello"
        message = self.cipher.encrypt(data)
        self.assertEqual(message[0], VERSION)
        self.assertEqual(len(message), 1 + NONCE_SIZE + len(data) + TAG_SIZE)

    def test_ciphertext_does_not_contain_the_data(self):
        message = self.cipher.encrypt(b"Medicaid status: yes")
        self.assertNotIn(b"Medicaid", message)

    def test_same_data_encrypts_differently_every_time(self):
        self.assertNotEqual(self.cipher.encrypt(b"same"), self.cipher.encrypt(b"same"))

    def test_fixed_nonce_is_repeatable(self):
        nonce = b"\x00" * NONCE_SIZE
        self.assertEqual(self.cipher.encrypt(b"same", nonce=nonce), self.cipher.encrypt(b"same", nonce=nonce))


class TamperTests(unittest.TestCase):
    """Every way of changing or misusing a message must fail loudly, never return wrong data."""

    def setUp(self):
        self.cipher = make_cipher()
        self.message = self.cipher.encrypt(b"plan_code = H1001-002")

    def test_changing_any_byte_is_caught(self):
        for i in range(len(self.message)):
            changed = bytearray(self.message)
            changed[i] ^= 0x01  # flip one bit
            with self.assertRaises(DecryptionError, msg=f"byte {i}"):
                self.cipher.decrypt(bytes(changed))

    def test_cut_short_is_caught(self):
        for size in (0, 10, len(self.message) - 1):
            with self.assertRaises(DecryptionError):
                self.cipher.decrypt(self.message[:size])

    def test_wrong_passphrase_is_caught(self):
        with self.assertRaises(DecryptionError):
            make_cipher("not the right passphrase").decrypt(self.message)

    def test_wrong_salt_is_caught(self):
        with self.assertRaises(DecryptionError):
            make_cipher(salt=b"t" * 16).decrypt(self.message)

    def test_unknown_version_is_caught(self):
        with self.assertRaises(DecryptionError):
            self.cipher.decrypt(b"\x09" + self.message[1:])

    def test_associated_data_must_match(self):
        message = self.cipher.encrypt(b"row 1", associated=b"clients")
        self.assertEqual(self.cipher.decrypt(message, associated=b"clients"), b"row 1")
        with self.assertRaises(DecryptionError):
            self.cipher.decrypt(message, associated=b"plans")  # moved to another table's file

    def test_flipping_a_bit_would_change_the_data_without_the_tag(self):
        # Why the tag matters: XOR encryption alone lets an attacker change data they can't read.
        nonce = b"\x00" * NONCE_SIZE
        message = bytearray(self.cipher.encrypt(b"role=read", nonce=nonce))
        start = 1 + NONCE_SIZE + len("role=")
        for i, (old, new) in enumerate(zip(b"read", b"evil")):
            message[start + i] ^= old ^ new
        ciphertext = bytes(message[1 + NONCE_SIZE:-TAG_SIZE])
        stream = keystream(self.cipher._encryption_key, nonce, len(ciphertext))  # type: ignore
        self.assertEqual(xor(ciphertext, stream), b"role=evil")  # the attack works on the raw bytes...
        with self.assertRaises(DecryptionError):
            self.cipher.decrypt(bytes(message))  # ...but the tag catches it


class TextTests(unittest.TestCase):
    def setUp(self):
        self.cipher = make_cipher()

    def test_round_trip(self):
        line = '{"op": "set", "key": "1", "value": {"name": "José Peña"}}'
        self.assertEqual(self.cipher.decrypt_text(self.cipher.encrypt_text(line)), line)

    def test_one_line_of_plain_ascii(self):
        encrypted = self.cipher.encrypt_text("line one\nline two")
        self.assertTrue(encrypted.isascii())
        self.assertNotIn("\n", encrypted)

    def test_garbage_is_caught(self):
        for line in ("not base64!!", "é", "QUJD"):
            with self.assertRaises(DecryptionError):
                self.cipher.decrypt_text(line)

    def test_decryption_error_is_a_value_error(self):
        # Storage.replay treats ValueError on the last line as a torn write, so this matters in Lesson 5.
        self.assertTrue(issubclass(DecryptionError, ValueError))


if __name__ == "__main__":
    unittest.main()
