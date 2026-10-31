"""Instance-scoped encrypted fields, using the platform's established AES-GCM format."""

import os
import base64
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..contracts import Fault, require


class Vault:
    def __init__(self, repository, key_file):
        self.repository, self.path = repository, Path(key_file)
        if not self.path.exists():
            with repository.transaction(False) as db:
                require(
                    db.execute('SELECT 1 FROM plugin_secrets LIMIT 1').fetchone() is None,
                    "plugin_store_unavailable",
                    503,
                )
            try:
                with self.path.open("xb") as stream:
                    os.chmod(self.path, 0o600)
                    stream.write(AESGCM.generate_key(bit_length=256))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
        try:
            self.cipher = AESGCM(self.path.read_bytes())
        except (OSError, ValueError):
            raise Fault("plugin_store_unavailable", 503) from None

    def names(self, instance, db=None):
        if db is None:
            with self.repository.transaction(False) as connection:
                return self.names(instance, connection)
        return {
            row[0]: True
            for row in db.execute(
                'SELECT name FROM plugin_secrets WHERE instance_id=%s', (instance,)
            )
        }

    def save(self, instance, name, value, db):
        if value is None:
            db.execute(
                'DELETE FROM plugin_secrets WHERE instance_id=%s AND name=%s', (instance, name)
            )
            return
        nonce = os.urandom(12)
        encrypted = nonce + self.cipher.encrypt(
            nonce, value.encode(), (instance + ":" + name).encode()
        )
        db.execute(
            'INSERT INTO plugin_secrets VALUES(%s,%s,%s) ON CONFLICT(instance_id,name) DO UPDATE SET value=excluded.value',
            (instance, name, encrypted),
        )

    def resolve(self, instance, name):
        with self.repository.transaction(False) as db:
            row = db.execute(
                'SELECT value FROM plugin_secrets WHERE instance_id=%s AND name=%s', (instance, name)
            ).fetchone()
        require(row is not None, "config_invalid", 400)
        return self.unseal(instance, name, bytes(row[0]))

    def snapshot(self, instance):
        with self.repository.transaction(False) as db:
            return {
                row[0]: base64.b64encode(bytes(row[1])).decode()
                for row in db.execute(
                    'SELECT name,value FROM plugin_secrets WHERE instance_id=%s', (instance,)
                )
            }

    def unseal(self, instance, name, value):
        try:
            return self.cipher.decrypt(
                value[:12], value[12:], (instance + ":" + name).encode()
            ).decode()
        except Exception:
            raise Fault("plugin_store_unavailable", 503) from None
