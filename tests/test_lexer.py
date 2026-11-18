"""Unit tests for the SQL lexer.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import unittest

from tinydb.lexer import END, KEYWORD, NAME, NUMBER, STRING, SYMBOL, SqlError, Token, tokenize


def kinds_and_values(text):
    return [(token.kind, token.value) for token in tokenize(text)]


class LexerTests(unittest.TestCase):
    def test_simple_select(self):
        self.assertEqual(kinds_and_values("SELECT * FROM clients;"), [
            (KEYWORD, "SELECT"), (SYMBOL, "*"), (KEYWORD, "FROM"), (NAME, "clients"), (SYMBOL, ";"), (END, None),
        ])

    def test_keywords_and_names_ignore_case(self):
        self.assertEqual(kinds_and_values("select Name"), [(KEYWORD, "SELECT"), (NAME, "name"), (END, None)])

    def test_names_can_have_digits_and_underscores(self):
        self.assertEqual(tokenize("plan_2")[0], Token(NAME, "plan_2", 0))

    def test_numbers(self):
        self.assertEqual(kinds_and_values("65 -3 24.5"), [(NUMBER, 65), (NUMBER, -3), (NUMBER, 24.5), (END, None)])
        self.assertIsInstance(tokenize("24.0")[0].value, float)

    def test_bad_number(self):
        with self.assertRaises(SqlError):
            tokenize("1.2.3")

    def test_strings(self):
        self.assertEqual(tokenize("'Ada Lovelace'")[0], Token(STRING, "Ada Lovelace", 0))
        self.assertEqual(tokenize("''")[0].value, "")

    def test_doubled_quote_is_one_quote(self):
        self.assertEqual(tokenize("'O''Brien'")[0].value, "O'Brien")

    def test_sql_inside_a_string_stays_text(self):
        tokens = tokenize("'x; DROP TABLE clients; --'")
        self.assertEqual(len(tokens), 2)
        self.assertEqual(tokens[0], Token(STRING, "x; DROP TABLE clients; --", 0))

    def test_unclosed_string(self):
        with self.assertRaises(SqlError):
            tokenize("'Ada")

    def test_double_quotes_are_rejected(self):
        with self.assertRaises(SqlError):
            tokenize('"Ada"')

    def test_symbols(self):
        values = [token.value for token in tokenize("<= >= != <> = < > ( ) ,")][:-1]
        self.assertEqual(values, ["<=", ">=", "!=", "!=", "=", "<", ">", "(", ")", ","])

    def test_unknown_character(self):
        with self.assertRaises(SqlError):
            tokenize("SELECT @")

    def test_comments_are_skipped(self):
        self.assertEqual(kinds_and_values("-- all clients\nSELECT"), [(KEYWORD, "SELECT"), (END, None)])

    def test_positions(self):
        self.assertEqual([token.position for token in tokenize("SELECT  age")], [0, 8, 11])

    def test_describe(self):
        self.assertEqual(tokenize("x")[1].describe(), "the end")
        self.assertEqual(tokenize("'Ada'")[0].describe(), "'Ada'")
        self.assertEqual(tokenize("age")[0].describe(), "age")


if __name__ == "__main__":
    unittest.main()
