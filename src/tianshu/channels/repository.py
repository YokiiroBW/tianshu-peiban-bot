"""Native PostgreSQL transport inbox/outbox references the one capability ledger."""

import json
from datetime import UTC, datetime
from tianshu.runtime.serialization import wire
from tianshu.capabilities.contracts import canonical, digest
from .contracts import ChannelError, TransportResult


class Repository:
    def __init__(self, runtime, identity, sources):
        self.actions = None
        self.recovery_policy = None
        self.statistics = None
        self.runtime, self.database, self.identity, self.sources = (
            runtime,
            runtime.database,
            identity,
            sources,
        )

    def seal(self, value, reference):
        return self.runtime.credentials.seal_bytes(
            canonical(wire(value)).encode(), domain="channels.transport", reference=reference
        )

    def unseal(self, value, reference):
        return json.loads(
            self.runtime.credentials.unseal_bytes(
                bytes(value), domain="channels.transport", reference=reference
            )
        )

    async def connection(self, identifier, context=None):
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT body FROM channels.connections WHERE id=%s", (identifier,)
                )
            ).fetchone()
        if not row:
            raise ChannelError("channel_not_configured", 503)
        body = row["body"]
        if context is not None:
            registered = await self.identity.connection(context, identifier)
            if registered["self_external_id"] != body["self_external_id"] or registered.get(
                "actor_id"
            ) != body.get("actor_id"):
                raise ChannelError("channel_registration_changed", 409)
        return body

    async def save_connection(self, context, body):
        registered = await self.identity.connection(context, body["id"])
        if body.get("token_ref") and body["token_ref"] != "sealed:channels:" + body["id"]:
            raise ChannelError("channel_credential_reference_not_owned", 400)
        adapter = body.get("adapter", registered.get("adapter", "onebot"))
        transport = body["transport"]
        allowed = {
            "onebot": {"http", "reverse_http", "ws", "reverse_ws"},
            "telegram": {"telegram_webhook", "telegram_poll"},
            "nonebot": {"bridge"},
            "astrbot": {"bridge"},
        }
        if adapter not in allowed or transport not in allowed[adapter]:
            raise ChannelError("channel_transport", 400)
        from urllib.parse import urlsplit

        endpoint = body.get("endpoint") or (
            "https://api.telegram.org" if adapter == "telegram" else None
        )
        if endpoint:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in ({"ws", "wss"} if transport == "ws" else {"http", "https"})
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ChannelError("channel_endpoint", 400)
        for candidate in (endpoint, body.get("api_endpoint")):
            if candidate:
                parsed = urlsplit(candidate)
                if (
                    parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or not parsed.hostname
                ):
                    raise ChannelError("channel_endpoint", 400)
        if transport in {"http", "ws", "bridge"} and not endpoint:
            raise ChannelError("channel_endpoint_required", 400)
        if body.get("provider", "generic") not in {
            "generic",
            "napcat",
            "snowluma",
            "telegram",
            "nonebot",
            "astrbot",
        }:
            raise ChannelError("channel_provider", 400)
        permitted = {
            "endpoint",
            "api_endpoint",
            "transport",
            "provider",
            "enabled",
            "extension_apis",
            "api_support",
            "segment_support",
            "media_path_mappings",
            "allowed_media_hosts",
            "max_media_bytes",
            "max_inline_bytes",
            "allow_at_all",
            "external_commands",
            "bridge_instance_id",
            "webhook_public_url",
            "webhook_secret_ref",
        }
        value = {key: body[key] for key in permitted if key in body}
        value["endpoint"] = endpoint
        value.update(
            id=body["id"],
            adapter=adapter,
            self_external_id=registered["self_external_id"],
            namespace=registered["namespace"],
            actor_id=registered.get("actor_id"),
            enabled=body.get("enabled", True),
        )
        async with self.database.transaction() as conn:
            old = await (
                await conn.execute(
                    "SELECT revision,secret_ref FROM channels.connections WHERE id=%s FOR UPDATE",
                    (body["id"],),
                )
            ).fetchone()
            revision = old["revision"] if old else 0
            if revision != body.get("expected_revision", 0):
                raise ChannelError("revision_conflict", 409)
            secret_ref = old["secret_ref"] if old else None
            if "token" in body:
                secret_ref = "sealed:channels:" + body["id"]
                await self.runtime.credentials.put(conn, secret_ref, body["token"])
            value.update(
                revision=revision + 1,
                configured=bool(secret_ref or body.get("token_ref")),
                token_ref=secret_ref or body.get("token_ref"),
                state="configured" if secret_ref or body.get("token_ref") else "not_configured",
            )
            if not value["configured"]:
                value["reason"] = "channel_token_required"
            await conn.execute(
                "INSERT INTO channels.connections(id,revision,body,secret_ref) VALUES(%s,%s,%s::jsonb,%s) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body,secret_ref=excluded.secret_ref,updated_at=now()",
                (value["id"], value["revision"], canonical(value), secret_ref),
            )
            from .supplements import wake_for_configuration
            await wake_for_configuration(self, conn, value["id"], value["revision"])
        return value

    async def token(self, connection):
        if not connection.get("token_ref"):
            raise ChannelError("channel_token_required", 503)
        return await self.runtime.credentials.resolve(connection["token_ref"])

    async def calls_for(self, action_id, attempt_id):
        async with self.database.transaction(read_only=True) as conn:
            return await (
                await conn.execute(
                    "SELECT * FROM channels.calls WHERE action_id=%s AND attempt_id=%s AND NOT control ORDER BY created_at,id",
                    (action_id, attempt_id),
                )
            ).fetchall()

    async def call(self, identifier):
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (
                await conn.execute("SELECT * FROM channels.calls WHERE id=%s", (identifier,))
            ).fetchone()
        if not row:
            raise ChannelError("channel_call_not_found", 404)
        return row

    async def prepare_call(
        self,
        action,
        attempt,
        connection_id,
        name,
        params,
        *,
        suffix="0",
        expected_count=1,
        control=False,
    ):
        identifier = digest([action.action_id, attempt.attempt_id, suffix])
        payload = self.seal(params, identifier + ":request")
        async with self.database.transaction() as conn:
            generation = await self.runtime.jobs.current_generation_on(conn)
            if not await self.sources.validate_persistence(
                conn, action.context, action.source_refs
            ):
                raise ChannelError("source_revoked", 410)
            await conn.execute(
                "INSERT INTO channels.calls(id,action_id,attempt_id,connection_id,expression_id,api_name,payload,expected_count,control,deployment_generation) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                (
                    identifier,
                    action.action_id,
                    attempt.attempt_id,
                    connection_id,
                    action.expression_id,
                    name,
                    payload,
                    expected_count,
                    control,
                    canonical(wire(generation)),
                ),
            )
            if action.source_refs:
                await conn.execute(
                    "INSERT INTO channels.source_dependencies(call_id,source_id,source_revision,refs) SELECT %s,item->>'source_id',(item->>'revision')::bigint,jsonb_agg(item) FROM jsonb_array_elements(%s::jsonb) item GROUP BY item->>'source_id',item->>'revision' ON CONFLICT(call_id,source_id,source_revision) DO UPDATE SET refs=channels.source_dependencies.refs||excluded.refs",
                    (identifier, canonical(wire(action.source_refs))),
                )
        return identifier

    async def settle(self, identifier, result):
        original = await self.call(identifier)
        action, _, _ = await self.actions.load_for_execution(original["action_id"])
        public = {
            "state": result.state,
            "retcode": result.retcode,
            "error_code": result.error_code,
            "message_ids": result.message_ids,
        }
        async with self.database.transaction(control=True) as conn:
            valid = await self.sources.validate_persistence(
                conn, action.context, action.source_refs
            )
            row = await (
                await conn.execute(
                    "SELECT state,payload,result FROM channels.calls WHERE id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if row["state"] == "confirmed":
                return
            if row["state"] in {"confirmed", "rejected"} and result.state not in {
                "confirmed",
                "rejected",
            }:
                return
            if not valid or row["payload"] is None:
                sealed = None
                public["error_code"] = "source_revoked"
            else:
                sealed = self.seal(result.raw, identifier + ":response")
            await conn.execute(
                "UPDATE channels.calls SET state=%s,response=%s,result=%s::jsonb,completed_at=CASE WHEN %s IN ('confirmed','rejected') THEN now() ELSE completed_at END WHERE id=%s",
                (result.state, sealed, canonical(public), result.state, identifier),
            )

    def outcome(self, row, *, include_data=False):
        value = row.get("result") or {}
        raw = (
            self.unseal(row["response"], row["id"] + ":response")
            if include_data and row.get("response")
            else {}
        )
        return TransportResult(
            value.get("state", "unknown" if row["state"] == "sending" else row["state"]),
            raw.get("data"),
            value.get("retcode"),
            value.get("error_code"),
            tuple(value.get("message_ids", ())),
            raw,
        )

    async def observe(self, connection_id, kind, name, body):
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "INSERT INTO channels.observations(connection_id,kind,name,body) VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(connection_id,kind,name) DO UPDATE SET body=excluded.body,observed_at=now()",
                (connection_id, kind, name, canonical(body)),
            )

    async def bind_event_sources(self, conn, event_key, refs):
        if not refs:
            return
        await conn.execute(
            "INSERT INTO channels.event_sources(event_key,source_id,source_revision,refs) "
            "SELECT %s,item->>'source_id',(item->>'revision')::bigint,jsonb_agg(item) "
            "FROM jsonb_array_elements(%s::jsonb) item GROUP BY item->>'source_id',item->>'revision' "
            "ON CONFLICT(event_key,source_id,source_revision) DO UPDATE SET refs=excluded.refs",
            (event_key, canonical(wire(refs))),
        )

    async def actors_for_source(self, conn, refs, reason="source_revoked"):
        if not refs:
            return ()
        from tianshu.sources.dependencies import changes_for, intersects
        changes, _ = changes_for(refs)
        identifiers, revisions = [r.source_id for r in refs], [r.revision for r in refs]
        calls = await (await conn.execute(
            "SELECT d.refs,a.action->'context'->>'actor_id' AS actor_id FROM channels.source_dependencies d "
            "JOIN channels.calls c ON c.id=d.call_id JOIN capabilities.actions a ON a.id=c.action_id "
            "WHERE (d.source_id,d.source_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (identifiers,revisions))).fetchall()
        events = await (await conn.execute(
            "SELECT d.refs,e.actor_id FROM channels.event_sources d "
            "JOIN channels.events e ON e.id=d.event_key "
            "WHERE (d.source_id,d.source_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (identifiers,revisions))).fetchall()
        actors = {row["actor_id"] for row in (*calls, *events) if intersects(row["refs"], changes, reason)}
        return tuple(sorted(actors, key=lambda value: "" if value is None else value))

    async def withdraw_sources(self, conn, refs, reason="source_revoked", *, actor_id=None, filter_actor=False):
        if not refs:
            return
        from tianshu.sources.dependencies import changes_for, intersects
        from tianshu.runtime.jobs import JobSpec
        from tianshu.types import ServiceClass

        changes, _ = changes_for(refs)
        rows = await (
            await conn.execute(
                "SELECT d.call_id,d.refs FROM channels.source_dependencies d JOIN channels.calls c ON c.id=d.call_id "
                "JOIN capabilities.actions a ON a.id=c.action_id WHERE (d.source_id,d.source_revision) IN "
                "(SELECT * FROM unnest(%s::text[],%s::bigint[]))" +
                (" AND a.action->'context'->>'actor_id' IS NOT DISTINCT FROM %s" if filter_actor else ""),
                ([r.source_id for r in refs], [r.revision for r in refs], actor_id) if filter_actor else
                ([r.source_id for r in refs], [r.revision for r in refs]),
            )
        ).fetchall()
        identifiers = sorted(
            {row["call_id"] for row in rows if intersects(row["refs"], changes, reason)}
        )
        await conn.execute(
            "UPDATE channels.calls SET payload=NULL,response=NULL,result=CASE WHEN state='prepared' "
            "THEN coalesce(result,'{}'::jsonb)||jsonb_build_object('state','rejected','error_code','source_revoked') "
            "ELSE result END,state=CASE WHEN state='prepared' THEN 'rejected' ELSE state END WHERE id=ANY(%s)",
            (identifiers,),
        )
        events = await (
            await conn.execute(
                "SELECT d.event_key,d.refs FROM channels.event_sources d JOIN channels.events e ON e.id=d.event_key "
                "WHERE (d.source_id,d.source_revision) IN "
                "(SELECT * FROM unnest(%s::text[],%s::bigint[]))" +
                (" AND e.actor_id IS NOT DISTINCT FROM %s" if filter_actor else ""),
                ([r.source_id for r in refs], [r.revision for r in refs], actor_id) if filter_actor else
                ([r.source_id for r in refs], [r.revision for r in refs]),
            )
        ).fetchall()
        event_keys = sorted(
            {row["event_key"] for row in events if intersects(row["refs"], changes, reason)}
        )
        if event_keys:
            await conn.execute(
                "UPDATE channels.events SET state='withdrawn',payload=NULL,outcome=NULL WHERE id=ANY(%s)",
                (event_keys,),
            )
            await conn.execute(
                "UPDATE channels.media_supplements SET state='withdrawn',payload=NULL,lease_until=NULL "
                "WHERE event_key=ANY(%s) AND state<>'complete'", (event_keys,))
        for identifier in identifiers:
            await self.runtime.jobs.enqueue(
                conn,
                JobSpec(
                    "channels.cancel",
                    identifier + ":source_revoked",
                    {"call_id": identifier},
                    service_class=ServiceClass.INTERACTIVE,
                ),
            )
