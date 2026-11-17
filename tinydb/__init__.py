"""tinydb: a tiny database engine written from scratch in pure Python."""

from tinydb.query import Query, QueryError, QueryParser
from tinydb.record import Record
from tinydb.storage import CorruptLogError, Storage
from tinydb.table import Table

__all__ = ["CorruptLogError", "Query", "QueryError", "QueryParser", "Record", "Storage", "Table"]
__version__ = "0.2.0"
