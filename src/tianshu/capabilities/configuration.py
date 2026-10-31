"""Revisioned configuration with sealed secrets; business validation stays in each domain."""
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .contracts import canonical,Fault

class Configuration:
    KINDS=frozenset({"speech_connection","voice_profile","dictionary","image_engine","image_profile","image_workflow","reaction_pack","content_provider","device_connection","game_connection"})
    def __init__(self,database,key_path):
        self.database,self.key_path=database,Path(key_path)
        self.cipher=None
    async def initialize(self):
        if not self.key_path.exists():
            async with self.database.transaction(read_only=True) as conn:
                row=await (await conn.execute("SELECT 1 FROM capabilities.configuration WHERE secret IS NOT NULL LIMIT 1")).fetchone()
            if row:raise Fault("credential_key_missing",503)
        self.open()
    def open(self):
        self.key_path.parent.mkdir(parents=True,exist_ok=True)
        try:
            with self.key_path.open("xb") as file:
                os.chmod(self.key_path,0o600);file.write(AESGCM.generate_key(bit_length=256));file.flush();os.fsync(file.fileno())
        except FileExistsError: pass
        self.cipher=AESGCM(self.key_path.read_bytes())
    async def list(self,kind):
        if kind not in self.KINDS: raise ValueError("configuration_kind")
        async with self.database.transaction(read_only=True) as conn:
            cursor=await conn.execute("SELECT body FROM capabilities.configuration WHERE kind=%s ORDER BY id",(kind,))
            return [row["body"] for row in await cursor.fetchall()]
    async def get(self,kind,identifier):
        async with self.database.transaction(read_only=True) as conn:
            cursor=await conn.execute("SELECT body FROM capabilities.configuration WHERE kind=%s AND id=%s",(kind,identifier));row=await cursor.fetchone()
        return row["body"] if row else None
    async def save(self,kind,identifier,value,expected_revision,*,secret=None,connection=None):
        if kind not in self.KINDS: raise ValueError("configuration_kind")
        if connection is None:
            async with self.database.transaction() as conn:return await self.save(kind,identifier,value,expected_revision,secret=secret,connection=conn)
        conn=connection
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(kind+":"+identifier,))
        cursor=await conn.execute("SELECT revision FROM capabilities.configuration WHERE kind=%s AND id=%s FOR UPDATE",(kind,identifier));old=await cursor.fetchone()
        revision=old["revision"] if old else 0
        if expected_revision!=revision: raise Fault("revision_conflict",409)
        value={**value,"id":identifier,"revision":revision+1}
        await conn.execute("INSERT INTO capabilities.configuration(kind,id,revision,body) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",(kind,identifier,revision+1,canonical(value)))
        if secret is not None:
            if self.cipher is None: raise Fault("credential_store_unavailable",503)
            nonce=os.urandom(12);encrypted=nonce+self.cipher.encrypt(nonce,canonical(secret).encode(),(kind+":"+identifier).encode())
            await conn.execute("UPDATE capabilities.configuration SET secret=%s WHERE kind=%s AND id=%s",(encrypted,kind,identifier))
        return value
    async def seal_command(self,context,identifier,body):
        from .contracts import digest
        if self.cipher is None:raise Fault("credential_store_unavailable",503)
        fingerprint=digest(body);nonce=os.urandom(12);aad=(context.principal_id+":"+identifier).encode()
        encrypted=nonce+self.cipher.encrypt(nonce,canonical(body).encode(),aad)
        async with self.database.transaction() as conn:
            await conn.execute("INSERT INTO capabilities.command_inputs(id,principal_id,fingerprint,body) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(identifier,context.principal_id,fingerprint,encrypted))
            row=await (await conn.execute("SELECT fingerprint FROM capabilities.command_inputs WHERE id=%s AND principal_id=%s",(identifier,context.principal_id))).fetchone()
            if not row or row["fingerprint"]!=fingerprint:raise Fault("idempotency_conflict",409)
        return {"command_ref":identifier,"body_digest":fingerprint}
    async def command(self,context,arguments):
        from .contracts import loads
        async with self.database.transaction(read_only=True) as conn:
            row=await (await conn.execute("SELECT body,fingerprint FROM capabilities.command_inputs WHERE id=%s AND principal_id=%s",(arguments["command_ref"],context.principal_id))).fetchone()
        if not row or row["fingerprint"]!=arguments["body_digest"]:raise Fault("command_input_unavailable",503)
        raw=bytes(row["body"])
        return loads(self.cipher.decrypt(raw[:12],raw[12:],(context.principal_id+":"+arguments["command_ref"]).encode()))
    async def secret(self,kind,identifier):
        if self.cipher is None: raise Fault("credential_store_unavailable",503)
        async with self.database.transaction(read_only=True) as conn:
            cursor=await conn.execute("SELECT secret FROM capabilities.configuration WHERE kind=%s AND id=%s",(kind,identifier));row=await cursor.fetchone()
        if not row or not row["secret"]: raise Fault("not_configured",503)
        raw=bytes(row["secret"])
        from .contracts import loads
        return loads(self.cipher.decrypt(raw[:12],raw[12:],(kind+":"+identifier).encode()))
