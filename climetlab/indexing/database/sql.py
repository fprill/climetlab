# (C) Copyright 2021 ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#


import datetime
import logging
import os
import sqlite3
from collections import defaultdict
from threading import local

import numpy as np

import climetlab as cml
from climetlab.core.index import Order, OrderOrSelection, Selection
from climetlab.utils import tqdm
from climetlab.utils.parts import Part

from . import (
    ALL_KEYS,
    ALL_KEYS_DICT,
    CFGRIB_KEYS,
    FILEPARTS_KEY_NAMES,
    FILEPARTS_KEYS,
    GRIB_KEYS,
    STATISTICS_KEY_NAMES,
    STATISTICS_KEYS,
    Database,
    DBKey,
    FloatDBKey,
    IntDBKey,
    StrDBKey,
)

LOG = logging.getLogger(__name__)


class EntriesTable:
    table_name = "entries"

    def __init__(self, owner):
        self.owner = owner
        self.keys = {}
        self.build()

    def build(self):
        cursor = self.owner.connection.execute(f"PRAGMA table_info({self.table_name})")
        for x in cursor.fetchall():
            column_name = x[1]
            typ = x[2]
            klass = {"TEXT": StrDBKey, "FLOAT": FloatDBKey, "INTEGER": IntDBKey}[typ]
            name = self.dbname_to_name(column_name)
            self.keys[name] = klass(name)
        if not self.keys:
            LOG.debug(f"Table {self.table_name} does not exist.")

    @property
    def connection(self):
        return self.owner.connection

    def name_to_dbname(self, n):
        def add_mars(x):
            return "mars_" + x

        def remove_first_underscore(x):
            assert x[0] == "_", x
            return x[1:]

        if n in FILEPARTS_KEY_NAMES:
            return remove_first_underscore(n)
        if n in STATISTICS_KEY_NAMES:
            return n
        return add_mars(n)

    def dbname_to_name(self, n):
        if n.startswith("mars_"):
            return n[5:]
        return n

    def __str__(self):
        content = ",".join([k for k, v in self.keys.items()])
        return f"EntriesTable({self.table_name},{content}"

    def create_table_from_entry_if_needed(self, entry):
        if self.keys:
            assert self.insert_statement
            # self.keys is not empty. Table already created.
            return

        CLASSES = {
            str: StrDBKey,
            float: FloatDBKey,
            np.float64: FloatDBKey,
            np.float32: FloatDBKey,
            int: IntDBKey,
            datetime.datetime: StrDBKey,
        }
        for k, v in entry.items():
            typ = type(v)
            if typ not in CLASSES:
                raise ValueError(f"Unknown type '{typ}' for key '{k}'.")
            klass = CLASSES[typ]
            dbkey = klass(k)
            self.keys[k] = dbkey

        assert self.keys, f"Cannot build from entry '{entry}'"
        LOG.debug(f"Created table {self} from entry {entry}.")

        self.key_names = [k for k, v in self.keys.items()]
        self.column_names = [self.name_to_dbname(k) for k, v in self.keys.items()]

        columns_defs = ",".join(
            [
                f"{self.name_to_dbname(v.name)} {v.sql_type}"
                for k, v in self.keys.items()
            ]
        )
        create_statement = (
            f"CREATE TABLE IF NOT EXISTS {self.table_name} ({columns_defs});"
        )
        LOG.debug("%s", create_statement)
        self.connection.execute(create_statement)

        names = ",".join(self.column_names)
        values = ",".join(["?"] * len(self.column_names))
        print("BUILD")
        self.insert_statement = (
            f"INSERT INTO {self.table_name} ({names}) VALUES({values});"
        )
        LOG.debug("%s", self.insert_statement)

    def insert(self, entry):
        self.create_table_from_entry_if_needed(entry)
        values = [entry.get(k) for k in self.key_names]
        LOG.debug("inserting entry")
        LOG.debug(entry)
        self.connection.execute(self.insert_statement, tuple(values))

    def build_sql_indexes(self):
        indexed_columns = [
            v
            for k, v in self.keys.items()
            if self.name_to_dbname(k).startswith("mars_")
        ]
        indexed_columns += self.keys["_path"]

        pbar = tqdm(indexed_columns, desc="Building indexes")
        for n in pbar:
            pbar.set_description(f"Building index for {n}")
            self.connection.execute(
                f"CREATE INDEX IF NOT EXISTS {n}_index ON {self.table_name} ({n});"
            )


