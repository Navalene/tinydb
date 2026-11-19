"""Command-line interface for tinydb.

Two ways to use it:
    One-shot:     python3 main.py set name Ada
    Interactive:  python3 main.py        (opens a tinydb> prompt)

Each command goes through two steps: QueryParser turns the words into a Query,
then execute() runs that Query against a Table.

Phase 4 adds SQL. A line that starts with a SQL word (SELECT, INSERT, ...) or
ends with ";" is SQL, and runs against the Database in the --dir folder. In the
interactive prompt a SQL statement can span several lines and ends at ";".

Phase 6 adds the HTTP API and its keys:
    python3 main.py serve [--port 8000] [--host 127.0.0.1]
    python3 main.py apikey create NAME --role read|write
An encrypted --dir folder is opened with TINYDB_PASSPHRASE, or a hidden prompt.
"""

import argparse
import json
import sys

from tinydb import auth, server
from tinydb.database import Database, passphrase_for
from tinydb.query import QueryError, QueryParser
from tinydb.storage import CorruptLogError
from tinydb.table import Table

HELP_TEXT = """Commands:
  set <key> <value>   store a value (JSON like 42 or [1,2] is parsed, anything else is text)
  get <key>           print a value
  delete <key>        remove a key
  exists <key>        print true or false
  keys                list all keys
  count               print the number of keys
  clear               remove all keys
  compact             shrink the log file down to just the live keys

Searching values that are JSON objects, like {"name": "Ada", "plan": "PPO", "age": 70}:
  find <field> <value>        records whose field equals value
  range <field> <low> <high>  records whose field is between low and high (- for no limit)
  sort <field> [asc|desc]     records that have field, sorted by it
  index <field> [hash|btree]  index a field so find (hash) or find, range and sort (btree) are fast
  unindex <field>             remove a field's index
  indexes                     list indexes

SQL (end each statement with ;):
  CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, age INT);
  INSERT INTO clients VALUES (1, 'Ada', 70), (2, 'Bob', 66);
  SELECT name, age FROM clients WHERE age >= 65 ORDER BY age DESC LIMIT 10;
  UPDATE clients SET age = 71 WHERE id = 1;
  DELETE FROM clients WHERE id = 2;
  CREATE INDEX ON clients (age);            -- btree; add USING HASH for a hash index
  SHOW TABLES;  DESCRIBE clients;  DROP TABLE clients;
  Types: INT, FLOAT, TEXT, BOOL, DATE ('YYYY-MM-DD')

HTTP API (Phase 6):
  serve [--port 8000] [--host 127.0.0.1]     run the API on the --dir folder
  apikey create NAME [--role read|write]     make an API key (shown once)
  apikey list                                list keys (ids, roles, names)
  apikey revoke ID                           delete a key so it stops working
  Encrypt a new --dir folder: export TINYDB_PASSPHRASE='a long passphrase' first

  help                show this message
  exit                quit the interactive prompt"""

parser = QueryParser()

SQL_STARTS = ("CREATE", "DROP", "INSERT", "SELECT", "UPDATE", "SHOW", "DESCRIBE")


