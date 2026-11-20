"""Sample data: a made-up Medicare book of business, as big as you ask for.

Phase 7 needs the same kind of data three times. The benchmarks need thousands
of rows, the demo needs a realistic folder to serve over HTTP, and the tests
need small rows they can check. This module makes all three from a seed, so
every run on every machine gets exactly the same rows.

    python3 -m tinydb.sample --dir sample_data --clients 1000

All names and data are made up.
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta

from tinydb.database import Database

SCHEMA = """
CREATE TABLE plans (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    monthly_premium FLOAT NOT NULL,
    star_rating FLOAT
);
CREATE TABLE clients (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date DATE NOT NULL,
    zip TEXT,
    plan_code TEXT,
    medicaid BOOL
);
CREATE TABLE enrollment_periods (
    id INT PRIMARY KEY,
    client_id INT NOT NULL,
    period TEXT NOT NULL,
    reason TEXT,
    starts DATE NOT NULL,
    deadline DATE NOT NULL
);
"""

PLANS = (
    ("H1001-001", "Sunrise Advantage PPO", "MAPD", 0.0, 4.5),
    ("H1001-002", "Sunrise Advantage HMO", "MAPD", 29.5, 4.0),
    ("H2040-003", "Coastal Care HMO", "MAPD", 0.0, 3.5),
    ("H3300-007", "Bayview Dual Plan", "MAPD", 0.0, 4.0),
    ("S2002-010", "Harbor Rx Basic", "PDP", 12.4, 3.5),
    ("S2002-011", "Harbor Rx Plus", "PDP", 48.9, 4.5),
    ("MS-G", "Plan G Supplement", "Supplement", 148.0, None),
    ("MS-N", "Plan N Supplement", "Supplement", 109.0, None),
)

FIRST_NAMES = ("Ada", "Bob", "Cy", "Di", "Eve", "Fay", "Gus", "Hal", "Ida", "Jo",
               "Kay", "Lou", "Mae", "Ned", "Ora", "Pat", "Rae", "Sal", "Tess", "Val")
LAST_NAMES = ("Reyes", "Okafor", "Lindqvist", "Patel", "O'Brien", "Nguyen", "Garcia",
              "Kowalski", "Haddad", "Ito", "Mensah", "Silva", "D'Angelo", "Chen", "Brooks")
ZIPS = ("33101", "33125", "33130", "33139", "33142", "33150", "33161", "33176")

# (period, reason, length in days). Reasons are what an agent would write down.
PERIODS = (
    ("IEP", "Turning 65", 210),
    ("AEP", None, 53),
    ("OEP", None, 90),
    ("SEP", "Has Medicaid", 75),
    ("SEP", "Moved to a new ZIP code", 60),
    ("SEP", "Lost employer coverage", 60),
)

YEAR_START = date(2026, 1, 1)


def sql_value(value):
    """Write one Python value as SQL text: 'O''Brien', 42, 4.5, TRUE, NULL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # before int, because True is also an int
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def generate(clients=100, seed=7):
    """Return {"plans": rows, "clients": rows, "enrollment_periods": rows}.

    Each row is a tuple in the table's column order. The same clients and seed
    always give the same rows.
    """
    rng = random.Random(seed)  # its own generator, so nothing else changes the sequence
    client_rows = []
    period_rows = []
    for client_id in range(1, clients + 1):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        birth = date(rng.randint(1940, 1961), rng.randint(1, 12), rng.randint(1, 28))
        plan = None if rng.random() < 0.1 else rng.choice(PLANS)[0]  # 1 in 10 hasn't picked a plan
        medicaid = rng.random() < 0.2
        client_rows.append((client_id, name, birth.isoformat(), rng.choice(ZIPS), plan, medicaid))

        for _ in range(rng.randint(1, 2)):  # one or two enrollment periods per client
            period, reason, days = rng.choice(PERIODS)
            starts = YEAR_START + timedelta(days=rng.randrange(365))
            deadline = starts + timedelta(days=days)
            period_rows.append((len(period_rows) + 1, client_id, period, reason,
                                starts.isoformat(), deadline.isoformat()))
    return {"plans": list(PLANS), "clients": client_rows, "enrollment_periods": period_rows}


def insert_statements(table, rows, batch=500):
    """Turn rows into INSERT statements of up to batch rows each.

    One INSERT with many rows is one transaction, so it costs one fsync instead
    of one per row. The benchmarks show how much that matters.
    """
    statements = []
    for start in range(0, len(rows), batch):
        values = ",\n    ".join("(" + ", ".join(sql_value(v) for v in row) + ")"
                                for row in rows[start:start + batch])
        statements.append(f"INSERT INTO {table} VALUES\n    {values};")
    return statements


def load(database, clients=100, seed=7, batch=500):
    """Create the three tables in database and fill them. Returns {table: row count}."""
    database.run(SCHEMA)
    counts = {}
    for table, rows in generate(clients, seed).items():
        for statement in insert_statements(table, rows, batch):
            database.run(statement)
        counts[table] = len(rows)
    return counts


def main(argv=None, out=None):
    out = out or sys.stdout
    arg_parser = argparse.ArgumentParser(prog="tinydb.sample",
                                         description="Fill a new folder with made-up Medicare data.")
    arg_parser.add_argument("--dir", default="sample_data", help="a new database folder (default: sample_data)")
    arg_parser.add_argument("--clients", type=int, default=1000, help="how many clients (default: 1000)")
    arg_parser.add_argument("--seed", type=int, default=7, help="same seed, same data (default: 7)")
    options = arg_parser.parse_args(argv)

    if options.clients < 1:
        print("--clients must be at least 1", file=out)
        return 1
    if os.path.exists(options.dir) and os.listdir(options.dir):
        print(f"{options.dir} isn't empty. Pick a new folder, or delete it first", file=out)
        return 1
    counts = load(Database(options.dir), options.clients, options.seed)
    for table, count in counts.items():
        print(f"{table:<20} {count:>7} rows", file=out)
    print(f"Saved in {options.dir}/", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
