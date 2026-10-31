"""Write-only credentials sealed with the deployment's external master key."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import AppError


class CredentialStore:
    def __init__(self, database, secrets, master_key_ref="TIANSHU_MASTER_KEY"):
        self.database, self.secrets, self.master_key_ref = database, secrets, master_key_ref

    def _cipher(self):
        try:
            key = base64.urlsafe_b64decode(self.secrets.resolve(self.master_key_ref))
        except (ValueError, AppError):
            raise AppError("credential_master_not_configured", status=503) from None
        if len(key) != 32:
            raise AppError("credential_master_invalid", status=503)
        return AESGCM(key)

    async def put(self, connection, reference, value):
        if not reference.startswith("sealed:") or not isinstance(value, str) or not 1 <= len(value) <= 16384:
            raise AppError("invalid_credential")
        nonce = os.urandom(12)
        encrypted = self._cipher().encrypt(nonce, value.encode(), reference.encode())
        await connection.execute("INSERT INTO runtime.credentials(reference,nonce,ciphertext) "
            "VALUES(%s,%s,%s) ON CONFLICT(reference) DO UPDATE SET nonce=excluded.nonce,"
            "ciphertext=excluded.ciphertext,revision=runtime.credentials.revision+1,updated_at=now()",
            (reference, nonce, encrypted))

    async def resolve(self, reference):
        if not reference.startswith("sealed:"):
            return self.secrets.resolve(reference)
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (await conn.execute("SELECT nonce,ciphertext FROM runtime.credentials "
                                           "WHERE reference=%s", (reference,))).fetchone()
        if not row:
            raise AppError("credential_not_configured", status=503)
        try:
            return self._cipher().decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]),
                                          reference.encode()).decode()
        except Exception:
            raise AppError("credential_unseal_failed", status=503) from None
