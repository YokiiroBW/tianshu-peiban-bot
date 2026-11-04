"""Revisioned configuration with sealed secrets; business validation stays in each domain."""

from .contracts import canonical, Fault


class Configuration:
    KINDS = frozenset(
        {
            "speech_connection",
            "voice_profile",
            "dictionary",
            "image_engine",
            "image_profile",
            "image_workflow",
            "reaction_pack",
            "content_provider",
            "device_connection",
            "game_connection",
            "weather_connection",
            "catalog_actor",
            "catalog_source",
            "asset_connection",
            "archive_connection",
        }
    )

    def __init__(self, database, credentials):
        self.database, self.credentials = database, credentials

    async def initialize(self):
        # The deployment owns the one external master key. Reading non-secret
        # settings works before a provider/account has been configured.
        return None

    def seal(self, raw, reference):
        return self.credentials.seal_bytes(
            raw, domain="capabilities.configuration", reference=reference
        )

    def unseal(self, raw, reference):
        return self.credentials.unseal_bytes(
            raw, domain="capabilities.configuration", reference=reference
        )

    async def list(self, kind):
        if kind not in self.KINDS:
            raise ValueError("configuration_kind")
        async with self.database.transaction(read_only=True) as conn:
            cursor = await conn.execute(
                "SELECT body FROM capabilities.configuration WHERE kind=%s ORDER BY id", (kind,)
            )
            return [row["body"] for row in await cursor.fetchall()]

    async def get(self, kind, identifier):
        async with self.database.transaction(read_only=True) as conn:
            cursor = await conn.execute(
                "SELECT body FROM capabilities.configuration WHERE kind=%s AND id=%s",
                (kind, identifier),
            )
            row = await cursor.fetchone()
        return row["body"] if row else None

    async def availability_revisions(self):
        """Local change tokens; no provider, credential or network access."""
        kinds = ("speech_connection", "voice_profile", "dictionary", "image_engine",
                 "image_profile", "image_workflow", "content_provider", "device_connection",
                 "game_connection", "weather_connection", "asset_connection", "archive_connection")
        async with self.database.transaction(read_only=True) as conn:
            rows = await (await conn.execute(
                "SELECT kind,id,revision FROM capabilities.configuration WHERE kind=ANY(%s) "
                "ORDER BY kind,id", (list(kinds),))).fetchall()
        revisions = {kind: [] for kind in kinds}
        for row in rows:
            revisions[row["kind"]].append((row["id"], row["revision"]))
        return {kind: tuple(values) for kind, values in revisions.items()}

    async def save(
        self, kind, identifier, value, expected_revision, *, secret=None, connection=None
    ):
        if kind not in self.KINDS:
            raise ValueError("configuration_kind")
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.save(
                    kind, identifier, value, expected_revision, secret=secret, connection=conn
                )
        conn = connection
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (kind + ":" + identifier,))
        cursor = await conn.execute(
            "SELECT revision FROM capabilities.configuration WHERE kind=%s AND id=%s FOR UPDATE",
            (kind, identifier),
        )
        old = await cursor.fetchone()
        revision = old["revision"] if old else 0
        if expected_revision != revision:
            raise Fault("revision_conflict", 409)
        value = {**value, "id": identifier, "revision": revision + 1}
        await conn.execute(
            "INSERT INTO capabilities.configuration(kind,id,revision,body) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
            (kind, identifier, revision + 1, canonical(value)),
        )
        if secret is not None:
            encrypted = self.seal(canonical(secret).encode(), kind + ":" + identifier)
            await conn.execute(
                "UPDATE capabilities.configuration SET secret=%s WHERE kind=%s AND id=%s",
                (encrypted, kind, identifier),
            )
        return value

    async def seal_command(self, context, identifier, body):
        from .contracts import digest

        fingerprint = digest(body)
        encrypted = self.seal(canonical(body).encode(), context.principal_id + ":" + identifier)
        async with self.database.transaction() as conn:
            await conn.execute(
                "INSERT INTO capabilities.command_inputs(id,principal_id,fingerprint,body) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identifier, context.principal_id, fingerprint, encrypted),
            )
            row = await (
                await conn.execute(
                    "SELECT fingerprint FROM capabilities.command_inputs WHERE id=%s AND principal_id=%s",
                    (identifier, context.principal_id),
                )
            ).fetchone()
            if not row or row["fingerprint"] != fingerprint:
                raise Fault("idempotency_conflict", 409)
        return {"command_ref": identifier, "body_digest": fingerprint}

    async def command(self, context, arguments):
        from .contracts import loads

        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT body,fingerprint FROM capabilities.command_inputs WHERE id=%s AND principal_id=%s",
                    (arguments["command_ref"], context.principal_id),
                )
            ).fetchone()
        if not row or row["fingerprint"] != arguments["body_digest"]:
            raise Fault("command_input_unavailable", 503)
        raw = bytes(row["body"])
        return loads(self.unseal(raw, context.principal_id + ":" + arguments["command_ref"]))

    async def secret(self, kind, identifier):
        async with self.database.transaction(read_only=True) as conn:
            cursor = await conn.execute(
                "SELECT secret FROM capabilities.configuration WHERE kind=%s AND id=%s",
                (kind, identifier),
            )
            row = await cursor.fetchone()
        if not row or not row["secret"]:
            raise Fault("not_configured", 503)
        raw = bytes(row["secret"])
        from .contracts import loads

        return loads(self.unseal(raw, kind + ":" + identifier))
