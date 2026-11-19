"""tinydb: a tiny database engine written from scratch in pure Python."""

# First, before the imports below: server.py reads tinydb.__version__ while it's being imported.
__version__ = "0.6.0"

from tinydb.algorithms import binary_search, merge_sort
from tinydb.auth import ApiKey, KeyStore
from tinydb.btree import BTree
from tinydb.crypto import Cipher, DecryptionError
from tinydb.database import Database, DatabaseError, Result
from tinydb.hashtable import HashTable
from tinydb.index import BTreeIndex, HashIndex
from tinydb.lexer import SqlError, tokenize
from tinydb.query import Query, QueryError, QueryParser
from tinydb.record import Record
from tinydb.schema import Column, Schema, SchemaError
from tinydb.server import ApiServer
from tinydb.sql import SqlParser, parse
from tinydb.storage import CorruptLogError, Storage
from tinydb.table import Table
from tinydb.wal import WriteAheadLog

__all__ = [
    "ApiKey",
    "ApiServer",
    "BTree",
    "BTreeIndex",
    "Cipher",
    "Column",
    "CorruptLogError",
    "Database",
    "DatabaseError",
    "DecryptionError",
    "HashIndex",
    "HashTable",
    "KeyStore",
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
