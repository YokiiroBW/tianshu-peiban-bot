"""A small DB-API boundary for native PostgreSQL; product dialects own their SQL."""

import re
from collections.abc import Sequence
from types import MappingProxyType
from uuid import uuid4

import psycopg

from .errors import StorageError, postgres_error


def _row_metadata(columns):
    columns = tuple(columns)
    positions = {name.casefold(): n for n, name in reversed(list(enumerate(columns)))}
    return columns, MappingProxyType(positions)


class Row(Sequence):
    def __init__(self, values, columns):
        self._values = tuple(values)
        self._columns, self._positions = _row_metadata(columns)

    @classmethod
    def _from_metadata(cls, values, metadata):
        row = cls.__new__(cls)
        row._values = tuple(values)
        row._columns, row._positions = metadata
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._positions[key.casefold()]]
            except KeyError:
                raise IndexError("No item with that key") from None
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._columns)


class MemoryCursor:
    def __init__(self, rows=(), columns=(), rowcount=-1, lastrowid=None):
        self._rows = iter(rows)
        self._columns = tuple(columns)
        self._row_metadata = _row_metadata(self._columns)
        self.description = [(name, None, None, None, None, None, None) for name in self._columns]
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.arraysize = 1

    def fetchone(self):
        value = next(self._rows, None)
        return None if value is None else Row._from_metadata(value, self._row_metadata)

    def fetchmany(self, size=None):
        rows = []
        for _ in range(self.arraysize if size is None else size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self):
        return list(self)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def close(self):
        pass


def qmark(sql, has_parameters):
    """Bind positional '?' outside SQL literals/comments; never interpolate values."""
    if not has_parameters:
        return sql
    output = []
    n = 0
    quote = None
    block_depth = 0
    dollar = None
    line_comment = False
    while n < len(sql):
        char = sql[n]
        pair = sql[n : n + 2]
        if line_comment:
            output.append(char)
            line_comment = char != "\n"
            n += 1
        elif block_depth:
            output.append(pair if pair in {"/*", "*/"} else char)
            if pair == "/*":
                block_depth += 1
            elif pair == "*/":
                block_depth -= 1
            n += 2 if pair in {"/*", "*/"} else 1
        elif dollar:
            if sql.startswith(dollar, n):
                output.append(dollar)
                n += len(dollar)
                dollar = None
            else:
                output.append(char)
                n += 1
        elif quote:
            output.append(char)
            n += 1
            if char == quote:
                if n < len(sql) and sql[n] == quote:
                    output.append(quote)
                    n += 1
                else:
                    quote = None
        elif pair == "--":
            output.append(pair)
            line_comment = True
            n += 2
        elif pair == "/*":
            output.append(pair)
            block_depth = 1
            n += 2
        elif char in "'\"":
            quote = char
            output.append(char)
            n += 1
        elif char == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[n:])):
            dollar = match.group()
            output.append(dollar)
            n += len(dollar)
        elif char == "?":
            output.append("\x00tianshu_bind\x00")
            n += 1
        elif char == "%":
            output.append("%")
            n += 1
        else:
            output.append(char)
            n += 1
    result = "".join(output)
    if "\x00tianshu_bind\x00" in result:
        # psycopg parses '%' even inside quoted SQL when parameters are supplied.
        return result.replace("%", "%%").replace("\x00tianshu_bind\x00", "%s")
    return result


