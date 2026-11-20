"""Unit tests for bench.py: the timing helpers, and each benchmark on tiny data.

These check that the benchmarks measure the right things, not how fast your
computer is. Real numbers come from: python3 -m tinydb.bench

KeepAliveTest is the one exception. It guards the Nagle fix in server.py,
which the HTTP benchmark found, and the gap it checks is about 100x.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import gc
import io
import os
import tempfile
import threading
import time
import unittest

from tinydb.auth import KeyStore
from tinydb.bench import (
    Client,
    Measurement,
    bench_encryption,
    bench_http,
    bench_inserts,
    bench_lookups,
    bench_ranges,
    bench_startup,
    human_size,
    human_time,
    main,
    measure,
    run,
    table,
    timed,
)
from tinydb.database import Database
from tinydb.sample import load
from tinydb.server import ApiHandler, ApiServer


class MeasurementTest(unittest.TestCase):
    def test_median_and_rates(self):
        m = Measurement("x", operations=10, seconds=(3.0, 1.0, 2.0))
        self.assertEqual(m.median, 2.0)  # the middle run, not the average
        self.assertEqual(m.per_operation, 0.2)
        self.assertEqual(m.per_second, 5.0)

    def test_median_ignores_one_slow_run(self):
        m = Measurement("x", operations=1, seconds=(1.0, 1.0, 1.0, 1.0, 50.0))
        self.assertEqual(m.median, 1.0)

    def test_zero_time_does_not_divide_by_zero(self):
        self.assertEqual(Measurement("x", 1, (0.0,)).per_second, float("inf"))


class MeasureTest(unittest.TestCase):
    def test_runs_setup_before_every_run(self):
        calls = []
        m = measure("x", lambda: calls.append("work"), repeat=3, setup=lambda: calls.append("setup"))
        self.assertEqual(calls, ["setup", "work"] * 3)
        self.assertEqual(len(m.seconds), 3)
        self.assertTrue(all(seconds >= 0 for seconds in m.seconds))


class TimedTest(unittest.TestCase):
    def test_garbage_collector_is_paused_then_restored(self):
        seen = []
        timed(lambda: seen.append(gc.isenabled()))
        self.assertEqual(seen, [False])
        self.assertTrue(gc.isenabled())

    def test_restored_even_if_work_fails(self):
        def broken():
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            timed(broken)
        self.assertTrue(gc.isenabled())


class FormatTest(unittest.TestCase):
    def test_human_time(self):
        self.assertEqual(human_time(0.0000042), "4.2 µs")
        self.assertEqual(human_time(0.0153), "15.3 ms")
        self.assertEqual(human_time(2.5), "2.50 s")

    def test_human_size(self):
        self.assertEqual(human_size(512), "512 bytes")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertEqual(human_size(3 * 1024 * 1024), "3.0 MB")

    def test_table_is_markdown(self):
        text = table([Measurement("SELECT", 2, (0.004,), "full scan")])
        lines = text.splitlines()
        self.assertEqual(lines[0], "| Benchmark | Median | Per operation | Per second | Notes |")
        self.assertEqual(lines[2], "| SELECT | 4.0 ms | 2.0 ms | 500 | full scan |")


class BenchmarkTest(unittest.TestCase):
    """Every benchmark, on data small enough to run in well under a second."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        self.folder = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def loaded(self, clients=60):
        directory = os.path.join(self.folder, "data")
        load(Database(directory), clients)
        return Database(directory)

    def test_inserts_count_fsyncs(self):
        single, batched = bench_inserts(self.folder, rows=20, repeat=1)
        self.assertEqual(single.note, "20 rows, 20 fsyncs")
        self.assertEqual(batched.note, "20 rows, 1 fsync")
        self.assertEqual(single.operations, 20)

    def test_lookups_use_each_plan(self):
        notes = [m.note for m in bench_lookups(self.loaded(), 60, queries=3, repeat=1)]
        self.assertEqual(notes, ["primary key lookup", "full scan",
                                 "hash index on birth_date", "btree index on birth_date"])

    def test_lookups_leave_no_index_behind(self):
        db = self.loaded()
        bench_lookups(db, 60, queries=2, repeat=1)
        self.assertEqual(db.table("clients").indexes(), [])

    def test_ranges_with_and_without_index(self):
        db = self.loaded()
        without, with_index = bench_ranges(db, queries=2, repeat=1)
        self.assertTrue(without.note.endswith("full scan"))
        self.assertTrue(with_index.note.endswith("btree index on deadline"))
        self.assertEqual(without.note.split(",")[0], with_index.note.split(",")[0])  # same rows either way
        self.assertEqual(db.table("enrollment_periods").indexes(), [])

    def test_startup_log_grows_then_shrinks(self):
        names = [m.name for m in bench_startup(self.folder, 40, repeat=1)]
        self.assertEqual(names, ["Open (replay), fresh", "Open (replay), after 3 full updates",
                                 "Compact all tables", "Open (replay), after compaction"])

    def test_http_benchmarks(self):
        db = self.loaded()
        results = bench_http(db, 60, queries=8, threads=2, repeat=1)
        self.assertEqual([m.name for m in results], [
            "SELECT by primary key, in-process",
            "QUERY /query, new connection each",
            "QUERY /query, one kept-alive connection",
            "QUERY /query, 2 clients at once",
        ])
        self.assertEqual(results[1].note, "1 row per query")
        self.assertEqual(results[3].operations, 8)
        self.assertEqual(len(KeyStore.in_folder(db.directory)), 0)  # its key is revoked afterwards

    def test_encryption_benchmarks(self):
        results = bench_encryption(self.folder, 30, queries=3, repeat=1, iterations=1000)
        self.assertEqual([m.name for m in results], [
            "Derive key (PBKDF2, 1,000 rounds)",
            "Load 30 clients, plain", "Load 30 clients, encrypted",
            "Open (replay), plain", "Open (replay), encrypted",
            "SELECT by primary key, plain", "SELECT by primary key, encrypted",
        ])
        self.assertTrue(Database(os.path.join(self.folder, "plain")).table("clients"))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "encrypted", "_crypto.json")))

    def test_run_prints_a_table(self):
        out = io.StringIO()
        run(30, 1, out, iterations=1000)
        text = out.getvalue()
        self.assertIn("30 clients", text)
        self.assertIn("| INSERT, 1 row each |", text)
        self.assertIn("| Open (replay), after compaction |", text)
        self.assertIn("| QUERY /query, one kept-alive connection |", text)
        self.assertIn("| SELECT by primary key, encrypted |", text)

    def test_main_refuses_tiny_sizes(self):
        out = io.StringIO()
        self.assertEqual(main(["--clients", "5"], out=out), 1)
        self.assertIn("at least 10", out.getvalue())