def _list_all_tables(connection):
    statement = "SELECT name FROM sqlite_master WHERE type='table';"
    cursor = connection.execute(statement)
    return [r[0] for r in cursor]


class CoordTable:
    def __init__(self, key, connection, create_if_not_exists=False):
        self.connection = connection
        self.key = key
        self.table_name = "coords_" + self.key
        if create_if_not_exists:
            self.create_table_if_not_exist()
        self.dic = self.read_table()

    def create_table_if_not_exist(self):
        create_statement = f"""CREATE TABLE IF NOT EXISTS {self.table_name} (
            key   INTEGER PRIMARY KEY,
            value TEXT
            );"""
        LOG.debug("%s", create_statement)
        self.connection.execute(create_statement)
        assert self._table_exists()

    def _table_exists(self):
        return self.table_name in _list_all_tables(self.connection)

    def read_table(self):
        if not self._table_exists():
            raise CoordTableDoesNotExist()

        statement = f"SELECT key,value FROM {self.table_name}; "
        LOG.debug("%s", statement)
        return {k: v for k, v in self.connection.execute(statement)}

    def append(self, value):
        value = str(value)

        if value in self.dic.values():
            return  # already in the table

        self.create_table_if_not_exist()

        statement = f"INSERT INTO {self.table_name} (value) VALUES(?); "
        LOG.debug("%s", statement)
        self.connection.execute(statement, [value])

        statement = f"SELECT key FROM {self.table_name} WHERE value='{value}'; "
        LOG.debug("%s", statement)
        keys = []
        for key in self.connection.execute(statement):
            keys.append(key)
        assert len(keys) == 1
        self.dic[key[0]] = value

    def is_empty(self):
        return len(self.dic) > 0

    def __len__(self):
        return len(self.dic)

    def items(self):
        return self.dic.items()

    def keys(self):
        return self.dic.keys()

    def __str__(self):
        typ = ""
        if self.dic:
            first = self.dic[list(self.dic.keys())[0]]
            if not isinstance(first, str):
                typ = f" ({type(first)})"
        return f"{self.key}{typ}={'/'.join([str(v) for v in self.dic.values()])}"


class CoordTableDoesNotExist(Exception):
    pass


class CoordTables:
    def __init__(self, connection):
        self.connection = connection
        self.dic = {}

        for table in _list_all_tables(self.connection):
            if not table.startswith("coords_"):
                continue
            key = table[len("coords_") :]
            self.dic[key] = CoordTable(key, self.connection)

    def __getitem__(self, key):
        if key not in self.dic:
            self.dic[key] = CoordTable(key, self.connection, create_if_not_exists=True)
        return self.dic[key]

    def update_with_entry(self, entry):
        for n in [k.name for k in GRIB_KEYS] + ["md5_grid_section"] + ["_path"]:
            v = entry.get(n)
            if v is None:
                continue
            self[n].append(v)

    def __str__(self):
        return "Coords:" + "\n".join([str(v) for k, v in self.dic.items()])

    def __len__(self):
        return len(self.dic)

    def items(self):
        return self.dic.items()

    def keys(self):
        return self.dic.keys()


class SqlSorter:
    @property
    def _func_name(self):
        return f"userorder_{self.view}"

    def __init__(self, order, view, db):
        self.order = order
        self.view = view
        self.db = db

        self.dict_of_dicts = dict()
        self.order_lst = []

        # TODO: To improve speed, we could use ASC or DESC when lst is already sorted
        # TODO: move GRIB_INDEX_KEYS and two comments above to upper class
        # Use mars keys order by default
        # But make sure the order provided by the user
        # in the order override this default order.

        if order is None or order.is_empty:
            return

        for key, lst in self.order.items():
            self._add_key(key, lst)

    def _add_key(self, key, lst):
        dbkey = ALL_KEYS_DICT[key]

        if lst is None:
            self.order_lst.append(dbkey.name_in_db)
            return
        if lst == "ascending":
            self.order_lst.append(f"{dbkey.name_in_db} ASC")
            return
        if lst == "descending":
            self.order_lst.append(f"{dbkey.name_in_db} DESC")
            return
        if not isinstance(lst, (list, tuple)):
            lst = [lst]

        lst = [dbkey.normalize(value, db=self.db) for value in lst]

        self.dict_of_dicts[key] = dict(zip(lst, range(len(lst))))
        self.order_lst.append(f'{self._func_name}("{key}",{dbkey.name_in_db})')

    @property
    def order_statement(self):
        if not self.order_lst:
            assert not self.dict_of_dicts, self.dict_of_dicts
            return ""
        return "ORDER BY " + ",".join(self.order_lst)

    def create_sql_function_if_needed(self, connection):
        if not self.dict_of_dicts:
            return

        dict_of_dicts = self.dict_of_dicts  # avoid creating closure on self.

        def order_func(k, v):
            return dict_of_dicts[k][v]

        connection.create_function(self._func_name, 2, order_func)


