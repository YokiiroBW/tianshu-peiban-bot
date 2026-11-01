"""Registered node definitions and revision-bound tests over native PostgreSQL."""

import json

from .storage import DomainStore
from .contracts import canonical, require


class ExternalCatalog:
    @staticmethod
    def verify_existing(directory, *, runtime=None):
        require(runtime is not None, "database_unavailable", 503)

    def __init__(self, directory, *, runtime=None):
        self.store = DomainStore(runtime.database, "capability_connections")

    def snapshot(self):
        with self.store.transaction(False) as connection:
            rows = connection.execute("SELECT kind,revision,body,last_test FROM connections ORDER BY kind").fetchall()
        return sum(row["revision"] for row in rows), {
            row["kind"]: {"value": json.loads(row["body"]), "credential": None, "ca_pem": None,
                          "last_test": row["last_test"]} for row in rows
        }

    def save(self, kind, value, credential, ca, expected_revision, client_id):
        require(kind.startswith("infrastructure:"), "connection_kind_invalid", 400)
        with self.store.transaction() as connection:
            old = connection.execute("SELECT revision FROM connections WHERE kind=%s FOR UPDATE", (kind,)).fetchone()
            revision = old["revision"] if old else 0
            require(revision == expected_revision, "revision_conflict", 409)
            result = {**value, "connection_revision": revision + 1}
            connection.execute(
                "INSERT INTO connections(kind,revision,body) VALUES(%s,%s,%s) ON CONFLICT(kind) DO UPDATE "
                "SET revision=excluded.revision,body=excluded.body", (kind, revision + 1, canonical(result)))
        return {"revision": revision + 1}

    def record_test(self, kind, expected_catalog_revision, result):
        """A late collector cannot attach its result to a different connection."""
        require(kind.startswith("infrastructure:"), "connection_kind_invalid", 400)
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT kind,revision FROM connections ORDER BY kind FOR UPDATE").fetchall()
            require(sum(row["revision"] for row in rows) == expected_catalog_revision, "revision_conflict", 409)
            current = next((row for row in rows if row["kind"] == kind), None)
            require(current is not None and result.get("connection_revision") == current["revision"], "revision_conflict", 409)
            connection.execute("UPDATE connections SET last_test=%s::jsonb WHERE kind=%s", (canonical(result), kind))
        return result
