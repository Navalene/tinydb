"""A guided demo of tinydb's HTTP API, for a presentation or a screen recording.

    python3 -m tinydb.demo                 # press Enter between steps
    python3 -m tinydb.demo --no-pause      # run straight through

It builds a made-up Medicare book of business in a temporary folder, makes a
read key and a write key, starts the real server on a free port, and then
walks through the API one request at a time. Each step prints the exact HTTP
request it sends and the answer it gets. Nothing is mocked.

The questions are asked "as of" a fixed date, so the answers are the same
every time the demo runs, whatever today's date is.
"""

import argparse
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta

from tinydb.auth import KeyStore
from tinydb.cli import print_result
from tinydb.database import Database, Result
from tinydb.sample import load
from tinydb.server import JSON_TYPE, SQL_TYPE, ApiServer

AS_OF = date(2026, 11, 19)
SHOWN_HEADERS = ("Allow", "Accept-Query", "WWW-Authenticate")  # response headers worth pointing out

SEP_SQL = ("SELECT client_id, reason, deadline FROM enrollment_periods "
           "WHERE period = ? AND deadline >= ? AND deadline <= ? ORDER BY deadline LIMIT 5")


class Demo:
    """Sends requests to a running server and prints each one, with its answer."""

    def __init__(self, port, keys, out, pause):
        self.port = port
        self.keys = keys  # {"read": "tdb_...", "write": "tdb_..."}
        self.out = out
        self.pause = pause
        self.step = 0

    def say(self, text=""):
        print(text, file=self.out)

    def title(self, text, explanation):
        """Start a new step. Waits for Enter first, if pausing."""
        if self.pause and self.step:
            input("\nPress Enter for the next step...")
        self.step += 1
        self.say(f"\n=== Step {self.step}: {text} ===")
        self.say(explanation)

    def send(self, method, path, body=None, key=None):
        """Send one request, print it and the answer, and return the answer's JSON (or None)."""
        headers = {}
        if key is not None:
            headers["Authorization"] = f"Bearer {self.keys[key]}"
        data = None
        if body is not None:
            sql_only = isinstance(body, str)
            headers["Content-Type"] = SQL_TYPE if sql_only else JSON_TYPE
            data = (body if sql_only else json.dumps(body)).encode("utf-8")

        self.say(f"\n> {method} {path}")
        for name, value in headers.items():
            shown = f"Bearer tdb_... ({key} key)" if name == "Authorization" else value
            self.say(f"> {name}: {shown}")
        if data is not None:
            self.say(">")
            self.say("> " + data.decode("utf-8"))

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            start = time.perf_counter()
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            milliseconds = (time.perf_counter() - start) * 1000
        finally:
            connection.close()

        self.say(f"\n< {response.status} {response.reason}  ({milliseconds:.1f} ms)")
        for name in SHOWN_HEADERS:
            if response.getheader(name):
                self.say(f"< {name}: {response.getheader(name)}")
        reply = json.loads(raw) if raw else None
        self.show(reply)
        return reply

    def show(self, reply):
        """Print a JSON answer the way the shell prints results: tables for rows, messages otherwise."""
        if not reply:
            return
        if "error" in reply:
            self.say(f"  {reply['error']['type']}: {reply['error']['message']}")
            return
        if "results" not in reply:
            self.say("  " + json.dumps(reply))
            return
        for item in reply["results"]:
            result = Result(item["message"], tuple(item["columns"]),
                            tuple(tuple(row) for row in item["rows"]), item["plan"])
            print_result(result, self.out)


