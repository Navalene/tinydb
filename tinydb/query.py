"""QueryParser: turns the words a user types into a Query object.

Parsing (what did the user ask for?) is kept separate from executing (do it).
The parser never touches data, and the code that runs a Query never looks at
raw text. Phase 4 swaps in a mini-SQL parser that produces Query objects too.
"""

import json
import shlex
from dataclasses import dataclass
from typing import Any, Optional


class QueryError(ValueError):
    """Raised when a command is unknown or has the wrong arguments."""


@dataclass(frozen=True)
class Query:
    """One parsed command, ready to run."""

    command: str
    key: Optional[str] = None
    value: Any = None


class QueryParser:
    """Parses tinydb commands like 'set name Ada' or 'get name'."""

    # Every command and its usage text. The usage text says which arguments it takes.
    COMMANDS = {
        "set": "set <key> <value>",
        "get": "get <key>",
        "delete": "delete <key>",
        "exists": "exists <key>",
        "keys": "keys",
        "count": "count",
        "clear": "clear",
        "compact": "compact",
        "help": "help",
        "exit": "exit",
    }
    KEY_COMMANDS = ("get", "delete", "exists")
    ALIASES = {"quit": "exit"}

    def parse_line(self, line):
        """Parse one line of text, splitting it into words the way a shell does."""
        try:
            words = shlex.split(line)
        except ValueError as e:
            raise QueryError(str(e)) from e
        return self.parse(words)

    def parse(self, words):
        """Parse a list of words into a Query. Returns None for a blank command."""
        if not words:
            return None
        command, rest = words[0].lower(), words[1:]
        command = self.ALIASES.get(command, command)
        if command not in self.COMMANDS:
            raise QueryError(f"Unknown command: {command}. Type 'help' for a list of commands.")

        if command == "set":
            if len(rest) < 2:
                raise self._usage(command)
            return Query(command, key=rest[0], value=self.parse_value(" ".join(rest[1:])))
        if command in self.KEY_COMMANDS:
            if len(rest) != 1:
                raise self._usage(command)
            return Query(command, key=rest[0])
        if rest:
            raise self._usage(command)
        return Query(command)

    def _usage(self, command):
        return QueryError(f"Usage: {self.COMMANDS[command]}")

    @staticmethod
    def parse_value(raw):
        """Valid JSON becomes that type (42, true, [1,2]). Anything else stays a string."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
