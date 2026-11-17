"""Record: one row of data, a key plus the value stored under it.

A Record checks its own key and value when it's created, so every other
class can trust that any Record it's handed is already valid.
"""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Record:
    """One key and its value. Frozen: its fields can't be reassigned after it's made."""

    key: str
    value: Any = None

    def __post_init__(self):
        self.check_key(self.key)
        try:
            text = json.dumps(self.value)
        except (TypeError, ValueError) as e:
            raise TypeError(f"Value is not JSON-serializable: {e}") from e
        # Store the value exactly as it will come back from disk, so a tuple becomes
        # a list right away instead of only after a restart.
        object.__setattr__(self, "value", json.loads(text))

    @staticmethod
    def check_key(key):
        """Raise an error unless key is a non-empty string."""
        if not isinstance(key, str):
            raise TypeError("Key must be a string")
        if key == "":
            raise ValueError("Key must not be empty")

    def to_dict(self):
        """Turn the record into a plain dict that json can write."""
        return {"key": self.key, "value": self.value}

    @classmethod
    def from_dict(cls, data):
        """Build a Record from a dict like the one to_dict() returns."""
        return cls(data["key"], data["value"])