def run(demo, as_of=AS_OF):
    """The demo's steps, in order."""
    soon = (as_of + timedelta(days=30)).isoformat()
    sep_params = ["SEP", as_of.isoformat(), soon]

    demo.title("Is the server up?", "GET /health needs no key. It's the only GET, and it never touches data.")
    demo.send("GET", "/health")

    demo.title("What does /query accept?",
               "OPTIONS lists the methods. Accept-Query (RFC 10008) lists the body types QUERY takes.")
    demo.send("OPTIONS", "/query")

    demo.title("No key, no data", "Every request that touches data needs an API key.")
    demo.send("QUERY", "/query", "SHOW TABLES")

    demo.title("A read with the read key", "QUERY carries the SQL in its body. It's safe and idempotent, like GET.")
    demo.send("QUERY", "/query", "SHOW TABLES; SELECT code, name, monthly_premium FROM plans ORDER BY monthly_premium",
              key="read")

    demo.title(f"The 5 soonest SEP deadlines in the next 30 days (as of {as_of.isoformat()})",
               "The values go in params, one per ?. They're never part of the SQL text. No index yet: full scan.")
    demo.send("QUERY", "/query", {"sql": SEP_SQL, "params": sep_params}, key="read")

    demo.title("The read key can't write", "Writes go to POST /execute, and only a write key may use it.")
    demo.send("POST", "/execute", "CREATE INDEX ON enrollment_periods (deadline)", key="read")

    demo.title("The write key adds a B-tree index", "Same request, with the write key.")
    demo.send("POST", "/execute", "CREATE INDEX ON enrollment_periods (deadline)", key="write")

    demo.title("The same question again", "Same answer. The plan now says btree index on deadline.")
    demo.send("QUERY", "/query", {"sql": SEP_SQL, "params": sep_params}, key="read")

    demo.title("QUERY can't change data",
               "A DELETE hidden in a QUERY body is refused before anything runs, even the SELECT in front of it.")
    demo.send("QUERY", "/query", "SELECT id FROM plans; DELETE FROM clients", key="write")

    attack = "x'; DROP TABLE clients; --"
    demo.title("An injection attempt goes nowhere",
               f"The name is {attack!r}. As a ? value it's just text to compare, so it matches nobody.")
    demo.send("QUERY", "/query", {"sql": "SELECT id, name FROM clients WHERE name = ?", "params": [attack]},
              key="read")
    demo.send("QUERY", "/query", "SHOW TABLES", key="read")

    demo.title("A new client enrolls", "One POST, two statements: insert her, then record her enrollment period.")
    demo.send("POST", "/execute", {
        "sql": "INSERT INTO clients VALUES (?, ?, ?, ?, ?, ?); "
               "INSERT INTO enrollment_periods VALUES (?, ?, ?, ?, ?, ?)",
        "params": [100001, "Nell O'Hara", "1961-11-30", "33101", None, False,
                   100001, 100001, "IEP", "Turning 65", "2026-08-01", "2027-02-28"],
    }, key="write")
    demo.send("QUERY", "/query", {"sql": "SELECT id, name, birth_date, plan_code FROM clients WHERE id = ?",
                                  "params": [100001]}, key="read")

    demo.title("Bad data is stopped at the door", "The schema checks every value: a DATE must be YYYY-MM-DD.")
    demo.send("POST", "/execute", {"sql": "UPDATE clients SET birth_date = ? WHERE id = ?",
                                   "params": ["11/30/1961", 100001]}, key="write")

    demo.say("\nThat's the tour: QUERY for reads, POST for writes, keys with roles, ? placeholders, "
             "and an index the planner picks up on its own.")


def main(argv=None, out=None):
    out = out or sys.stdout
    arg_parser = argparse.ArgumentParser(prog="tinydb.demo", description="A guided tour of the tinydb HTTP API.")
    arg_parser.add_argument("--clients", type=int, default=2000, help="how many made-up clients (default: 2000)")
    arg_parser.add_argument("--no-pause", action="store_true", help="don't wait for Enter between steps")
    options = arg_parser.parse_args(argv)
    if options.clients < 1:
        print("--clients must be at least 1", file=out)
        return 1

    with tempfile.TemporaryDirectory(prefix="tinydb-demo-") as folder:
        directory = os.path.join(folder, "medicare")
        counts = load(Database(directory), options.clients)
        database = Database(directory)
        store = KeyStore.in_folder(directory)
        keys = {role: store.create(f"demo {role}", role)[1] for role in ("read", "write")}
        server = ApiServer(database, store, port=0, quiet=True)  # port 0: any free port
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            print(f"tinydb demo: {counts['clients']:,} clients, {counts['enrollment_periods']:,} enrollment periods, "
                  f"{counts['plans']} plans (all made up)", file=out)
            print(f"Server on http://127.0.0.1:{server.server_address[1]} with a read key and a write key", file=out)
            pause = not options.no_pause and sys.stdin.isatty()
            run(Demo(server.server_address[1], keys, out, pause))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
