"""Write-only credentials sealed with the deployment's external master key."""

import base64
import os
import struct

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

    @staticmethod
    def _aad(domain: str, reference: str) -> bytes:
        if not isinstance(domain, str) or not domain or len(domain) > 128:
            raise AppError("invalid_credential_domain")
        if not isinstance(reference, str) or not reference or len(reference) > 4096:
            raise AppError("invalid_credential_reference")
        parts = (domain.encode("utf-8"), reference.encode("utf-8"))
        return b"tianshu.sealed.v1" + b"".join(struct.pack("!I", len(part)) + part for part in parts)

    def seal_bytes(self, raw: bytes, *, domain: str, reference: str) -> bytes:
        """Seal domain-owned bytes using the deployment's sole external master key."""
        if not isinstance(raw, bytes):
            raise AppError("invalid_credential_bytes")
        nonce = os.urandom(12)
        return b"TS1" + nonce + self._cipher().encrypt(nonce, raw, self._aad(domain, reference))

    def unseal_bytes(self, sealed: bytes, *, domain: str, reference: str) -> bytes:
        aad = self._aad(domain, reference)
        if not isinstance(sealed, bytes) or len(sealed) < 31 or sealed[:3] != b"TS1":
            raise AppError("credential_unseal_failed", status=503)
        try:
            return self._cipher().decrypt(sealed[3:15], sealed[15:], aad)
        except Exception:
            raise AppError("credential_unseal_failed", status=503) from None

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
