"""Lexer: splits SQL text into tokens, the "words" the parser reads.

    SELECT name FROM clients WHERE age >= 65;

becomes

    KEYWORD SELECT, NAME name, KEYWORD FROM, NAME clients, KEYWORD WHERE,
    NAME age, SYMBOL >=, NUMBER 65, SYMBOL ;, END

The lexer only cares what kind of word each piece is. Whether the words make
a valid statement is the parser's job. Text in 'single quotes' always becomes
one STRING token, so a value can never turn into SQL code. That is the root
of injection-safe parsing.

Phase 7 adds the ? placeholder, a SYMBOL token that stands for a value sent
separately from the SQL text. See sql.py.
"""

import string
from dataclasses import dataclass
from typing import Any, List

KEYWORD = "KEYWORD"
NAME = "NAME"
NUMBER = "NUMBER"
STRING = "STRING"
SYMBOL = "SYMBOL"
END = "END"

KEYWORDS = {
    "AND", "ASC", "BY", "CREATE", "DELETE", "DESC", "DESCRIBE", "DROP", "FALSE",
    "FROM", "INDEX", "INSERT", "INTO", "KEY", "LIMIT", "NOT", "NULL", "ON", "ORDER",
    "PRIMARY", "SELECT", "SET", "SHOW", "TABLE", "TABLES", "TRUE", "UPDATE", "USING",
    "VALUES", "WHERE",
}
MAX_NUMBER_LENGTH = 30  # digits; far more than any real value, and stops numbers too big to handle
SYMBOLS = ("<=", ">=", "!=", "<>", "(", ")", ",", ";", "*", "=", "<", ">", "?")  # two-character ones first
WORD_START = string.ascii_letters + "_"
WORD_CHARS = WORD_START + string.digits


class SqlError(ValueError):
    """Raised when SQL text can't be read or doesn't make sense."""


@dataclass(frozen=True)
class Token:
    """One piece of SQL: what kind it is, its value, and where it started in the text."""

    kind: str
    value: Any
    position: int

    def describe(self):
        """How the token reads in an error message."""
        if self.kind == END:
            return "the end"
        if self.kind == STRING:
            return repr(self.value)
        return str(self.value)


def tokenize(text):
    """Split SQL text into a list of Tokens. The last token is always END."""
    tokens: List[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif text.startswith("--", i):
            # A comment runs to the end of the line.
            end = text.find("\n", i)
            i = len(text) if end == -1 else end
        elif ch in WORD_START:
            start = i
            while i < len(text) and text[i] in WORD_CHARS:
                i += 1
            word = text[start:i]
            if word.upper() in KEYWORDS:
                tokens.append(Token(KEYWORD, word.upper(), start))
            else:
                tokens.append(Token(NAME, word.lower(), start))  # names ignore case, like in SQL
        elif ch in string.digits or (ch == "-" and text[i + 1:i + 2].isdigit()):
            start = i
            i += 1
            while i < len(text) and (text[i] in string.digits or text[i] == "."):
                i += 1
            tokens.append(Token(NUMBER, read_number(text[start:i], start), start))
        elif ch == "'":
            start = i
            value, i = read_string(text, i)
            tokens.append(Token(STRING, value, start))
        elif ch == '"':
            raise SqlError(f"Use single quotes for text, like 'Ada' (position {i})")
        else:
            symbol = next((s for s in SYMBOLS if text.startswith(s, i)), None)
            if symbol is None:
                raise SqlError(f"Unexpected character {ch!r} at position {i}")
            tokens.append(Token(SYMBOL, "!=" if symbol == "<>" else symbol, i))
            i += len(symbol)
    tokens.append(Token(END, None, len(text)))
    return tokens


def read_number(raw, position):
    """Turn number text into an int, or a float if it has a decimal point."""
    if len(raw) > MAX_NUMBER_LENGTH:
        # A 500,000-digit number is valid Python, but slow to convert and print: a cheap way to stall a server.
        raise SqlError(f"Number at position {position} is too long (over {MAX_NUMBER_LENGTH} characters)")
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        raise SqlError(f"Bad number {raw!r} at position {position}") from None


def read_string(text, start):
    """Read a 'quoted' string starting at text[start]. Returns (value, index after it).

    Two quotes in a row stand for one quote character: 'O''Brien' is O'Brien.
    """
    i = start + 1
    pieces = []
    while i < len(text):
        if text[i] == "'":
            if text[i + 1:i + 2] == "'":
                pieces.append("'")
                i += 2
                continue
            return "".join(pieces), i + 1
        pieces.append(text[i])
        i += 1
    raise SqlError(f"Text starting at position {start} is missing its closing quote")
