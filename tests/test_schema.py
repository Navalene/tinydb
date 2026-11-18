"""Unit tests for Column and Schema.

Run from the project root: python3 -m unittest discover -s tests -t .
"""

import unittest

from tinydb.schema import Column, Schema, SchemaError, check_name


def clients_schema():
    return Schema((
        Column("id", "INT", primary_key=True),
        Column("name", "TEXT", not_null=True),
        Column("age", "INT"),
        Column("premium", "FLOAT"),
        Column("active", "BOOL"),
        Column("sep_deadline", "DATE"),
    ))


class CheckNameTests(unittest.TestCase):
    def test_accepts_simple_names(self):
        for name in ("clients", "sep_deadline", "_tmp", "plan2"):
            check_name(name)

    def test_rejects_bad_names(self):
        for name in ("", "Clients", "2plans", "my-table", "../secrets", "a b"):
            with self.assertRaises(SchemaError):
                check_name(name)

    def test_rejects_non_strings(self):
        with self.assertRaises(SchemaError):
            check_name(None)


class ColumnTests(unittest.TestCase):
    def test_rejects_unknown_type(self):
        with self.assertRaises(SchemaError):
            Column("age", "NUMBER")

    def test_primary_key_type_is_limited(self):
        Column("id", "TEXT", primary_key=True)
        with self.assertRaises(SchemaError):
            Column("id", "FLOAT", primary_key=True)

    def test_int(self):
        column = Column("age", "INT")
        self.assertEqual(column.convert(70), 70)
        for bad in ("70", 70.5, True):
            with self.assertRaises(SchemaError):
                column.convert(bad)

    def test_float_accepts_whole_numbers(self):
        column = Column("premium", "FLOAT")
        self.assertEqual(column.convert(25), 25.0)
        self.assertIsInstance(column.convert(25), float)
        with self.assertRaises(SchemaError):
            column.convert("25")

    def test_text_and_bool(self):
        self.assertEqual(Column("name", "TEXT").convert("Ada"), "Ada")
        self.assertIs(Column("active", "BOOL").convert(False), False)
        with self.assertRaises(SchemaError):
            Column("name", "TEXT").convert(5)
        with self.assertRaises(SchemaError):
            Column("active", "BOOL").convert(1)

    def test_date_must_be_a_real_iso_date(self):
        column = Column("sep_deadline", "DATE")
        self.assertEqual(column.convert("2026-12-31"), "2026-12-31")
        for bad in ("12/31/2026", "2026-02-30", 20261231):
            with self.assertRaises(SchemaError):
                column.convert(bad)

    def test_null(self):
        self.assertIsNone(Column("age", "INT").convert(None))
        with self.assertRaises(SchemaError):
            Column("name", "TEXT", not_null=True).convert(None)
        with self.assertRaises(SchemaError):
            Column("id", "INT", primary_key=True).convert(None)

    def test_to_dict_and_from_dict_round_trip(self):
        column = Column("id", "INT", primary_key=True)
        self.assertEqual(Column.from_dict(column.to_dict()), column)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = clients_schema()

    def test_needs_exactly_one_primary_key(self):
        with self.assertRaises(SchemaError):
            Schema((Column("name", "TEXT"),))
        with self.assertRaises(SchemaError):
            Schema((Column("a", "INT", primary_key=True), Column("b", "INT", primary_key=True)))

    def test_rejects_empty_and_duplicate_columns(self):
        with self.assertRaises(SchemaError):
            Schema(())
        with self.assertRaises(SchemaError):
            Schema((Column("id", "INT", primary_key=True), Column("id", "TEXT")))

    def test_names_and_primary_key(self):
        self.assertEqual(self.schema.names(), ["id", "name", "age", "premium", "active", "sep_deadline"])
        self.assertEqual(self.schema.primary_key, "id")
        self.assertEqual(self.schema.column("age"), Column("age", "INT"))
        with self.assertRaises(SchemaError):
            self.schema.column("zip")

    def test_make_row_converts_and_leaves_out_nulls(self):
        row = self.schema.make_row({"id": 1, "name": "Ada", "premium": 30, "age": None})
        self.assertEqual(row, {"id": 1, "name": "Ada", "premium": 30.0})

    def test_make_row_rejects_bad_rows(self):
        with self.assertRaises(SchemaError):
            self.schema.make_row({"id": 1})  # name is NOT NULL
        with self.assertRaises(SchemaError):
            self.schema.make_row({"id": 1, "name": "Ada", "zip": "02134"})
        with self.assertRaises(SchemaError):
            self.schema.make_row({"id": "one", "name": "Ada"})

    def test_key_of(self):
        self.assertEqual(self.schema.key_of({"id": 7, "name": "Ada"}), "7")

    def test_to_dict_and_from_dict_round_trip(self):
        self.assertEqual(Schema.from_dict(self.schema.to_dict()), self.schema)

    def test_list_of_columns_becomes_a_tuple(self):
        schema = Schema([Column("id", "INT", primary_key=True)])  # type: ignore
        self.assertIsInstance(schema.columns, tuple)


if __name__ == "__main__":
    unittest.main()
