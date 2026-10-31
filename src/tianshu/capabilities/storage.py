"""Native PostgreSQL transactions for the retained media algorithms."""
from contextlib import contextmanager
from contextvars import ContextVar
BOUND_CONNECTION=ContextVar("capability_native_connection",default=None)

class Record(dict):
    def __iter__(self):return iter(self.values())
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def record_row(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Record(zip(names, values))

class DomainStore:
    def __init__(self, database, schema):
        if schema not in {"capability_media", "capability_plugins", "capability_infrastructure", "capability_connections"}:
            raise ValueError("Unknown capability schema")
        self.database, self.schema = database, schema

    @contextmanager
    def transaction(self,write=True):
        bound=BOUND_CONNECTION.get()
        if bound is not None:
            with bound.transaction():
                self.prepare(bound,write)
                yield bound
            return
        with self.database.sync_transaction() as conn:
            self.prepare(conn,write)
            yield conn
    def prepare(self,conn,write):
        conn.row_factory=record_row
        conn.execute("SET LOCAL search_path TO "+self.schema+",public")
        if write:conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(self.schema,))

async def run_repository(database,operation,*args,**kwargs):
    from contextvars import copy_context
    context=copy_context()
    def invoke(conn):
        token=BOUND_CONNECTION.set(conn)
        try:return operation(*args,**kwargs)
        finally:BOUND_CONNECTION.reset(token)
    return await database.run_sync(lambda conn:context.run(invoke,conn))
