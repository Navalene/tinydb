"""Schema: the columns a SQL table has, and the type of each one.

A key-value table takes any JSON value. A SQL table is stricter. CREATE TABLE
says which columns exist and what type each one holds, and every row is
checked against that before it's written. Bad data is stopped at the door, so
everything already inside the table can be trusted.

    CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, age INT)

becomes a Schema with three Columns. Exactly one column is the primary key:
its value is unique in the table and becomes the row's record key.
"""

import datetime
from dataclasses import dataclass
from typing import Tuple

TYPES = ("INT", "FLOAT", "TEXT", "BOOL", "DATE")
KEY_TYPES = ("INT", "TEXT", "DATE")  # types a primary key may have
NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_"


class SchemaError(ValueError):
    """Raised when a schema is invalid or a row doesn't fit it."""


def check_name(name, what="Name"):
    """Raise SchemaError unless name is lowercase letters, digits and _, not starting with a digit.

    Table names become file names, so this rule also stops names like "../secrets".
    """
    if not isinstance(name, str) or name == "":
        raise SchemaError(f"{what} must be a non-empty string")
    if name[0].isdigit() or any(ch not in NAME_CHARS for ch in name):
        raise SchemaError(f"{what} {name!r} must use only a-z, 0-9 and _, and not start with a digit")


@dataclass(frozen=True)
class Column:
    """One column: its name, its type, and whether it may be left empty (NULL)."""

    name: str
    type: str
    primary_key: bool = False
    not_null: bool = False

    def __post_init__(self):
        check_name(self.name, "Column name")
        if self.type not in TYPES:
            raise SchemaError(f"Unknown type {self.type!r} for column {self.name}. Use one of: {', '.join(TYPES)}")
        if self.primary_key and self.type not in KEY_TYPES:
            raise SchemaError(f"Primary key {self.name} must be {', '.join(KEY_TYPES)}, not {self.type}")

    def convert(self, value):
        """Check that value fits this column. Returns the value to store.

        Raises SchemaError if it doesn't fit, for example text in an INT column.
        """
        if value is None:
            if self.primary_key or self.not_null:
                raise SchemaError(f"Column {self.name} can't be NULL")
            return None
        if self.type == "INT":
            # bool is checked first because Python counts True as the int 1.
            if isinstance(value, bool) or not isinstance(value, int):
                raise self._wrong_type(value)
            return value
        if self.type == "FLOAT":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise self._wrong_type(value)
            return float(value)
        if self.type == "BOOL":
            if not isinstance(value, bool):
                raise self._wrong_type(value)
            return value
        # TEXT and DATE are both stored as strings.
        if not isinstance(value, str):
            raise self._wrong_type(value)
        if self.type == "DATE":
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise SchemaError(f"Column {self.name} is DATE, so use 'YYYY-MM-DD', not {value!r}") from None
        return value

    def _wrong_type(self, value):
        return SchemaError(f"Column {self.name} is {self.type}, so it can't hold {value!r}")

    def to_dict(self):
        """Turn the column into a plain dict that json can write."""
        return {"name": self.name, "type": self.type, "primary_key": self.primary_key, "not_null": self.not_null}

    @classmethod
    def from_dict(cls, data):
        """Build a Column from a dict like the one to_dict() returns."""
        return cls(data["name"], data["type"], data["primary_key"], data["not_null"])


@dataclass(frozen=True)
class Schema:
    """Every column of one table, in order."""

    columns: Tuple[Column, ...]

    def __post_init__(self):
        object.__setattr__(self, "columns", tuple(self.columns))  # a list becomes a tuple
        if not self.columns:
            raise SchemaError("A table needs at least one column")
        names = [column.name for column in self.columns]
        for name in names:
            if names.count(name) > 1:
                raise SchemaError(f"Column {name} is listed twice")
        keys = [column for column in self.columns if column.primary_key]
        if len(keys) != 1:
            raise SchemaError("A table needs exactly one PRIMARY KEY column")

    @property
    def primary_key(self):
        """The name of the primary key column."""
        return [column.name for column in self.columns if column.primary_key][0]

    def names(self):
        """Every column name, in the order CREATE TABLE listed them."""
        return [column.name for column in self.columns]

    def column(self, name):
        """Return the Column called name. Raises SchemaError if there isn't one."""
        for column in self.columns:
            if column.name == name:
                return column
        raise SchemaError(f"No column named {name}")

    def make_row(self, values):
        """Check a dict of column -> value and return the row to store.

        Every value is converted by its column. Columns that are NULL are left
        out of the stored row, so a record only holds the columns it has.
        """
        for name in values:
            self.column(name)  # raises for a column that doesn't exist
        row = {}
        for column in self.columns:
            value = column.convert(values.get(column.name))
            if value is not None:
                row[column.name] = value
        return row

    def key_of(self, row):
        """The record key for a row: its primary key value as text."""
        return str(row[self.primary_key])

    def to_dict(self):
        """Turn the schema into a plain dict that json can write."""
        return {"columns": [column.to_dict() for column in self.columns]}

    @classmethod
    def from_dict(cls, data):
        """Build a Schema from a dict like the one to_dict() returns."""
        return cls(tuple(Column.from_dict(item) for item in data["columns"]))