class Cursor:
    def __init__(self, connection, raw=None):
        self.connection = connection
        self.raw = raw
        self._delegate = None
        self.arraysize = 1
        self._row_result = None
        self._row_metadata = None

    def execute(self, sql, parameters=()):
        self._delegate = self.connection.execute(sql, parameters)
        self._clear_row_metadata()
        return self

    def executemany(self, sql, parameters):
        self._delegate = self.connection.executemany(sql, parameters)
        self._clear_row_metadata()
        return self

    @property
    def description(self):
        if self._delegate is not None:
            return self._delegate.description
        return self.raw.description

    @property
    def rowcount(self):
        return self._delegate.rowcount if self._delegate is not None else self.raw.rowcount

    @property
    def lastrowid(self):
        return self._delegate.lastrowid if self._delegate is not None else None

    def _clear_row_metadata(self):
        self._row_result = self._row_metadata = None

    def _get_row_metadata(self):
        result = getattr(self.raw, "pgresult", None)
        if result is None:
            # Non-psycopg cursors may expose only a changing DB-API description.
            columns = tuple(field.name for field in self.raw.description)
            if self._row_metadata is None or columns != self._row_metadata[0]:
                self._row_metadata = _row_metadata(columns)
            self._row_result = None
        elif self._row_metadata is None or result is not self._row_result:
            # psycopg creates new Column objects on every description access.
            # Its PGresult identifies the current execute()/nextset() result.
            self._row_metadata = _row_metadata(field.name for field in self.raw.description)
            self._row_result = result
        return self._row_metadata

    def fetchone(self):
        if self._delegate is not None:
            return self._delegate.fetchone()
        try:
            row = self.raw.fetchone()
        except psycopg.Error as exc:
            raise postgres_error(exc) from None
        if row is None:
            return None
        if self.connection.row_factory is None:
            return tuple(row)
        return Row._from_metadata(row, self._get_row_metadata())

    def fetchmany(self, size=None):
        result = []
        for _ in range(self.arraysize if size is None else size):
            row = self.fetchone()
            if row is None:
                break
            result.append(row)
        return result

    def fetchall(self):
        return list(self)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def close(self):
        try:
            if self._delegate is not None:
                self._delegate.close()
            elif self.raw is not None:
                self.raw.close()
        finally:
            self._clear_row_metadata()


class PostgresConnection:
    def __init__(self, raw, *, release=None, dialect=None, isolation_level="", path=None):
        self.raw = raw
        self._release = release
        self._dialect = dialect
        self._closed = False
        self.isolation_level = isolation_level
        self.row_factory = Row
        self.total_changes = 0
        self.path = path

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus

        return self.raw.info.transaction_status != TransactionStatus.IDLE

    def native(self, sql, parameters=(), *, savepoint=False):
        if self._closed:
            raise StorageError("storage_connection_closed")
        if not isinstance(sql, str):
            raise TypeError("SQL must be a string")
        parameters = () if parameters is None else parameters
        # Match SQLite's default implicit write transaction. Pure SELECT stays autocommit;
        # products explicitly start the read snapshots and authority transactions they need.
        leading = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if (
            self.isolation_level is not None
            and not self.in_transaction
            and leading in {"INSERT", "UPDATE", "DELETE", "REPLACE"}
        ):
            self.raw.execute("BEGIN")
        boundary = "ts_stmt_" + uuid4().hex if savepoint and self.in_transaction else None
        try:
            if boundary:
                self.raw.execute("SAVEPOINT " + boundary)
            statement = qmark(sql, bool(parameters))
            result = self.raw.execute(statement, parameters if parameters else None, prepare=False)
            if leading in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
                self.total_changes += max(0, result.rowcount)
            if boundary:
                self.raw.execute("RELEASE SAVEPOINT " + boundary)
            return Cursor(self, result)
        except psycopg.Error as exc:
            if boundary:
                try:
                    self.raw.execute("ROLLBACK TO SAVEPOINT " + boundary)
                    self.raw.execute("RELEASE SAVEPOINT " + boundary)
                except psycopg.Error:
                    # A lost socket cannot recover the savepoint. Return it to the
                    # pool exactly once so the pool can replace the broken session.
                    self.close()
            raise postgres_error(exc) from None

    def execute(self, sql, parameters=()):
        if self._dialect is not None:
            result = self._dialect(self, sql, parameters)
            if result is not None:
                return result
        return self.native(sql, parameters)

    def executemany(self, sql, parameters):
        total = 0
        last = None
        for values in parameters:
            last = self.execute(sql, values)
            total += max(last.rowcount, 0)
        if last is None:
            return MemoryCursor(rowcount=0)
        return MemoryCursor(rowcount=total, lastrowid=last.lastrowid)

    def executescript(self, sql):
        return self.execute(sql)

    def cursor(self):
        return Cursor(self)

    def commit(self):
        try:
            self.raw.commit()
        except psycopg.Error as exc:
            raise postgres_error(exc) from None

    def rollback(self):
        try:
            self.raw.rollback()
        except psycopg.Error as exc:
            raise postgres_error(exc) from None

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.in_transaction:
                self.raw.rollback()
        except psycopg.Error:
            self.raw.close()
        finally:
            if self._release is not None:
                self._release(self.raw)
            else:
                self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, traceback):
        if kind is None:
            self.commit()
        else:
            self.rollback()
        return False
