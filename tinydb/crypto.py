"""Encryption at rest, built only from Python's standard library.

Python has no AES built in, so this module builds authenticated encryption
from pieces that are built in and well studied:

    key       PBKDF2-HMAC-SHA256 turns a passphrase into two 32-byte keys,
              one for encrypting and one for checking (the "MAC" key)
    encrypt   HMAC-SHA256(key, nonce + counter) makes a stream of random-looking
              bytes, and the data is XORed with it (counter mode, "CTR")
    check     HMAC-SHA256 over everything that was written: the "tag".
              Any change to the encrypted bytes makes the tag wrong
              (encrypt-then-MAC)

One encrypted message looks like this:

    version (1 byte) | nonce (16 bytes) | ciphertext (same length as the data) | tag (32 bytes)

IMPORTANT: this is a learning project. In a real product, use a reviewed
library such as AES-GCM from the "cryptography" package, and never design
your own scheme. This one uses standard parts in a standard way, so it is
sound in theory, but nobody has reviewed or attacked this code.
"""

import base64
import binascii
import hashlib
import hmac
import secrets

VERSION = 1  # the first byte of every message, so the format can change later
NONCE_SIZE = 16  # bytes. Random for every message, so the same data never encrypts the same way twice
TAG_SIZE = 32  # bytes, the size of an HMAC-SHA256
BLOCK_SIZE = 32  # bytes of keystream from each HMAC
SALT_SIZE = 16  # bytes
ITERATIONS = 600_000  # PBKDF2 rounds: what OWASP recommends for HMAC-SHA256 (2023)
MIN_PASSPHRASE = 12  # characters


class DecryptionError(ValueError):
    """Raised when a message can't be decrypted: wrong key, or it was changed or cut short."""


def make_salt():
    """A new random salt. Store it next to the data. It isn't secret."""
    return secrets.token_bytes(SALT_SIZE)


def derive_keys(passphrase, salt, iterations=ITERATIONS):
    """Turn a passphrase into (encryption key, MAC key) with PBKDF2.

    PBKDF2 runs HMAC-SHA256 `iterations` times on purpose. It takes a fraction of a
    second for you, once, but makes every guess that slow for someone trying passwords.
    """
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters")
    keys = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=64)
    return keys[:32], keys[32:]


def keystream(key, nonce, length):
    """length bytes of keystream: HMAC(key, nonce + 0), HMAC(key, nonce + 1), ... joined together."""
    blocks = []
    for counter in range((length + BLOCK_SIZE - 1) // BLOCK_SIZE):
        blocks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
    return b"".join(blocks)[:length]


def xor(data, stream):
    """XOR two byte strings of the same length. Doing it twice with the same stream gives data back."""
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


class Cipher:
    """Encrypts and decrypts bytes with one pair of keys."""

    def __init__(self, encryption_key, mac_key):
        if len(encryption_key) != 32 or len(mac_key) != 32:
            raise ValueError("Each key must be 32 bytes")
        self._encryption_key = encryption_key
        self._mac_key = mac_key

    @classmethod
    def from_passphrase(cls, passphrase, salt, iterations=ITERATIONS):
        return cls(*derive_keys(passphrase, salt, iterations))

    def _tag(self, header, ciphertext, associated):
        """The HMAC that proves nothing was changed. associated is extra data that's checked but not encrypted."""
        mac = hmac.new(self._mac_key, digestmod=hashlib.sha256)
        mac.update(len(associated).to_bytes(8, "big") + associated)  # length first, so the parts can't blur together
        mac.update(header + ciphertext)
        return mac.digest()

    def encrypt(self, data, associated=b"", nonce=None):
        """Encrypt bytes. Returns version + nonce + ciphertext + tag.

        nonce is only for tests. Leave it out: a new random one is made every time.
        """
        nonce = nonce if nonce is not None else secrets.token_bytes(NONCE_SIZE)
        header = bytes([VERSION]) + nonce
        ciphertext = xor(data, keystream(self._encryption_key, nonce, len(data)))
        return header + ciphertext + self._tag(header, ciphertext, associated)

    def decrypt(self, message, associated=b""):
        """Check the tag, then decrypt. Raises DecryptionError if anything is wrong."""
        if len(message) < 1 + NONCE_SIZE + TAG_SIZE:
            raise DecryptionError("Encrypted data is too short")
        if message[0] != VERSION:
            raise DecryptionError(f"Unknown encryption version {message[0]}")
        header = message[:1 + NONCE_SIZE]
        ciphertext = message[1 + NONCE_SIZE:-TAG_SIZE]
        tag = message[-TAG_SIZE:]
        # Check before decrypting, and in constant time, like the API keys.
        if not hmac.compare_digest(tag, self._tag(header, ciphertext, associated)):
            raise DecryptionError("Wrong passphrase, or the data was changed")
        nonce = header[1:]
        return xor(ciphertext, keystream(self._encryption_key, nonce, len(ciphertext)))

    # ---- text, for one line of a log file ----

    def encrypt_text(self, text, associated=b""):
        """Encrypt a string into one line of plain ASCII (base64), with no newline in it."""
        return base64.urlsafe_b64encode(self.encrypt(text.encode("utf-8"), associated)).decode("ascii")

    def decrypt_text(self, line, associated=b""):
        """Undo encrypt_text. Raises DecryptionError if the line isn't valid."""
        try:
            message = base64.urlsafe_b64decode(line.encode("ascii"))
        except (binascii.Error, UnicodeEncodeError):
            raise DecryptionError("Encrypted line isn't valid base64") from None
        return self.decrypt(message, associated).decode("utf-8")
