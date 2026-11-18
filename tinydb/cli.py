"""Command-line interface for tinydb.

Two ways to use it:
    One-shot:     python3 main.py set name Ada
    Interactive:  python3 main.py        (opens a tinydb> prompt)

Each command goes through two steps: QueryParser turns the words into a Query,
then execute() runs that Query against a Table.
"""

import argparse
import json
import sys

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
  help                show this message
  exit                quit the interactive prompt"""

parser = QueryParser()


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


def repl(table):
    """Interactive prompt that reads commands until 'exit' or Ctrl-D."""
    print("tinydb interactive shell. Type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            line = input("tinydb> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
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


def main(argv=None):
    arg_parser = argparse.ArgumentParser(
        prog="tinydb",
        description="A tiny database.",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    arg_parser.add_argument("--db", default="tinydb.jsonl", help="path to the database log file (default: tinydb.jsonl)")
    arg_parser.add_argument("command", nargs=argparse.REMAINDER, help="command to run; omit to open the interactive prompt")
    options = arg_parser.parse_args(argv)

    try:
        table = Table.open(options.db)
    except CorruptLogError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if not options.command:
        repl(table)
        return 0
    return run_command(table, options.command)
