"""Unit tests for demo.py: the guided tour of the HTTP API runs, and every step gets the answer it should.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import io
import re
import unittest
from unittest import mock

from tinydb.demo import Demo, main

# The status of every request the tour sends, in order.
EXPECTED = [200, 204, 401, 200, 200, 403, 200, 200, 400, 200, 200, 200, 200, 400]


class DemoRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = io.StringIO()
        cls.code = main(["--no-pause", "--clients", "60"], out=out)
        cls.text = out.getvalue()

    def test_finishes(self):
        self.assertEqual(self.code, 0)
        self.assertIn("That's the tour", self.text)

    def test_every_answer_is_the_expected_status(self):
        statuses = [int(s) for s in re.findall(r"^< (\d{3}) ", self.text, re.MULTILINE)]
        self.assertEqual(statuses, EXPECTED)

    def test_twelve_steps(self):
        self.assertEqual(len(re.findall(r"^=== Step \d+: ", self.text, re.MULTILINE)), 12)

    def test_the_index_changes_the_plan(self):
        self.assertIn("btree index on deadline", self.text)

    def test_refusals_explain_themselves(self):
        for kind in ("Unauthorized:", "Forbidden:", "WrongMethod:", "SchemaError:"):
            self.assertIn(kind, self.text)

    def test_keys_are_never_printed(self):
        self.assertNotRegex(self.text, r"tdb_[A-Za-z0-9_-]{10,}")
        self.assertIn("Bearer tdb_... (read key)", self.text)

    def test_placeholder_values_survive_quotes(self):
        self.assertIn("Nell O'Hara", self.text)
        self.assertIn("(0 rows, full scan)", self.text)  # the injection attempt matched nobody


class DemoPartsTest(unittest.TestCase):
    def test_pauses_between_steps_only(self):
        demo = Demo(port=0, keys={}, out=io.StringIO(), pause=True)
        with mock.patch("builtins.input", return_value="") as fake_input:
            for number in range(3):
                demo.title(f"step {number}", "")
        self.assertEqual(fake_input.call_count, 2)  # not before the first step

    def test_show_error(self):
        out = io.StringIO()
        Demo(0, {}, out, False).show({"error": {"type": "Forbidden", "message": "read-only"}})
        self.assertEqual(out.getvalue(), "  Forbidden: read-only\n")

    def test_show_results_as_a_table(self):
        out = io.StringIO()
        Demo(0, {}, out, False).show({"results": [
            {"message": "1 row", "columns": ["id", "name"], "rows": [[1, "Ada"]], "plan": "primary key lookup"}]})
        self.assertEqual(out.getvalue().splitlines(), ["id | name", "---+-----", "1  | Ada",
                                                       "(1 row, primary key lookup)"])

    def test_refuses_zero_clients(self):
        out = io.StringIO()
        self.assertEqual(main(["--no-pause", "--clients", "0"], out=out), 1)


if __name__ == "__main__":
    unittest.main()
