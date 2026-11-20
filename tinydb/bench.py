"""Benchmarks: how fast tinydb really is, measured instead of guessed.

    python3 -m tinydb.bench                  # 10,000 clients, 5 runs each
    python3 -m tinydb.bench --clients 1000 --repeat 3

Each benchmark builds its data with sample.py in a temporary folder, runs the
same work several times, and reports the median. Everything goes through
Database.run, the same path the shell and the HTTP API use, so the numbers
include parsing and planning, not just the data structures.

Lesson 4 adds the layers on top: a real ApiServer on a free port, timed with
real QUERY requests, and an encrypted database next to a plain one.

The output is a Markdown table, ready to paste into docs/benchmarks.md.
"""

import argparse
import gc
import http.client
import json
import os
import platform
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Tuple

from tinydb.auth import KeyStore
from tinydb.crypto import ITERATIONS, derive_keys, make_salt
from tinydb.database import Database, plural
from tinydb.sample import SCHEMA, generate, insert_statements, load
from tinydb.server import SQL_TYPE, ApiServer

PASSPHRASE = "only for benchmarks, not a secret"


@dataclass(frozen=True)
class Measurement:
    """How long one benchmark took. seconds holds every run; operations is the work done per run."""

    name: str
    operations: int
    seconds: Tuple[float, ...]
    note: str = ""  # the query plan, a file size, anything that explains the number

    @property
    def median(self):
        return statistics.median(self.seconds)

    @property
    def per_operation(self):
        return self.median / self.operations

    @property
    def per_second(self):
        return self.operations / self.median if self.median > 0 else float("inf")


def timed(work):
    """Run work() once and return how many seconds it took.

    The garbage collector is paused while it runs, like the timeit module does,
    so a collection of objects some earlier benchmark left behind doesn't land
    in this one's time.
    """
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        start = time.perf_counter()
        work()
        return time.perf_counter() - start
    finally:
        if was_enabled:
            gc.enable()


def measure(name, work, operations=1, repeat=5, setup=None, note=""):
    """Time work() repeat times. setup() runs before each one, and isn't counted."""
    runs = []
    for _ in range(repeat):
        if setup is not None:
            setup()
        runs.append(timed(work))
    return Measurement(name, operations, tuple(runs), note)


def human_time(seconds):
    """0.0000042 -> '4.2 µs', 0.0153 -> '15.3 ms', 2.5 -> '2.50 s'."""
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.1f} µs"
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


