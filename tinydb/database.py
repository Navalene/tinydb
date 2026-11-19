"""Database: many SQL tables in one folder, and the code that runs SQL statements.

    tinydb_data/
        _catalog.jsonl   which tables exist, and each one's schema
        clients.jsonl    one log per table, read by the same Table class as before
        plans.jsonl

A SQL row is stored as a Record. Its key is the primary key as text, and its
value is a dict of the row's columns, like {"id": 1, "name": "Ada", "age": 70}.
That's exactly the kind of value Phase 3 taught Table to index and search, so
WHERE, ORDER BY and CREATE INDEX are built on find, find_range and create_index.

The parser says what to do. Database.execute decides how to do it (the
"query plan") and returns a Result. It never prints, so the shell and the
HTTP API can show the same Result in their own way.

Encryption at rest (Phase 6): Database(directory, passphrase="...") encrypts
every log in the folder. The first time, it writes _crypto.json with the salt
and a "check" value. Every later open must give the same passphrase, and a
wrong one is refused before any log is read, so nothing is ever trimmed by
mistake.
"""

import base64
import getpass
import json
import operator
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Tuple

from tinydb.algorithms import merge_sort
from tinydb.crypto import ITERATIONS, Cipher, DecryptionError, make_salt
from tinydb.schema import Schema, check_name
from tinydb.sql import (
    Condition,
    CreateIndex,
    CreateTable,
    Delete,
    Describe,
    DropTable,
    Insert,
    Select,
    ShowTables,
    Update,
    parse,
)
from tinydb.table import Table
from tinydb.wal import WriteAheadLog

CATALOG = "_catalog.jsonl"
CRYPTO_FILE = "_crypto.json"
CHECK_TEXT = "tinydb"  # encrypted into _crypto.json, so a wrong passphrase is caught right away
PASSPHRASE_VARIABLE = "TINYDB_PASSPHRASE"
CREATE_TABLE = "create_table"
DROP_TABLE = "drop_table"

COMPARE = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class DatabaseError(ValueError):
    """Raised when a statement can't run, like SELECT from a table that doesn't exist."""


@dataclass(frozen=True)
class Result:
    """What a statement returns: a message, plus columns and rows for SELECT-like statements."""

    message: str
    columns: Tuple[str, ...] = ()
    rows: Tuple[Tuple[Any, ...], ...] = ()
    plan: str = ""  # how the rows were found, like "btree index on age"


def is_encrypted(directory):
    """True if directory holds an encrypted database."""
    return os.path.exists(os.path.join(directory, CRYPTO_FILE))


def passphrase_for(directory, environ=None, ask=None):
    """The passphrase to open directory with, or None for a plain database.

    It comes from the TINYDB_PASSPHRASE environment variable. If that isn't set,
    the folder is encrypted, and someone is typing at a Terminal, it's asked for
    with a hidden prompt (nothing shows as you type). environ and ask are for tests.
    """
    environ = os.environ if environ is None else environ
    passphrase = environ.get(PASSPHRASE_VARIABLE) or None
    if passphrase is None and is_encrypted(directory):
        if ask is None and sys.stdin.isatty():
            ask = getpass.getpass
        if ask is not None:
            passphrase = ask(f"Passphrase for {directory}: ") or None
    return passphrase


def open_cipher(directory, passphrase, iterations=ITERATIONS):
    """Return the Cipher for a database folder, or None if it isn't encrypted.

    Refuses to mix: an encrypted folder needs the passphrase, and a folder that
    already holds plain data can't be opened with one.
    """
    path = os.path.join(directory, CRYPTO_FILE)
    if not is_encrypted(directory):
        if passphrase is None:
            return None  # a plain folder, like before Phase 6
        if os.path.exists(os.path.join(directory, CATALOG)):
            raise DatabaseError(f"{directory} already holds unencrypted data. Use a new folder for an encrypted database")
        os.makedirs(directory, exist_ok=True)
        salt = make_salt()
        cipher = Cipher.from_passphrase(passphrase, salt, iterations)
        settings = {
            "kdf": "pbkdf2-hmac-sha256",
            "iterations": iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "check": cipher.encrypt_text(CHECK_TEXT, CRYPTO_FILE.encode()),
        }
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tinydb-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)  # all or nothing, like Storage.rewrite
        return cipher

    if passphrase is None:
        raise DatabaseError(f"{directory} is encrypted. Set TINYDB_PASSPHRASE to its passphrase")
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    cipher = Cipher.from_passphrase(passphrase, base64.b64decode(settings["salt"]), settings["iterations"])
    try:
        if cipher.decrypt_text(settings["check"], CRYPTO_FILE.encode()) != CHECK_TEXT:
            raise DecryptionError("check value doesn't match")
    except DecryptionError:
        raise DatabaseError(f"Wrong passphrase for {directory}") from None
    return cipher


def plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def matches(row, condition):
    """True if the row passes one WHERE condition. A missing (NULL) column never passes."""
    value = row.get(condition.column)
    if value is None:
        return False
    return COMPARE[condition.op](value, condition.value)


class Database:
    """A folder of tables. Open it, then call run("SELECT ...") or execute(statement)."""

    def __init__(self, directory, passphrase=None, iterations=ITERATIONS):
        """Open (or start) the database in directory. Give a passphrase to encrypt it.

        iterations is only used when a new encrypted folder is made. The tests pass a small number.
        """
        self.directory = directory
        self._schemas = {}  # table name -> Schema
        self._tables = {}  # table name -> Table
        self._cipher = open_cipher(directory, passphrase, iterations)  # checked before any log is read
        self._catalog = WriteAheadLog(os.path.join(directory, CATALOG), self._cipher)
        self._catalog.replay(self._apply_catalog)
        for name in self._schemas:
            self._tables[name] = Table(name, WriteAheadLog(self._path(name), self._cipher))

    @property
    def encrypted(self):
        return self._cipher is not None

    def _apply_catalog(self, entry):
        """Apply one catalog log entry. Raises if the entry is invalid."""
        op, name = entry["op"], entry["table"]
        check_name(name, "Table name")
        if op == CREATE_TABLE:
            self._schemas[name] = Schema.from_dict(entry["schema"])
        elif op == DROP_TABLE:
            self._schemas.pop(name, None)
        else:
            raise ValueError(f"unknown op {op!r}")

    def _path(self, name):
        return os.path.join(self.directory, name + ".jsonl")

    # ---- tables ----

    def create_table(self, name, schema):
        """Add a new, empty table."""
        check_name(name, "Table name")
        if name.startswith("_"):
            raise DatabaseError("Table names can't start with _ (tinydb keeps those for itself)")
        if name in self._schemas:
            raise DatabaseError(f"Table {name} already exists")
        os.makedirs(self.directory, exist_ok=True)
        path = self._path(name)
        if os.path.exists(path):
            os.remove(path)  # left over from a dropped table, if a crash hit before it was deleted
        self._catalog.append({"op": CREATE_TABLE, "table": name, "schema": schema.to_dict()})
        self._schemas[name] = schema
        self._tables[name] = Table(name, WriteAheadLog(path, self._cipher))

    def drop_table(self, name):
        """Remove a table and all of its rows."""
        self.table(name)  # raises if there's no such table
        self._catalog.append({"op": DROP_TABLE, "table": name})
        del self._schemas[name]
        del self._tables[name]
        path = self._path(name)
        if os.path.exists(path):
            os.remove(path)

    def table(self, name):
        """Return the Table called name. Raises DatabaseError if there isn't one."""
        if name not in self._tables:
            raise DatabaseError(f"No table named {name}")
        return self._tables[name]

    def schema(self, name):
        """Return the Schema of the table called name."""
        self.table(name)
        return self._schemas[name]

    def table_names(self):
        return sorted(self._tables)

    # ---- running SQL ----

    def run(self, sql):
        """Parse SQL text and run every statement in it. Returns a list of Results."""
        return [self.execute(statement) for statement in parse(sql)]

    def execute(self, statement):
        """Run one parsed statement and return a Result."""
        if isinstance(statement, CreateTable):
            self.create_table(statement.table, Schema(statement.columns))
            return Result(f"Created table {statement.table}")
        if isinstance(statement, DropTable):
            self.drop_table(statement.table)
            return Result(f"Dropped table {statement.table}")
        if isinstance(statement, CreateIndex):
            self.schema(statement.table).column(statement.column)  # raises if the column doesn't exist
            if self.table(statement.table).create_index(statement.column, statement.kind):
                return Result(f"Created {statement.kind} index on {statement.table}.{statement.column}")
            return Result(f"{statement.table}.{statement.column} already has a {statement.kind} index")
        if isinstance(statement, Insert):
            return self._insert(statement)
        if isinstance(statement, Select):
            return self._select(statement)
        if isinstance(statement, Update):
            return self._update(statement)
        if isinstance(statement, Delete):
            records, plan = self._find(statement.table, statement.where)
            count = self.table(statement.table).delete_many([record.key for record in records])
            return Result(f"Deleted {plural(count, 'row')}", plan=plan)
        if isinstance(statement, ShowTables):
            names = self.table_names()
            return Result(plural(len(names), "table"), ("table",), tuple((name,) for name in names))
        if isinstance(statement, Describe):
            return self._describe(statement.table)
        raise DatabaseError(f"Don't know how to run {statement!r}")

    def _insert(self, statement):
        schema = self.schema(statement.table)
        table = self.table(statement.table)
        names = statement.columns or tuple(schema.names())
        if len(set(names)) != len(names):
            raise DatabaseError("A column is listed twice")
        pairs = []
        new_keys = set()
        for values in statement.rows:
            if len(values) != len(names):
                raise DatabaseError(f"Expected {plural(len(names), 'value')} per row but got {len(values)}")
            row = schema.make_row(dict(zip(names, values)))
            key = schema.key_of(row)
            if key in table or key in new_keys:
                raise DatabaseError(f"A row with {schema.primary_key} = {row[schema.primary_key]!r} already exists")
            new_keys.add(key)
            pairs.append((key, row))
        table.set_many(pairs)  # one transaction: every row is saved, or none are
        return Result(f"Inserted {plural(len(pairs), 'row')}")

    def _select(self, statement):
        schema = self.schema(statement.table)
        names = statement.columns or tuple(schema.names())
        for name in names:
            schema.column(name)  # raises if the column doesn't exist
        records, plan = self._find(statement.table, statement.where)
        rows = [record.value for record in records]

        order_by = statement.order_by or schema.primary_key
        schema.column(order_by)
        # NULLs sort last, or first when DESC, like PostgreSQL.
        rows = merge_sort(rows, key=lambda row: (1,) if row.get(order_by) is None else (0, row[order_by]),
                          reverse=statement.descending)
        if statement.order_by:
            plan += " + merge sort"
        if statement.limit is not None:
            rows = rows[:statement.limit]
        table_rows = tuple(tuple(row.get(name) for name in names) for row in rows)
        return Result(plural(len(table_rows), "row"), tuple(names), table_rows, plan)

    def _update(self, statement):
        schema = self.schema(statement.table)
        changes = dict(statement.assignments)
        for name in changes:
            schema.column(name)
        if schema.primary_key in changes:
            raise DatabaseError(f"Can't change the primary key {schema.primary_key}. Delete the row and insert a new one")
        records, plan = self._find(statement.table, statement.where)
        pairs = [(record.key, schema.make_row({**record.value, **changes})) for record in records]
        self.table(statement.table).set_many(pairs)
        return Result(f"Updated {plural(len(pairs), 'row')}", plan=plan)

    def _describe(self, name):
        schema = self.schema(name)
        indexes = dict(self.table(name).indexes())
        rows = []
        for column in schema.columns:
            notes = []
            if column.primary_key:
                notes.append("PRIMARY KEY")
            if column.not_null:
                notes.append("NOT NULL")
            if column.name in indexes:
                notes.append(f"{indexes[column.name]} index")
            rows.append((column.name, column.type, ", ".join(notes)))
        return Result(plural(len(rows), "column"), ("column", "type", "notes"), tuple(rows))

    # ---- WHERE: the query planner ----

    def _find(self, name, where):
        """Return (records that pass every condition, plan)."""
        schema = self.schema(name)
        table = self.table(name)
        # Check each value against its column's type, so age = 'old' fails loudly instead of matching nothing.
        conditions = [Condition(c.column, c.op, schema.column(c.column).convert(c.value)) for c in where]
        candidates, plan = self._candidates(table, schema, conditions)
        # The plan only narrows things down. Every condition is still checked on every candidate.
        found = [record for record in candidates if all(matches(record.value, c) for c in conditions)]
        return found, plan

    @staticmethod
    def _candidates(table, schema, conditions):
        """Use one condition to pick the rows worth checking, the fastest way available."""
        for c in conditions:
            if c.op == "=" and c.column == schema.primary_key:
                record = table.get(str(c.value))
                return ([] if record is None else [record]), "primary key lookup"
        indexed = dict(table.indexes())  # column -> "hash" or "btree"
        for c in conditions:
            if c.op == "=" and c.column in indexed:
                return table.find(c.column, c.value), table.last_plan
        for c in conditions:
            if c.op in ("<", "<=", ">", ">=") and indexed.get(c.column) == "btree":
                low = c.value if c.op in (">", ">=") else None
                high = c.value if c.op in ("<", "<=") else None
                return table.find_range(c.column, low, high), table.last_plan
        return table.records(), "full scan"
