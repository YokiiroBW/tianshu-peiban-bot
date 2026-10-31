"""Instance-scoped encrypted fields, using the platform's established AES-GCM format."""

import base64
from ..contracts import Fault, require


class Vault:
    def __init__(self, repository, credentials):
        self.repository, self.credentials = repository, credentials

    def names(self, instance, db=None):
        if db is None:
            with self.repository.transaction(False) as connection:
                return self.names(instance, connection)
        return {
            row[0]: True
            for row in db.execute(
                "SELECT name FROM plugin_secrets WHERE instance_id=%s", (instance,)
            )
        }

    def save(self, instance, name, value, db):
        if value is None:
            db.execute(
                "DELETE FROM plugin_secrets WHERE instance_id=%s AND name=%s", (instance, name)
            )
            return
        encrypted = self.credentials.seal_bytes(
            value.encode(), domain="capabilities.plugin", reference=instance + ":" + name
        )
        db.execute(
            "INSERT INTO plugin_secrets VALUES(%s,%s,%s) ON CONFLICT(instance_id,name) DO UPDATE SET value=excluded.value",
            (instance, name, encrypted),
        )

    def resolve(self, instance, name):
        with self.repository.transaction(False) as db:
            row = db.execute(
                "SELECT value FROM plugin_secrets WHERE instance_id=%s AND name=%s",
                (instance, name),
            ).fetchone()
        require(row is not None, "config_invalid", 400)
        return self.unseal(instance, name, bytes(row[0]))

    def snapshot(self, instance):
        with self.repository.transaction(False) as db:
            return {
                row[0]: base64.b64encode(bytes(row[1])).decode()
                for row in db.execute(
                    "SELECT name,value FROM plugin_secrets WHERE instance_id=%s", (instance,)
                )
            }

    def unseal(self, instance, name, value):
        try:
            return self.credentials.unseal_bytes(
                value, domain="capabilities.plugin", reference=instance + ":" + name
            ).decode()
        except Exception:
            raise Fault("plugin_store_unavailable", 503) from None