def human_size(size):
    """1536 -> '1.5 KB', 2_400_000 -> '2.3 MB'."""
    for unit in ("bytes", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""  # never reached, but keeps type checkers happy


def folder_size(directory):
    return sum(os.path.getsize(os.path.join(directory, name)) for name in os.listdir(directory))


def table(measurements):
    """Format measurements as a Markdown table."""
    lines = ["| Benchmark | Median | Per operation | Per second | Notes |",
             "|---|---:|---:|---:|---|"]
    for m in measurements:
        lines.append(f"| {m.name} | {human_time(m.median)} | {human_time(m.per_operation)} "
                     f"| {m.per_second:,.0f} | {m.note} |")
    return "\n".join(lines)


# ---- the benchmarks ----

def bench_inserts(folder, rows=500, repeat=3):
    """The same rows saved with one INSERT each, then 500 per INSERT. Each INSERT is one fsync."""
    data = generate(rows)["clients"][:rows]
    directory = os.path.join(folder, "inserts")
    state = {}

    def fresh():
        shutil.rmtree(directory, ignore_errors=True)
        state["db"] = Database(directory)
        state["db"].run(SCHEMA)

    def run_all(statements):
        return lambda: [state["db"].run(statement) for statement in statements]

    one_each = insert_statements("clients", data, batch=1)
    batched = insert_statements("clients", data, batch=500)
    return [
        measure("INSERT, 1 row each", run_all(one_each), rows, repeat, fresh,
                f"{rows} rows, {plural(len(one_each), 'fsync')}"),
        measure("INSERT, 500 rows each", run_all(batched), rows, repeat, fresh,
                f"{rows} rows, {plural(len(batched), 'fsync')}"),
    ]


def bench_lookups(db, clients, queries=50, repeat=5):
    """Find clients by birth date: full scan, then hash index, then B-tree index."""
    rng = random.Random(1)
    births = [row[2] for row in generate(clients)["clients"]]  # same seed as load, so these dates exist
    sql = [f"SELECT id, name FROM clients WHERE birth_date = '{rng.choice(births)}';" for _ in range(queries)]
    ids = [f"SELECT id, name FROM clients WHERE id = {rng.randint(1, clients)};" for _ in range(queries)]
    clients_table = db.table("clients")

    def run_all(statements):
        return lambda: [db.run(statement) for statement in statements]

    def plan(statements):
        return db.run(statements[0])[0].plan

    results = [measure("SELECT by primary key", run_all(ids), queries, repeat, note=plan(ids))]
    results.append(measure("SELECT by birth_date, no index", run_all(sql), queries, repeat, note=plan(sql)))
    db.run("CREATE INDEX ON clients USING HASH (birth_date);")
    results.append(measure("SELECT by birth_date, hash index", run_all(sql), queries, repeat, note=plan(sql)))
    db.run("CREATE INDEX ON clients (birth_date);")  # a different kind replaces the hash index
    results.append(measure("SELECT by birth_date, B-tree index", run_all(sql), queries, repeat, note=plan(sql)))
    clients_table.drop_index("birth_date")
    return results


def bench_ranges(db, queries=20, repeat=5):
    """SEP deadlines in a one-week window: full scan, then a B-tree index on deadline."""
    rng = random.Random(2)
    sql = []
    for _ in range(queries):
        day = rng.randint(1, 21)
        sql.append("SELECT client_id, deadline FROM enrollment_periods "
                   f"WHERE deadline >= '2026-11-{day:02d}' AND deadline <= '2026-11-{day + 7:02d}';")
    periods = db.table("enrollment_periods")

    def run_all():
        for statement in sql:
            db.run(statement)

    def note():
        result = db.run(sql[0])[0]
        return f"{result.message}, {result.plan}"

    results = [measure("Range on deadline, no index", run_all, queries, repeat, note=note())]
    db.run("CREATE INDEX ON enrollment_periods (deadline);")
    results.append(measure("Range on deadline, B-tree index", run_all, queries, repeat, note=note()))
    periods.drop_index("deadline")
    return results


def bench_startup(folder, clients, repeat=3):
    """Open time (replaying every log) for fresh data, after 3 rounds of updates, then after compaction."""
    directory = os.path.join(folder, "startup")
    shutil.rmtree(directory, ignore_errors=True)
    load(Database(directory), clients)

    def open_db():
        Database(directory)

    results = [measure("Open (replay), fresh", open_db, 1, repeat, note=human_size(folder_size(directory)))]
    db = Database(directory)
    for round_number in range(3):  # every UPDATE appends a new copy of every client row
        db.run(f"UPDATE clients SET zip = '3310{round_number}';")
    results.append(measure("Open (replay), after 3 full updates", open_db, 1, repeat,
                           note=human_size(folder_size(directory))))
    results.append(measure("Compact all tables", lambda: [db.table(name).compact() for name in db.table_names()],
                           repeat=1))
    results.append(measure("Open (replay), after compaction", open_db, 1, repeat,
                           note=human_size(folder_size(directory))))
    return results


class Client:
    """Sends QUERY requests to a server, on one kept-alive connection or a new one each time."""

    def __init__(self, port, key, keep_alive=True):
        self.port = port
        self.headers = {"Authorization": "Bearer " + key, "Content-Type": SQL_TYPE}
        self.keep_alive = keep_alive
        self.connection = None

    def query(self, sql):
        """Send one QUERY /query and return the parsed JSON. Raises unless the answer is 200 OK."""
        if self.connection is None:
            self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.connection.request("QUERY", "/query", body=sql.encode("utf-8"), headers=self.headers)
        response = self.connection.getresponse()
        body = response.read()  # read it all, or the connection can't be used again
        if not self.keep_alive:
            self.close()
        if response.status != 200:
            raise RuntimeError(f"QUERY returned {response.status}: {body[:200]!r}")
        return json.loads(body)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None


def send_all(port, key, statements, keep_alive=True):
    """Send every statement as a QUERY, one after another, from one Client."""
    client = Client(port, key, keep_alive)
    try:
        for statement in statements:
            client.query(statement)
    finally:
        client.close()


def bench_http(db, clients, queries=200, threads=4, repeat=5):
    """The same primary key SELECT in-process, then over HTTP: new connections, kept alive, and from several threads."""
    rng = random.Random(3)
    sql = [f"SELECT id, name FROM clients WHERE id = {rng.randint(1, clients)};" for _ in range(queries)]
    keys = KeyStore.in_folder(db.directory)
    api_key, key = keys.create("benchmark", "read")
    server = ApiServer(db, keys, port=0, quiet=True)  # port 0: the operating system picks a free one
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        def in_process():
            for statement in sql:
                db.run(statement)

        def over_http(keep_alive):
            return lambda: send_all(port, key, sql, keep_alive)

        def in_parallel():
            share = queries // threads  # each thread sends its own slice of the queries
            workers = [threading.Thread(target=send_all, args=(port, key, sql[i * share:(i + 1) * share]))
                       for i in range(threads)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

        rows = len(Client(port, key, keep_alive=False).query(sql[0])["results"][0]["rows"])
        note = f"{plural(rows, 'row')} per query"
        return [
            measure("SELECT by primary key, in-process", in_process, queries, repeat, note=note),
            measure("QUERY /query, new connection each", over_http(False), queries, repeat, note=note),
            measure("QUERY /query, one kept-alive connection", over_http(True), queries, repeat, note=note),
            measure(f"QUERY /query, {threads} clients at once", in_parallel, threads * (queries // threads),
                    repeat, note=f"{threads} threads, one database lock"),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        keys.revoke(api_key.id)


def bench_encryption(folder, clients, queries=50, repeat=3, iterations=ITERATIONS):
    """The same data plain and encrypted: deriving the key, loading, opening, and reading.

    iterations is the PBKDF2 rounds for the new encrypted folder. The tests pass a small number.
    """
    rng = random.Random(4)
    sql = [f"SELECT id, name FROM clients WHERE id = {rng.randint(1, clients)};" for _ in range(queries)]
    salt = make_salt()
    loads, opens, reads = [], [], []
    for name, passphrase in (("plain", None), ("encrypted", PASSPHRASE)):
        directory = os.path.join(folder, name)
        state = {}

        def fresh():
            shutil.rmtree(directory, ignore_errors=True)
            state["db"] = Database(directory, passphrase=passphrase, iterations=iterations)  # not timed

        loads.append(measure(f"Load {clients:,} clients, {name}", lambda: load(state["db"], clients),
                             1, repeat, fresh))
        opens.append(measure(f"Open (replay), {name}", lambda: Database(directory, passphrase=passphrase),
                             1, repeat, note=human_size(folder_size(directory))))
        db = Database(directory, passphrase=passphrase)
        reads.append(measure(f"SELECT by primary key, {name}", lambda: [db.run(s) for s in sql], queries, repeat))
    derive = measure(f"Derive key (PBKDF2, {iterations:,} rounds)",
                     lambda: derive_keys(PASSPHRASE, salt, iterations), 1, repeat)
    return [derive] + loads + opens + reads


def run(clients=10_000, repeat=5, out=None, iterations=ITERATIONS):
    """Run every benchmark and print the results. Returns the list of Measurements."""
    out = out or sys.stdout
    print(f"tinydb benchmarks: {clients:,} clients, median of {repeat} runs", file=out)
    print(f"Python {platform.python_version()} on {platform.platform(terse=True)}\n", file=out)
    measurements = []
    with tempfile.TemporaryDirectory(prefix="tinydb-bench-") as folder:
        measurements += bench_inserts(folder, rows=min(clients, 500), repeat=min(repeat, 3))
        directory = os.path.join(folder, "data")
        load(Database(directory), clients)
        db = Database(directory)
        measurements += bench_lookups(db, clients, repeat=repeat)
        measurements += bench_ranges(db, repeat=repeat)
        measurements += bench_startup(folder, clients, repeat=min(repeat, 3))
        measurements += bench_http(db, clients, repeat=repeat)
        measurements += bench_encryption(folder, clients, repeat=min(repeat, 3), iterations=iterations)
    print(table(measurements), file=out)
    return measurements


def main(argv=None, out=None):
    arg_parser = argparse.ArgumentParser(prog="tinydb.bench", description="Measure how fast tinydb is.")
    arg_parser.add_argument("--clients", type=int, default=10_000, help="data size (default: 10000)")
    arg_parser.add_argument("--repeat", type=int, default=5, help="runs per benchmark (default: 5)")
    options = arg_parser.parse_args(argv)
    if options.clients < 10 or options.repeat < 1:
        print("--clients must be at least 10 and --repeat at least 1", file=out or sys.stdout)
        return 1
    run(options.clients, options.repeat, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
