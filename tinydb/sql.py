"""SqlParser: turns SQL text into statement objects.

The lexer splits the text into tokens. The parser reads those tokens left to
right, one grammar rule per method. That style is called recursive descent:

    SELECT <columns> FROM <table> [WHERE <condition> [AND <condition>]...]
           [ORDER BY <column> [ASC|DESC]] [LIMIT <number>]

The result is a plain dataclass such as Select(...) that says what to do,
without touching any data. The Database runs it. Keeping the two apart means
the HTTP API can parse a request body the same way the shell does.

Supported statements:
    CREATE TABLE t (col TYPE [PRIMARY KEY] [NOT NULL], ...)
    DROP TABLE t
    CREATE INDEX ON t [USING HASH|BTREE] (col)
    INSERT INTO t [(col, ...)] VALUES (value, ...), (value, ...)
    SELECT * | col, ... FROM t [WHERE ...] [ORDER BY col [ASC|DESC]] [LIMIT n]
    UPDATE t SET col = value, ... [WHERE ...]
    DELETE FROM t [WHERE ...]
    SHOW TABLES
    DESCRIBE t

Phase 7: anywhere a value can go, a ? placeholder can go instead, and the
values are passed separately, in order:

    parse("SELECT * FROM clients WHERE name = ? AND age > ?", ["O'Brien", 65])

A value never passes through the lexer, so it can't be read as SQL, whatever
it contains. That's what makes parameterized queries injection-proof.
"""

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from tinydb.lexer import END, KEYWORD, NAME, NUMBER, STRING, SYMBOL, SqlError, tokenize
from tinydb.schema import TYPES, Column

COMPARISONS = ("=", "!=", "<", "<=", ">", ">=")
INDEX_KINDS = ("HASH", "BTREE")
PARAM_TYPES = (str, bool, int, float, type(None))  # what a ? value may be: the same kinds a literal can be


# ---- statements: what the parser produces ----


@dataclass(frozen=True)
class Condition:
    """One test in a WHERE clause, like age >= 65."""

    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class CreateTable:
    table: str
    columns: Tuple[Column, ...]


@dataclass(frozen=True)
class DropTable:
    table: str


@dataclass(frozen=True)
class CreateIndex:
    table: str
    column: str
    kind: str  # "hash" or "btree"


@dataclass(frozen=True)
class Insert:
    table: str
    columns: Optional[Tuple[str, ...]]  # None means every column, in table order
    rows: Tuple[Tuple[Any, ...], ...]


@dataclass(frozen=True)
class Select:
    table: str
    columns: Optional[Tuple[str, ...]]  # None means SELECT *
    where: Tuple[Condition, ...] = ()
    order_by: Optional[str] = None
    descending: bool = False
    limit: Optional[int] = None


@dataclass(frozen=True)
class Update:
    table: str
    assignments: Tuple[Tuple[str, Any], ...]  # (column, new value) pairs
    where: Tuple[Condition, ...] = ()


@dataclass(frozen=True)
class Delete:
    table: str
    where: Tuple[Condition, ...] = ()


@dataclass(frozen=True)
class ShowTables:
    pass


@dataclass(frozen=True)
class Describe:
    table: str


# ---- the parser ----


