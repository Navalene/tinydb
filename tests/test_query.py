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
        for command in ("keys", "count", "clear", "compact", "help", "exit"):
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


if __name__ == "__main__":
    unittest.main()