class Connection(local):
    # Inheriting from threading.local allows one connection for each thread
    # __init__ is "called each time the local object is used in a separate thread".
    # https://github.com/python/cpython/blob/0346eddbe933b5f1f56151bdebf5bd49392bc275/Lib/_threading_local.py#L65
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path)


class SqlDatabase(Database):
    VERSION = 5
    EXTENSION = ".db"

    def __init__(
        self,
        db_path,
        filters=None,
    ):
        self._cache_column_names = {}

        self.db_path = db_path
        self._filters = filters or []
        self._view = None
        self._connection = None
        self._entries_table = None
        self.entries_table

    @property
    def view(self):
        if self._view is None:
            self._view = "entries"
            for f in self._filters:
                self._apply_filter(f)
            LOG.debug("DB %s %s", self.db_path, self.view)
        return self._view

    @property
    def connection(self):
        if self._connection is None:
            self._connection = Connection(self.db_path)
        return self._connection._conn

    def _apply_filter(self, filter: OrderOrSelection):
        # This method updates self.view with the additional filter

        old_view = self._view
        new_view = old_view + "_" + filter.h(parent_view=old_view)

        if isinstance(filter, Selection):
            order = None
            selection = filter
        elif isinstance(filter, Order):
            selection = None
            order = filter
        else:
            assert False, (type(filter), filter)

        conditions_statement = self._conditions(selection)
        sorter = SqlSorter(order, new_view, db=self)
        statement = (
            f"CREATE TEMP VIEW IF NOT EXISTS {new_view} AS SELECT * "
            + f"FROM {old_view} {conditions_statement} {sorter.order_statement};"
        )

        sorter.create_sql_function_if_needed(self.connection)
        LOG.debug("%s", statement)
        for i in self.connection.execute(statement):
            LOG.error(str(i))  # Output of .execute should be empty

        self._view = new_view

    def filter(self, filter: OrderOrSelection):
        return self.__class__(
            self.db_path,
            filters=self._filters + [filter],
        )

    @property
    def _version(self):
        cursor = self.connection.execute("PRAGMA user_version;")
        for res in cursor:
            version = res[0]
            return version if version else None
        assert False

    def _set_version(self):
        if self._version is None:
            self.connection.execute(f"PRAGMA user_version = {self.VERSION};")
            return
        self._check_version()

    def _check_version(self):
        version = self._version
        if version is None or version == self.VERSION:
            return
        raise Exception(
            (
                "Version mismatch: current version for database index"
                " is {self.VERSION} and the database already has version"
                f" {version}"
            )
        )

    @property
    def entries_table(self):
        if self._entries_table is None:
            self._set_version()
            self._entries_table = EntriesTable(self)
        return self._entries_table

    def load(self, iterator):
        # coords_tables = CoordTables(conn)
        count = 0
        for entry in iterator:
            self.entries_table.insert(entry)
            # coords_tables.update_with_entry(entry)
            count += 1

        assert count >= 1, "No entry found."
        LOG.info("Added %d entries", count)

        return count

    def _conditions(self, selection):
        if selection is None or selection.is_empty:
            return ""
        conditions = []
        for k, b in selection.dic.items():
            if b is None or b == cml.ALL:
                continue

            dbkey = ALL_KEYS_DICT[k]

            if isinstance(b, (list, tuple)):
                # if len(b) == 1:
                #    conditions.append(f"{dbkey.name_in_db}='{b[0]}'")
                #    continue
                w = ",".join([dbkey.to_sql_value(x) for x in b])
                conditions.append(f"{dbkey.name_in_db} IN ({w})")
                continue

            conditions.append(f"{dbkey.name_in_db}='{b}'")

        if not conditions:
            return ""
        return " WHERE " + " AND ".join(conditions)

    def lookup_parts(self, limit=None, offset=None, resolve_paths=True):
        """
        Look into the database and provide entries as Parts.
        limit: Returns only "limit" entries (used for paging).
        offset: Skip the first "offset" entries (used for paging).
        """

        _names = ["path", "offset", "length"]
        parts = []
        for path, offset, length in self._execute_select(_names, limit, offset):
            parts.append(Part(path, offset, length))
        if resolve_paths:
            parts = Part.resolve(parts, os.path.dirname(self.db_path))
        return parts

    def lookup_dicts(
        self,
        keys=None,
        limit=None,
        offset=None,
        remove_none=True,
        with_parts=None,
    ):
        """
        From a list of keys, return dicts with these columns of the database.
        limit: Returns only "limit" entries (used for paging).
        offset: Skip the first "offset" entries (used for paging).
        """

        if keys is None:
            keys = [k.name for k in GRIB_KEYS + STATISTICS_KEYS + CFGRIB_KEYS]
            if with_parts is None:
                with_parts = True

        if not isinstance(keys, (list, tuple)):
            keys = [keys]

        if with_parts:
            keys = [k.name for k in FILEPARTS_KEYS] + keys

        dbkeys = [ALL_KEYS_DICT[name] for name in keys]

        names_in_db = [k.name_in_db for k in dbkeys]
        names = [k.name for k in dbkeys]
        for tupl in self._execute_select(names_in_db, limit, offset):
            dic = {k: v for k, v in zip(names, tupl)}

            if remove_none:
                dic = {k: v for k, v in dic.items() if v is not None}
            yield dic

    def _execute_select(self, column_names, limit=None, offset=None):
        names_str = ",".join([x for x in column_names]) if column_names else "*"
        limit_str = f" LIMIT {limit}" if limit is not None else ""
        offset_str = f" OFFSET {offset}" if offset is not None else ""

        statement = f"SELECT {names_str} FROM {self.view} {limit_str} {offset_str};"
        LOG.debug("%s", statement)

        for tupl in self.connection.execute(statement):
            yield tupl

    def _find_all_coords_dict(self):
        raise NotImplementedError("wip")
        # start-of: This is just an optimisation for speed.
        if all([isinstance(f, Order) for f in self._filters]):
            # if there is a Selection filter, it may remove some keys
            # by selecting values on some other keys.
            # In such case, we cannot rely on the coords tables created
            # for the whole dataset.
            # For instance doing .sel(param='2t') will remove some keys
            # for step that had been inserted by param='tp'.
            return self._find_all_coords_dict_from_coords_tables()
        # end-of: This is just an optimisation for speed.

        values = defaultdict(list)
        i_names = self._columns_names("i", remove_prefix=False)
        names = self._columns_names("i", remove_prefix=True)
        for tupl in self._execute_select(i_names):
            for k, v in zip(names, tupl):
                if v in values[k]:
                    continue
                values[k].append(v)

        return values

    def _find_all_coords_dict_from_coords_tables(self):
        raise NotImplementedError("wip")
        # coords_tables = CoordTables(self.connection)
        # keys = list(coords_tables.keys())
        # keys = [k for k in keys if k in GRIB_KEYS_NAMES]

        # for f in self._filters:
        #     firsts = list(f.keys())
        #     keys = firsts + [k for k in keys if k not in firsts]

        # coords = {k: coords_tables[k].dic.values() for k in keys}

        # for f in self._filters:
        #     coords = {k: f.filter_values(k, v) for k, v in coords.items()}

        # return coords

    def count(self):
        statement = f"SELECT COUNT(*) FROM {self.view};"
        for result in self.connection.execute(statement):
            return result[0]
        assert False, statement  # Fail if result is empty.

    def duplicate_db(self, filename, **kwargs):
        new_db = SqlDatabase(db_path=filename)
        iterator = self.lookup_dicts()
        new_db.load(iterator)
        return new_db

    def normalize_datetime(self, value):
        return value
