"""Unit tests for the SQL parser.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import unittest

from tinydb.lexer import SqlError
from tinydb.schema import Column, SchemaError
from tinydb.sql import (
    Condition,
    CreateIndex,
    CreateTable,
    Delete,
    Describe,
    DropTable,
    Insert,
    Select,
    ShowTables,
    Update,
    parse,
)


def one(text):
    """Parse text that holds exactly one statement and return it."""
    statements = parse(text)
    assert len(statements) == 1, statements
    return statements[0]


class CreateAndDropTests(unittest.TestCase):
    def test_create_table(self):
        statement = one("CREATE TABLE clients (id INT PRIMARY KEY, name TEXT NOT NULL, age int)")
        self.assertEqual(statement, CreateTable("clients", (
            Column("id", "INT", primary_key=True),
            Column("name", "TEXT", not_null=True),
            Column("age", "INT"),
        )))

    def test_constraints_in_either_order(self):
        statement = one("CREATE TABLE t (id TEXT NOT NULL PRIMARY KEY)")
        self.assertEqual(statement, CreateTable("t", (Column("id", "TEXT", primary_key=True, not_null=True),)))

    def test_unknown_type(self):
        with self.assertRaises(SqlError):
            one("CREATE TABLE t (id NUMBER PRIMARY KEY)")

    def test_bad_column_is_caught_by_column(self):
        with self.assertRaises(SchemaError):
            one("CREATE TABLE t (id FLOAT PRIMARY KEY)")

    def test_drop_table(self):
        self.assertEqual(one("DROP TABLE clients"), DropTable("clients"))

    def test_create_index(self):
        self.assertEqual(one("CREATE INDEX ON clients (age)"), CreateIndex("clients", "age", "btree"))
        self.assertEqual(one("CREATE INDEX ON clients USING hash (plan)"), CreateIndex("clients", "plan", "hash"))
        with self.assertRaises(SqlError):
            one("CREATE INDEX ON clients USING bitmap (plan)")

    def test_show_tables_and_describe(self):
        self.assertEqual(one("SHOW TABLES"), ShowTables())
        self.assertEqual(one("DESCRIBE clients"), Describe("clients"))


class InsertTests(unittest.TestCase):
    def test_insert_every_column(self):
        statement = one("INSERT INTO clients VALUES (1, 'Ada', 70, TRUE, NULL)")
        self.assertEqual(statement, Insert("clients", None, ((1, "Ada", 70, True, None),)))

    def test_insert_named_columns_and_many_rows(self):
        statement = one("INSERT INTO clients (id, name) VALUES (1, 'Ada'), (2, 'Bob')")
        self.assertEqual(statement, Insert("clients", ("id", "name"), ((1, "Ada"), (2, "Bob"))))

    def test_insert_needs_values(self):
        with self.assertRaises(SqlError):
            one("INSERT INTO clients (id, name)")


class SelectTests(unittest.TestCase):
    def test_select_star(self):
        self.assertEqual(one("SELECT * FROM clients"), Select("clients", None))

    def test_select_columns(self):
        self.assertEqual(one("SELECT name, age FROM clients").columns, ("name", "age"))

    def test_where_with_and(self):
        statement = one("SELECT * FROM clients WHERE plan = 'PPO' AND age >= 65 AND age <> 70")
        self.assertEqual(statement.where, (
            Condition("plan", "=", "PPO"),
            Condition("age", ">=", 65),
            Condition("age", "!=", 70),
        ))

    def test_order_by_and_limit(self):
        statement = one("SELECT * FROM clients ORDER BY age DESC LIMIT 2")
        self.assertEqual((statement.order_by, statement.descending, statement.limit), ("age", True, 2))
        self.assertFalse(one("SELECT * FROM clients ORDER BY age ASC").descending)

    def test_bad_limit(self):
        for text in ("SELECT * FROM t LIMIT -1", "SELECT * FROM t LIMIT 2.5", "SELECT * FROM t LIMIT x"):
            with self.assertRaises(SqlError):
                one(text)

    def test_null_in_where_is_rejected(self):
        with self.assertRaises(SqlError):
            one("SELECT * FROM clients WHERE plan = NULL")

    def test_missing_comparison(self):
        with self.assertRaises(SqlError):
            one("SELECT * FROM clients WHERE age 65")

    def test_missing_from(self):
        with self.assertRaises(SqlError):
            one("SELECT * clients")


class UpdateAndDeleteTests(unittest.TestCase):
    def test_update(self):
        statement = one("UPDATE clients SET plan = 'HMO', age = 71 WHERE id = 1")
        self.assertEqual(statement, Update("clients", (("plan", "HMO"), ("age", 71)), (Condition("id", "=", 1),)))

    def test_delete(self):
        self.assertEqual(one("DELETE FROM clients WHERE age < 65"), Delete("clients", (Condition("age", "<", 65),)))
        self.assertEqual(one("DELETE FROM clients"), Delete("clients"))


class ScriptTests(unittest.TestCase):
    def test_many_statements(self):
        statements = parse("DROP TABLE a; DROP TABLE b;;")
        self.assertEqual(statements, [DropTable("a"), DropTable("b")])

    def test_empty_script(self):
        self.assertEqual(parse(" ; -- nothing here"), [])

    def test_statements_need_semicolons_between_them(self):
        with self.assertRaises(SqlError):
            parse("DROP TABLE a DROP TABLE b")

    def test_unknown_statement(self):
        with self.assertRaises(SqlError):
            parse("MERGE INTO clients")

    def test_error_says_where(self):
        with self.assertRaises(SqlError) as caught:
            parse("SELECT * FROM 42")
        self.assertIn("position 14", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