def format_value(value):
    """Show strings as-is and everything else as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def print_records(records, plan, out):
    """Print one record per line, then how many there were and how the search ran."""
    for record in records:
        print(f"{record.key}: {format_value(record.value)}", file=out)
    noun = "record" if len(records) == 1 else "records"
    print(f"({len(records)} {noun}, {plan})", file=out)


def is_sql(text):
    """True if text is SQL rather than a key-value command."""
    words = text.split()
    if not words:
        return False
    if text.rstrip().endswith(";") or words[0].upper() in SQL_STARTS:
        return True
    # "delete name" is the key-value command; "DELETE FROM clients" is SQL.
    return words[0].upper() == "DELETE" and len(words) > 1 and words[1].upper() == "FROM"


def format_cell(value):
    """How one value looks in a result table. Missing values show as NULL."""
    if value is None:
        return "NULL"
    return format_value(value)


def print_result(result, out):
    """Print a Result: a table of rows if it has columns, then the message and plan."""
    if not result.columns:
        print(result.message + (f" ({result.plan})" if result.plan else ""), file=out)
        return
    cells = [[format_cell(value) for value in row] for row in result.rows]
    widths = [max([len(name)] + [len(row[i]) for row in cells]) for i, name in enumerate(result.columns)]
    print(" | ".join(name.ljust(width) for name, width in zip(result.columns, widths)).rstrip(), file=out)
    print("-+-".join("-" * width for width in widths), file=out)
    for row in cells:
        print(" | ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip(), file=out)
    note = f"{result.message}, {result.plan}" if result.plan else result.message
    print(f"({note})", file=out)


def run_sql(db, text, out=None):
    """Run SQL text and print each Result. Returns an exit code: 0 ok, 2 error."""
    if out is None:
        out = sys.stdout
    try:
        for result in db.run(text):
            print_result(result, out)
    except (TypeError, ValueError) as e:
        # SqlError, SchemaError and DatabaseError are all ValueErrors.
        print(f"Error: {e}", file=out)
        return 2
    return 0


def execute(table, query, out=None):
    """Run one Query against a table. Returns an exit code: 0 ok, 1 not found, 2 error."""
    if out is None:
        out = sys.stdout
    command, key = query.command, query.key

    if command == "set":
        table.set(key, query.value)
        print("OK", file=out)
        return 0

    if command == "get":
        record = table.get(key)
        if record is None:
            print(f"Key not found: {key}", file=out)
            return 1
        print(format_value(record.value), file=out)
        return 0

    if command == "delete":
        if table.delete(key):
            print("Deleted", file=out)
            return 0
        print(f"Key not found: {key}", file=out)
        return 1

    if command == "exists":
        print("true" if table.exists(key) else "false", file=out)
        return 0

    if command == "keys":
        keys = table.keys()
        if not keys:
            print("(empty)", file=out)
        for k in keys:
            print(k, file=out)
        return 0

    if command == "count":
        print(len(table), file=out)
        return 0

    if command == "compact":
        before = table.log_entries
        table.compact()
        print(f"Compacted log from {before} to {table.log_entries} entries", file=out)
        return 0

    if command == "index":
        if table.create_index(query.field, query.kind):
            print(f"Created {query.kind} index on {query.field}", file=out)
        else:
            print(f"{query.field} already has a {query.kind} index", file=out)
        return 0

    if command == "unindex":
        if table.drop_index(query.field):
            print(f"Dropped index on {query.field}", file=out)
            return 0
        print(f"No index on {query.field}", file=out)
        return 1

    if command == "indexes":
        indexes = table.indexes()
        if not indexes:
            print("(no indexes)", file=out)
        for field, kind in indexes:
            print(f"{field} ({kind})", file=out)
        return 0

    if command == "find":
        print_records(table.find(query.field, query.value), table.last_plan, out)
        return 0

    if command == "range":
        print_records(table.find_range(query.field, query.low, query.high), table.last_plan, out)
        return 0

    if command == "sort":
        print_records(table.sort_by(query.field, query.descending), table.last_plan, out)
        return 0

    if command == "clear":
        table.clear()
        print("Cleared", file=out)
        return 0

    if command == "help":
        print(HELP_TEXT, file=out)
        return 0

    # "exit" only means something in the interactive prompt.
    return 0


def run_command(table, words, out=None):
    """Parse a list of words and run it. Returns the exit code."""
    if out is None:
        out = sys.stdout
    try:
        query = parser.parse(words)
        if query is None:
            return 0
        return execute(table, query, out)
    except (TypeError, ValueError) as e:
        # QueryError is a ValueError, so usage mistakes land here too.
        message = str(e) if isinstance(e, QueryError) else f"Error: {e}"
        print(message, file=out)
        return 2


def repl(table, directory):
    """Interactive prompt that reads commands until 'exit' or Ctrl-D.

    When input is piped in (python3 main.py < demo.sql) there are no prompts.
    """
    interactive = sys.stdin.isatty()
    if interactive:
        print("tinydb interactive shell. Type 'help' for commands, 'exit' to quit.")
    db = None  # the SQL Database is only opened once it's needed
    sql = ""  # SQL lines read so far, waiting for the ";" that ends the statement
    while True:
        prompt = ("   ...> " if sql else "tinydb> ") if interactive else ""
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            if interactive:
                print()
            break
        if not sql and line.strip().startswith("--"):
            continue  # a comment line
        if sql or is_sql(line):
            sql += line + "\n"
            if sql.rstrip().endswith(";"):
                db = db or open_database(directory)
                if db is not None:
                    run_sql(db, sql)
                sql = ""
            continue
        try:
            query = parser.parse_line(line)
        except QueryError as e:
            print(e)
            continue
        if query is None:
            continue
        if query.command == "exit":
            break
        try:
            execute(table, query)
        except (TypeError, ValueError) as e:
            print(f"Error: {e}")
    if sql.strip():
        db = db or open_database(directory)
        if db is not None:
            run_sql(db, sql)  # input ended without a final ";"


def open_database(directory):
    """Open the SQL Database, or print why it can't be opened and return None."""
    try:
        return Database(directory, passphrase=passphrase_for(directory))
    except (CorruptLogError, ValueError, KeyError, TypeError) as e:
        print(f"Error: can't open the database in {directory!r}: {e}", file=sys.stderr)
        return None


def main(argv=None):
    arg_parser = argparse.ArgumentParser(
        prog="tinydb",
        description="A tiny database.",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arg_parser.add_argument("--db", default="tinydb.jsonl", help="key-value log file (default: tinydb.jsonl)")
    arg_parser.add_argument("--dir", default="tinydb_data", help="folder for SQL tables (default: tinydb_data)")
    arg_parser.add_argument("command", nargs=argparse.REMAINDER, help="command or SQL to run; omit to open the interactive prompt")
    options = arg_parser.parse_args(argv)

    # The HTTP API commands hand the rest of the words to their own modules.
    first = options.command[0] if options.command else ""
    if first == "serve":
        return server.main(["--dir", options.dir] + options.command[1:])
    if first == "apikey":
        return auth.main(["--dir", options.dir] + options.command[1:])

    text = " ".join(options.command)
    if is_sql(text):
        db = open_database(options.dir)
        if db is None:
            return 2
        return run_sql(db, text)

    try:
        table = Table.open(options.db)
    except CorruptLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if not options.command:
        repl(table, options.dir)
        return 0
    return run_command(table, options.command)