class SqlParser:
    """Reads one or more SQL statements. Make a new parser for each piece of text."""

    def __init__(self, text, params: Sequence[Any] = ()):
        self.tokens = tokenize(text)
        self.pos = 0
        self.params = check_params(params)
        self.used = 0  # how many ? placeholders have been filled so far

    # -- reading tokens --

    def peek(self):
        """The current token, without moving past it."""
        return self.tokens[self.pos]

    def advance(self):
        """Return the current token and move to the next one."""
        token = self.tokens[self.pos]
        if token.kind != END:
            self.pos += 1
        return token

    def accept(self, kind, value=None):
        """If the current token matches, move past it and return True. Otherwise return False."""
        token = self.peek()
        if token.kind == kind and (value is None or token.value == value):
            self.advance()
            return True
        return False

    def expect(self, kind, value=None, what=None):
        """Move past the current token if it matches, or raise SqlError saying what was expected."""
        token = self.peek()
        if token.kind == kind and (value is None or token.value == value):
            return self.advance()
        raise SqlError(f"Expected {what or value or kind.lower()} but found {token.describe()} at position {token.position}")

    def keyword(self, word):
        self.expect(KEYWORD, word)

    def symbol(self, sym):
        self.expect(SYMBOL, sym, f"'{sym}'")

    def name(self, what="a name"):
        return self.expect(NAME, what=what).value

    # -- whole statements --

    def parse_script(self):
        """Parse every statement in the text. Statements are separated by ';'."""
        statements = []
        while True:
            while self.accept(SYMBOL, ";"):
                pass
            if self.peek().kind == END:
                if self.used != len(self.params):
                    raise SqlError(f"Too many values: got {len(self.params)} for {self.used} ? placeholder(s)")
                return statements
            statements.append(self.statement())
            if self.peek().kind != END:
                self.symbol(";")

    def statement(self):
        """Parse one statement, choosing the rule by its first word."""
        token = self.peek()
        if self.accept(KEYWORD, "CREATE"):
            if self.accept(KEYWORD, "TABLE"):
                return self.create_table()
            if self.accept(KEYWORD, "INDEX"):
                return self.create_index()
            raise SqlError("Expected TABLE or INDEX after CREATE")
        if self.accept(KEYWORD, "DROP"):
            self.keyword("TABLE")
            return DropTable(self.name("a table name"))
        if self.accept(KEYWORD, "INSERT"):
            return self.insert()
        if self.accept(KEYWORD, "SELECT"):
            return self.select()
        if self.accept(KEYWORD, "UPDATE"):
            return self.update()
        if self.accept(KEYWORD, "DELETE"):
            self.keyword("FROM")
            return Delete(self.name("a table name"), self.where())
        if self.accept(KEYWORD, "SHOW"):
            self.keyword("TABLES")
            return ShowTables()
        if self.accept(KEYWORD, "DESCRIBE"):
            return Describe(self.name("a table name"))
        raise SqlError(f"Unknown statement starting with {token.describe()}. "
                       "Use CREATE, DROP, INSERT, SELECT, UPDATE, DELETE, SHOW TABLES or DESCRIBE")

    def create_table(self):
        table = self.name("a table name")
        self.symbol("(")
        columns = [self.column_definition()]
        while self.accept(SYMBOL, ","):
            columns.append(self.column_definition())
        self.symbol(")")
        return CreateTable(table, tuple(columns))

    def column_definition(self):
        """name TYPE [PRIMARY KEY] [NOT NULL], in either order."""
        name = self.name("a column name")
        token = self.expect(NAME, what="a column type")
        column_type = token.value.upper()
        if column_type not in TYPES:
            raise SqlError(f"Unknown type {token.value} at position {token.position}. Use one of: {', '.join(TYPES)}")
        primary_key = not_null = False
        while True:
            if self.accept(KEYWORD, "PRIMARY"):
                self.keyword("KEY")
                primary_key = True
            elif self.accept(KEYWORD, "NOT"):
                self.keyword("NULL")
                not_null = True
            else:
                return Column(name, column_type, primary_key, not_null)

    def create_index(self):
        self.keyword("ON")
        table = self.name("a table name")
        kind = "BTREE"
        if self.accept(KEYWORD, "USING"):
            token = self.expect(NAME, what="HASH or BTREE")
            kind = token.value.upper()
            if kind not in INDEX_KINDS:
                raise SqlError(f"Unknown index kind {token.value}. Use HASH or BTREE")
        self.symbol("(")
        column = self.name("a column name")
        self.symbol(")")
        return CreateIndex(table, column, kind.lower())

    def insert(self):
        self.keyword("INTO")
        table = self.name("a table name")
        columns = None
        if self.accept(SYMBOL, "("):
            columns = tuple(self.name_list())
            self.symbol(")")
        self.keyword("VALUES")
        rows = [self.value_row()]
        while self.accept(SYMBOL, ","):
            rows.append(self.value_row())
        return Insert(table, columns, tuple(rows))

    def value_row(self):
        """(value, value, ...)"""
        self.symbol("(")
        values = [self.literal()]
        while self.accept(SYMBOL, ","):
            values.append(self.literal())
        self.symbol(")")
        return tuple(values)

    def select(self):
        columns = None
        if not self.accept(SYMBOL, "*"):
            columns = tuple(self.name_list())
        self.keyword("FROM")
        table = self.name("a table name")
        where = self.where()
        order_by, descending, limit = None, False, None
        if self.accept(KEYWORD, "ORDER"):
            self.keyword("BY")
            order_by = self.name("a column name")
            if self.accept(KEYWORD, "DESC"):
                descending = True
            else:
                self.accept(KEYWORD, "ASC")
        if self.accept(KEYWORD, "LIMIT"):
            token = self.expect(NUMBER, what="a number")
            if not isinstance(token.value, int) or token.value < 0:
                raise SqlError(f"LIMIT needs a whole number of 0 or more, not {token.value}")
            limit = token.value
        return Select(table, columns, where, order_by, descending, limit)

    def update(self):
        table = self.name("a table name")
        self.keyword("SET")
        assignments = [self.assignment()]
        while self.accept(SYMBOL, ","):
            assignments.append(self.assignment())
        return Update(table, tuple(assignments), self.where())

    def assignment(self):
        """column = value"""
        column = self.name("a column name")
        self.symbol("=")
        return (column, self.literal())

    # -- pieces shared by several statements --

    def name_list(self):
        """name, name, ..."""
        names = [self.name("a column name")]
        while self.accept(SYMBOL, ","):
            names.append(self.name("a column name"))
        return names

    def where(self):
        """[WHERE condition [AND condition]...]. Returns a tuple of Conditions, empty if no WHERE."""
        if not self.accept(KEYWORD, "WHERE"):
            return ()
        conditions = [self.condition()]
        while self.accept(KEYWORD, "AND"):
            conditions.append(self.condition())
        return tuple(conditions)

    def condition(self):
        """column op value, like age >= 65."""
        column = self.name("a column name")
        token = self.peek()
        if token.kind != SYMBOL or token.value not in COMPARISONS:
            raise SqlError(f"Expected a comparison (= != < <= > >=) but found {token.describe()} at position {token.position}")
        self.advance()
        value = self.literal()
        if value is None:
            raise SqlError("NULL can't be compared with =, <, and so on, because it means 'no value'")
        return Condition(column, token.value, value)

    def literal(self):
        """A value: a number, 'text', TRUE, FALSE, NULL, or a ? placeholder."""
        token = self.peek()
        if token.kind in (NUMBER, STRING):
            return self.advance().value
        if self.accept(SYMBOL, "?"):
            return self.placeholder(token)
        if self.accept(KEYWORD, "TRUE"):
            return True
        if self.accept(KEYWORD, "FALSE"):
            return False
        if self.accept(KEYWORD, "NULL"):
            return None
        raise SqlError(f"Expected a value but found {token.describe()} at position {token.position}")

    def placeholder(self, token):
        """The next value from params, for the ? just read."""
        if self.used >= len(self.params):
            raise SqlError(f"Not enough values: the ? at position {token.position} needs value "
                           f"{self.used + 1}, but only {len(self.params)} were given")
        value = self.params[self.used]
        self.used += 1
        return value


def check_params(params):
    """Check the values for ? placeholders and return them as a tuple."""
    if isinstance(params, (str, bytes, dict)):
        # A string is a sequence too: "Ada" would quietly become 3 values, "A", "d", "a".
        raise SqlError("Values for ? placeholders must be a list, like [\"Ada\", 70]")
    params = tuple(params)
    for number, value in enumerate(params, 1):
        if not isinstance(value, PARAM_TYPES):
            raise SqlError(f"Value {number} for a ? placeholder is a {type(value).__name__}. "
                           "Use a number, text, true, false or null")
    return params


def parse(text, params: Sequence[Any] = ()):
    """Parse SQL text into a list of statements, filling each ? with the next value in params."""
    return SqlParser(text, params).parse_script()
