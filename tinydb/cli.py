"""Command-line interface for the tinydb key-value store.
 
Two ways to use it:
    One-shot:     python3 main.py set name Ada
    Interactive:  python3 main.py        (opens a tinydb> prompt)
"""
 
import argparse
import json
import shlex
import sys
 
from tinydb.store import KeyValueStore
 
HELP_TEXT = """Commands:
  set <key> <value>   store a value (JSON like 42 or [1,2] is parsed, anything else is text)
  get <key>           print a value
  delete <key>        remove a key
  exists <key>        print true or false
  keys                list all keys
  count               print the number of keys
  clear               remove all keys
  compact             shrink the log file down to just the live keys
  help                show this message
  exit                quit the interactive prompt"""
 
 
def parse_value(raw):
    """Turn CLI text into a value: valid JSON becomes that type, otherwise keep it as a string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
 
 
def format_value(value):
    """Show strings as-is and everything else as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value)
 
 
def run_command(store, args, out=sys.stdout):
    """Run one command (a list of words). Returns an exit code: 0 ok, 1 not found, 2 usage error."""
    if not args:
        return 0
    command, rest = args[0].lower(), args[1:]
 
    try:
        if command == "set":
            if len(rest) < 2:
                print("Usage: set <key> <value>", file=out)
                return 2
            store.set(rest[0], parse_value(" ".join(rest[1:])))
            print("OK", file=out)
            return 0
 
        if command in ("get", "delete", "exists"):
            if len(rest) != 1:
                print(f"Usage: {command} <key>", file=out)
                return 2
            key = rest[0]
            if command == "get":
                if not store.exists(key):
                    print(f"Key not found: {key}", file=out)
                    return 1
                print(format_value(store.get(key)), file=out)
                return 0
            if command == "delete":
                if store.delete(key):
                    print("Deleted", file=out)
                    return 0
                print(f"Key not found: {key}", file=out)
                return 1
            print("true" if store.exists(key) else "false", file=out)
            return 0
 
        if command == "keys":
            keys = store.keys()
            if not keys:
                print("(empty)", file=out)
            for key in keys:
                print(key, file=out)
            return 0
 
        if command == "count":
            print(len(store), file=out)
            return 0
 
        if command == "compact":
            before = store.log_entries
            store.compact()
            print(f"Compacted log from {before} to {store.log_entries} entries", file=out)
            return 0
 
        if command == "clear":
            store.clear()
            print("Cleared", file=out)
            return 0
 
        if command == "help":
            print(HELP_TEXT, file=out)
            return 0
 
    except (TypeError, ValueError) as e:
        print(f"Error: {e}", file=out)
        return 2
 
    print(f"Unknown command: {command}. Type 'help' for a list of commands.", file=out)
    return 2
 
 
def repl(store):
    """Interactive prompt that reads commands until 'exit' or Ctrl-D."""
    print("tinydb interactive shell. Type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            line = input("tinydb> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            words = shlex.split(line)
        except ValueError as e:
            print(f"Error: {e}")
            continue
        if words and words[0].lower() in ("exit", "quit"):
            break
        run_command(store, words)
 
 
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="tinydb",
        description="A tiny key-value database.",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default="tinydb.jsonl", help="path to the database log file (default: tinydb.jsonl)")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command to run; omit to open the interactive prompt")
    options = parser.parse_args(argv)
 
    try:
        store = KeyValueStore(options.db)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
 
    if not options.command:
        repl(store)
        return 0
    return run_command(store, options.command)
 