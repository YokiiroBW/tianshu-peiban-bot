"""Latest attempts and the last usable observation, never a second connection/credential store."""

import json
import psycopg
from contextlib import closing
from pathlib import Path

from ..contracts import Fault, canonical


class Repository:
    def __init__(self, database, *, runtime=None):
        from ..storage import DomainStore
        self.database=Path(database)
        self.storage = DomainStore(runtime.database, "capability_infrastructure")

    def record(self, node_id, operation, revision, result):
        try:
            with self.storage.transaction() as db:


                old = db.execute(
                    'SELECT revision,sample FROM observations WHERE node_id=%s AND operation=%s',
                    (node_id, operation),
                ).fetchone()
                # LocalWork owns running native database work after its caller is cancelled.
                # A retired connection's late commit cannot replace a newer revision.
                if old and old[0] > revision:
                    return
                sample = old[1] if old and old[0] == revision else None
                if result["status"] in {"ok", "partial"} and result.get("data") is not None:
                    sample = canonical(result)
                db.execute(
                    'INSERT INTO observations VALUES(%s,%s,%s,%s,%s) ON CONFLICT(node_id,operation) DO UPDATE SET revision=excluded.revision,latest=excluded.latest,sample=excluded.sample',
                    (node_id, operation, revision, canonical(result), sample),
                )
        except (psycopg.Error, OSError):
            raise Fault("infrastructure_store_unavailable", 503) from None

    def read(self, node_id, operation, revision):
        try:
            with self.storage.transaction(False) as db:
                row = db.execute(
                    'SELECT revision,latest,sample FROM observations WHERE node_id=%s AND operation=%s',
                    (node_id, operation),
                ).fetchone()
            if row is None or row[0] != revision:
                return None
            return {"latest": json.loads(row[1]), "sample": json.loads(row[2]) if row[2] else None}
        except (psycopg.Error, OSError, ValueError):
            raise Fault("infrastructure_store_unavailable", 503) from None

    def keys(self, node_id, prefix, revision):
        try:
            with self.storage.transaction(False) as db:
                return [
                    row[0]
                    for row in db.execute(
                        'SELECT operation FROM observations WHERE node_id=%s AND revision=%s AND operation LIKE %s',
                        (node_id, revision, prefix + "%"),
                    )
                ]
        except (psycopg.Error, OSError):
            raise Fault("infrastructure_store_unavailable", 503) from None
