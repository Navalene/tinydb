"""QueryParser: turns the words a user types into a Query object.

Parsing (what did the user ask for?) is kept separate from executing (do it).
The parser never touches data, and the code that runs a Query never looks at
raw text. Phase 4 swaps in a mini-SQL parser that produces Query objects too.

Phase 3 adds commands that work on a field inside the values: index, unindex,
find, range and sort. Those Queries carry the field name in Query.field.
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
    field: Optional[str] = None  # the field inside the value, for index and search commands
    kind: Optional[str] = None  # "hash" or "btree", for the index command
    low: Any = None  # range bounds; None means no limit
    high: Any = None
    descending: bool = False  # for sort


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
        "index": "index <field> [hash|btree]",
        "unindex": "unindex <field>",
        "indexes": "indexes",
        "find": "find <field> <value>",
        "range": "range <field> <low> <high>",
        "sort": "sort <field> [asc|desc]",
        "help": "help",
        "exit": "exit",
    }
    KEY_COMMANDS = ("get", "delete", "exists")
    ALIASES = {"quit": "exit"}
    INDEX_KINDS = ("hash", "btree")
    NO_LIMIT = "-"  # typed as a range bound to mean "no limit on this side"

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
        if command in ("index", "unindex", "find", "range", "sort"):
            return self._parse_field_command(command, rest)
        if command in self.KEY_COMMANDS:
            if len(rest) != 1:
                raise self._usage(command)
            return Query(command, key=rest[0])
        if rest:
            raise self._usage(command)
        return Query(command)

    def _parse_field_command(self, command, rest):
        """Parse the commands whose first argument is a field name."""
        if not rest:
            raise self._usage(command)
        field, args = rest[0], rest[1:]

        if command == "index":
            if len(args) > 1:
                raise self._usage(command)
            kind = args[0].lower() if args else "hash"
            if kind not in self.INDEX_KINDS:
                raise QueryError(f"Unknown index kind: {kind}. Use hash or btree")
            return Query(command, field=field, kind=kind)
        if command == "unindex":
            if args:
                raise self._usage(command)
            return Query(command, field=field)
        if command == "find":
            if not args:
                raise self._usage(command)
            return Query(command, field=field, value=self.parse_value(" ".join(args)))
        if command == "range":
            if len(args) != 2:
                raise self._usage(command)
            return Query(command, field=field, low=self.parse_bound(args[0]), high=self.parse_bound(args[1]))
        # sort
        order = args[0].lower() if args else "asc"
        if len(args) > 1 or order not in ("asc", "desc"):
            raise self._usage(command)
        return Query(command, field=field, descending=(order == "desc"))

    def _usage(self, command):
        return QueryError(f"Usage: {self.COMMANDS[command]}")

    def parse_bound(self, raw):
        """A range bound: "-" means no limit, anything else is parsed like a value."""
        return None if raw == self.NO_LIMIT else self.parse_value(raw)

    @staticmethod
    def parse_value(raw):
        """Valid JSON becomes that type (42, true, [1,2]). Anything else stays a string."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
