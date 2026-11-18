"""tinydb: a tiny database engine written from scratch in pure Python."""

from tinydb.algorithms import binary_search, merge_sort
from tinydb.btree import BTree
from tinydb.database import Database, DatabaseError, Result
from tinydb.hashtable import HashTable
from tinydb.index import BTreeIndex, HashIndex
from tinydb.lexer import SqlError, tokenize
from tinydb.query import Query, QueryError, QueryParser
from tinydb.record import Record
from tinydb.schema import Column, Schema, SchemaError
from tinydb.sql import SqlParser, parse
from tinydb.storage import CorruptLogError, Storage
from tinydb.table import Table
from tinydb.wal import WriteAheadLog

__all__ = [
    "BTree",
    "BTreeIndex",
    "Column",
    "CorruptLogError",
    "Database",
    "DatabaseError",
    "HashIndex",
    "HashTable",
    "Query",
    "QueryError",
    "QueryParser",
    "Record",
    "Result",
    "Schema",
    "SchemaError",
    "SqlError",
    "SqlParser",
    "Storage",
    "Table",
    "WriteAheadLog",
    "binary_search",
    "merge_sort",
    "parse",
    "tokenize",
]
__version__ = "0.4.0"
