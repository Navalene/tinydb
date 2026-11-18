"""Unit tests for QueryParser.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import unittest

from tinydb.query import Query, QueryError, QueryParser


class QueryParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = QueryParser()

    def test_set(self):
        self.assertEqual(self.parser.parse(["set", "age", "36"]), Query("set", key="age", value=36))

    def test_set_joins_multi_word_values(self):
        query = self.parser.parse(["set", "greeting", "hello", "world"])
        self.assertEqual(query, Query("set", key="greeting", value="hello world"))

    def test_key_commands(self):
        for command in ("get", "delete", "exists"):
            self.assertEqual(self.parser.parse([command, "name"]), Query(command, key="name"))

    def test_commands_without_arguments(self):
        for command in ("keys", "count", "clear", "compact", "indexes", "help", "exit"):
            self.assertEqual(self.parser.parse([command]), Query(command))

    def test_command_is_case_insensitive(self):
        self.assertEqual(self.parser.parse(["GET", "name"]), Query("get", key="name"))

    def test_quit_means_exit(self):
        self.assertEqual(self.parser.parse(["quit"]), Query("exit"))

    def test_blank_input_returns_none(self):
        self.assertIsNone(self.parser.parse([]))
        self.assertIsNone(self.parser.parse_line("   "))

    def test_unknown_command(self):
        with self.assertRaises(QueryError) as cm:
            self.parser.parse(["bogus"])
        self.assertIn("Unknown command: bogus", str(cm.exception))

    def test_wrong_number_of_arguments(self):
        cases = [["set", "only_key"], ["get"], ["get", "a", "b"], ["keys", "extra"]]
        for words in cases:
            with self.assertRaises(QueryError) as cm:
                self.parser.parse(words)
            self.assertTrue(str(cm.exception).startswith("Usage: "), words)

    def test_parse_line_handles_quotes(self):
        query = self.parser.parse_line("set plans '[\"Part A\", \"Part B\"]'")
        self.assertEqual(query, Query("set", key="plans", value=["Part A", "Part B"]))

    def test_parse_line_unclosed_quote(self):
        with self.assertRaises(QueryError):
            self.parser.parse_line('set name "Ada')

    def test_parse_value(self):
        self.assertEqual(QueryParser.parse_value("42"), 42)
        self.assertEqual(QueryParser.parse_value("true"), True)
        self.assertEqual(QueryParser.parse_value("[1, 2]"), [1, 2])
        self.assertEqual(QueryParser.parse_value("hello"), "hello")

    def test_query_error_is_a_value_error(self):
        self.assertTrue(issubclass(QueryError, ValueError))


    # ---- Phase 3: index and search commands ----

    def test_index_defaults_to_hash(self):
        self.assertEqual(self.parser.parse(["index", "plan"]), Query("index", field="plan", kind="hash"))

    def test_index_btree(self):
        query = self.parser.parse(["index", "age", "BTREE"])
        self.assertEqual(query, Query("index", field="age", kind="btree"))

    def test_index_unknown_kind(self):
        with self.assertRaises(QueryError) as cm:
            self.parser.parse(["index", "age", "bitmap"])
        self.assertIn("Unknown index kind: bitmap", str(cm.exception))

    def test_unindex(self):
        self.assertEqual(self.parser.parse(["unindex", "plan"]), Query("unindex", field="plan"))

    def test_find_parses_the_value(self):
        self.assertEqual(self.parser.parse(["find", "age", "70"]), Query("find", field="age", value=70))
        query = self.parser.parse(["find", "plan", "Medicare", "Advantage"])
        self.assertEqual(query, Query("find", field="plan", value="Medicare Advantage"))

    def test_range(self):
        query = self.parser.parse(["range", "age", "65", "70"])
        self.assertEqual(query, Query("range", field="age", low=65, high=70))

    def test_range_dash_means_no_limit(self):
        query = self.parser.parse(["range", "age", "65", "-"])
        self.assertEqual(query, Query("range", field="age", low=65, high=None))

    def test_sort(self):
        self.assertEqual(self.parser.parse(["sort", "age"]), Query("sort", field="age"))
        self.assertEqual(self.parser.parse(["sort", "age", "asc"]), Query("sort", field="age"))
        self.assertEqual(self.parser.parse(["sort", "age", "DESC"]), Query("sort", field="age", descending=True))

    def test_field_commands_wrong_arguments(self):
        cases = [
            ["index"], ["index", "a", "hash", "extra"], ["unindex"], ["unindex", "a", "b"],
            ["find"], ["find", "plan"], ["range", "age", "65"], ["sort"], ["sort", "age", "sideways"],
        ]
        for words in cases:
            with self.assertRaises(QueryError) as cm:
                self.parser.parse(words)
            self.assertTrue(str(cm.exception).startswith("Usage: "), words)

if __name__ == "__main__":
    unittest.main()
