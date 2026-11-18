"""tinydb: a tiny database engine written from scratch in pure Python."""

from tinydb.algorithms import binary_search, merge_sort
from tinydb.btree import BTree
from tinydb.hashtable import HashTable
from tinydb.index import BTreeIndex, HashIndex
from tinydb.query import Query, QueryError, QueryParser
from tinydb.record import Record
from tinydb.storage import CorruptLogError, Storage
from tinydb.table import Table

__all__ = [
    "BTree",
    "BTreeIndex",
    "CorruptLogError",
    "HashIndex",
    "HashTable",
    "Query",
    "QueryError",
    "QueryParser",
    "Record",
    "Storage",
    "Table",
    "binary_search",
    "merge_sort",
]
__version__ = "0.3.0"
