"""Registered node definitions over native PostgreSQL, owned by infrastructure."""
import json
from .storage import DomainStore
from .contracts import require,canonical
class ExternalCatalog:
    @staticmethod
    def verify_existing(directory,*,runtime=None):require(runtime is not None,"database_unavailable",503)
    def __init__(self,directory,*,runtime=None):self.store=DomainStore(runtime.database,"capability_connections")
    def snapshot(self):
        with self.store.transaction(False) as conn:
            rows=conn.execute("SELECT kind,revision,body FROM connections ORDER BY kind").fetchall()
        return sum(row["revision"] for row in rows),{row["kind"]:{"value":json.loads(row["body"]),"credential":None,"ca_pem":None,"last_test":None} for row in rows}
    def save(self,kind,value,credential,ca,expected_revision,client_id):
        require(kind.startswith("infrastructure:"),"connection_kind_invalid",400)
        with self.store.transaction() as conn:
            old=conn.execute("SELECT revision FROM connections WHERE kind=%s FOR UPDATE",(kind,)).fetchone();revision=old[0] if old else 0
            require(revision==expected_revision,"revision_conflict",409)
            result={**value,"connection_revision":revision+1}
            conn.execute("INSERT INTO connections(kind,revision,body) VALUES(%s,%s,%s) ON CONFLICT(kind) DO UPDATE SET revision=excluded.revision,body=excluded.body",(kind,revision+1,canonical(result)))
        return {"revision":revision+1}