class KeepAliveTest(unittest.TestCase):
    """Guards the Nagle fix in server.py. Without it, each kept-alive QUERY waits about 40 ms."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="tinydb-test-")
        db = Database(os.path.join(self.tmpdir.name, "data"))
        db.run("CREATE TABLE t (id INT PRIMARY KEY); INSERT INTO t VALUES (1);")
        keys = KeyStore.in_folder(db.directory)
        self.key = keys.create("test", "read")[1]
        self.server = ApiServer(db, keys, port=0, quiet=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmpdir.cleanup()

    def test_handler_turns_nagle_off(self):
        self.assertTrue(ApiHandler.disable_nagle_algorithm)

    def test_kept_alive_queries_are_not_delayed(self):
        client = Client(self.server.server_address[1], self.key)
        client.query("SELECT * FROM t;")  # the first one opens the connection
        start = time.perf_counter()
        for _ in range(20):
            client.query("SELECT * FROM t;")
        elapsed = time.perf_counter() - start
        client.close()
        self.assertLess(elapsed, 0.4)  # about 0.01 s with the fix, about 0.8 s without

    def test_client_refuses_an_error_reply(self):
        client = Client(self.server.server_address[1], "tdb_wrong")
        with self.assertRaises(RuntimeError) as caught:
            client.query("SELECT * FROM t;")
        self.assertIn("401", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
