"""Authored persona versions and scoped affect; no scoring or hidden second model loop."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.runtime.api import CommandResult, Page
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage
from tianshu.conversation.codec import document, signature, source_refs
from .types import AffectUpdate, Persona


def bounded_text(value, name, maximum=4000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise AppError("invalid_input", f"请检查{name}")
    return value


def require_revision(row, expected):
    if row is None:
        if expected not in (None, 0):
            raise AppError("revision_conflict", status=409)
    elif type(expected) is not int or expected != row["revision"]:
        raise AppError("revision_conflict", status=409, details={"revision": row["revision"]})


class CompanionService:
    def __init__(self, database, commands, identity, memory, *, actor_registry=None,
                 actor_changed=None):
        self.database, self.commands = database, commands
        self.identity, self.memory = identity, memory
        self.actor_registry = actor_registry or identity
        self.actor_changed = actor_changed

    async def roles(self, context, *, cursor=None, limit=50):
        await self.identity.check(context)
        limit = min(100, max(1, int(limit)))
        # Access to each actor is resolved by identity, never inferred from its name.
        allowed = await self.identity.actor_ids(context)
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT actor_id,name,enabled,revision,persona_id,persona_revision,model_function,capabilities "
                "FROM companion.actors WHERE actor_id=ANY(%s) AND deleted_at IS NULL "
                "AND (%s::text IS NULL OR actor_id>%s) ORDER BY actor_id LIMIT %s",
                (list(allowed), cursor, cursor, limit + 1))).fetchall()
        more = len(rows) > limit
        return Page(tuple(rows[:limit]), rows[limit - 1]["actor_id"] if more else None,
                    Coverage(not more, None, ()))

    async def actor(self, context):
        await self.identity.check(context)
        if not context.actor_id:
            raise AppError("actor_required")
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM companion.actors WHERE actor_id=%s AND deleted_at IS NULL",
                (context.actor_id,))).fetchone()
        if row is None:
            raise AppError("actor_not_found", status=404)
        return row

    async def save_actor(self, context, body):
        await self.identity.check(context)
        name = bounded_text(body.get("name"), "角色名称", 128)
        persona_id, persona_revision = body.get("persona_id"), body.get("persona_revision")
        capabilities = body.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise AppError("invalid_capabilities")
        enabled = body.get("enabled", False)
        if type(enabled) is not bool:
            raise AppError("invalid_input")

        async def apply(connection):
            actor_id = context.actor_id
            row = None
            if actor_id:
                row = await (await connection.execute(
                    "SELECT * FROM companion.actors WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
                if row is None:
                    raise AppError("actor_not_found", status=404)
            require_revision(row, body.get("expected_revision"))
            if persona_id:
                persona = await (await connection.execute(
                    "SELECT 1 FROM companion.persona_revisions WHERE persona_id=%s AND revision=%s",
                    (persona_id, persona_revision))).fetchone()
                if not persona:
                    raise AppError("persona_revision_not_found", status=404)
            if row is None:
                actor_id = await self.actor_registry.register_actor(context, connection=connection,
                                                                    enabled=enabled)
                await connection.execute(
                    "INSERT INTO companion.actors(actor_id,name,enabled,persona_id,persona_revision,model_function,capabilities) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (actor_id, name, enabled, persona_id, persona_revision,
                     body.get("model_function", "chat"), capabilities))
                revision = 1
            else:
                if enabled != row["enabled"]:
                    await self.actor_registry.set_actor_enabled(context, actor_id, enabled,
                                                                 connection=connection)
                revision = row["revision"] + 1
                await connection.execute(
                    "UPDATE companion.actors SET name=%s,enabled=%s,persona_id=%s,persona_revision=%s,"
                    "model_function=%s,capabilities=%s,revision=%s,updated_at=now() WHERE actor_id=%s",
                    (name, enabled, persona_id, persona_revision, body.get("model_function", row["model_function"]),
                     capabilities, revision, actor_id))
            if self.actor_changed:
                await self.actor_changed(context, actor_id, enabled, connection=connection)
            return CommandResult(body["client_request_id"], data={"actor_id": actor_id, "name": name,
                                 "enabled": enabled, "revision": revision}, revision=revision)
        return await self.commands.execute(context, "companion.roles.save", body["client_request_id"],
                                           signature(body), apply)

    async def delete_actor(self, context, body):
        await self.identity.check(context)
        async def apply(connection):
            row = await (await connection.execute(
                "SELECT revision FROM companion.actors WHERE actor_id=%s FOR UPDATE", (context.actor_id,))).fetchone()
            if not row:
                raise AppError("actor_not_found", status=404)
            require_revision(row, body.get("expected_revision"))
            await self.actor_registry.set_actor_enabled(context, context.actor_id, False,
                                                         connection=connection)
            await connection.execute(
                "UPDATE companion.actors SET enabled=false,deleted_at=now(),revision=revision+1 WHERE actor_id=%s",
                (context.actor_id,))
            if self.actor_changed:
                await self.actor_changed(context, context.actor_id, False, connection=connection)
            return CommandResult(body["client_request_id"], {"actor_id": context.actor_id,
                                 "deleted": True}, row["revision"] + 1)
        return await self.commands.execute(context, "companion.roles.delete", body["client_request_id"],
                                           signature(body), apply)

    async def persona_catalog(self, context):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT persona_id,name,revision,current_revision FROM companion.persona_profiles ORDER BY name,persona_id")).fetchall()
        return {"items": rows}

    async def persona(self, context, persona_id, revision=None):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT p.name,r.* FROM companion.persona_profiles p JOIN companion.persona_revisions r "
                "ON r.persona_id=p.persona_id AND r.revision=COALESCE(%s,p.current_revision) WHERE p.persona_id=%s",
                (revision, persona_id))).fetchone()
        if not row:
            raise AppError("persona_not_found", status=404)
        return Persona(row["persona_id"], row["revision"], row["name"], row["author_text"],
                       row["tone"], row["style"], row["address"], row["world"])

    async def save_persona(self, context, body):
        await self.identity.check(context)
        name = bounded_text(body.get("name"), "人格名称", 128)
        text = bounded_text(body.get("author_text"), "完整作者人格", 200000)
        fields = {key: bounded_text(body.get(key, ""), key, 20000, empty=True)
                  for key in ("tone", "style", "address", "world", "note")}
        async def apply(connection):
            persona_id = body.get("persona_id") or str(uuid4())
            row = await (await connection.execute(
                "SELECT * FROM companion.persona_profiles WHERE persona_id=%s FOR UPDATE", (persona_id,))).fetchone()
            require_revision(row, body.get("expected_revision"))
            revision = row["revision"] + 1 if row else 1
            if row:
                await connection.execute(
                    "UPDATE companion.persona_profiles SET name=%s,revision=%s,current_revision=%s WHERE persona_id=%s",
                    (name, revision, revision, persona_id))
            else:
                await connection.execute(
                    "INSERT INTO companion.persona_profiles(persona_id,name) VALUES (%s,%s)", (persona_id, name))
            await connection.execute(
                "INSERT INTO companion.persona_revisions(persona_id,revision,author_text,tone,style,address,world,note) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (persona_id, revision, text, fields["tone"], fields["style"], fields["address"], fields["world"], fields["note"]))
            return CommandResult(body["client_request_id"], {"persona_id": persona_id, "revision": revision}, revision)
        return await self.commands.execute(context, "companion.personas.save", body["client_request_id"], signature(body), apply)

    async def persona_history(self, context, persona_id, *, cursor=None, limit=40):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT revision,note,created_at FROM companion.persona_revisions WHERE persona_id=%s "
                "AND (%s::bigint IS NULL OR revision<%s) ORDER BY revision DESC LIMIT %s",
                (persona_id, cursor, cursor, min(100, limit) + 1))).fetchall()
        more = len(rows) > limit
        return Page(tuple(rows[:limit]), str(rows[limit - 1]["revision"]) if more else None, Coverage(not more))

    async def apply_affect(self, context, update: AffectUpdate):
        await self.identity.check(context, update.source_refs)
        values = (update.valence, update.arousal, update.stress, update.social_need, update.confidence)
        if any(not math.isfinite(value) for value in values) or not -1 <= values[0] <= 1 or any(not 0 <= value <= 1 for value in values[1:]):
            raise AppError("invalid_affect")
        if not 60 <= update.half_life_seconds <= 604800:
            raise AppError("invalid_affect_half_life")
        async with self.database.transaction() as connection:
            prior = await (await connection.execute(
                "SELECT event_revision FROM companion.affect_events WHERE actor_id=%s AND event_id=%s FOR UPDATE",
                (context.actor_id, update.event_id))).fetchone()
            if prior and prior["event_revision"] >= update.event_revision:
                return {"applied": False, "reason": "event_already_appraised"}
            await connection.execute(
                "INSERT INTO companion.affect_events(actor_id,event_id,event_revision,scope_ids,appraisal,valence,arousal,stress,social_need,confidence,half_life_seconds,occurred_at,source_refs) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(actor_id,event_id) DO UPDATE SET "
                "event_revision=excluded.event_revision,scope_ids=excluded.scope_ids,appraisal=excluded.appraisal,"
                "valence=excluded.valence,arousal=excluded.arousal,stress=excluded.stress,social_need=excluded.social_need,"
                "confidence=excluded.confidence,half_life_seconds=excluded.half_life_seconds,occurred_at=excluded.occurred_at,source_refs=excluded.source_refs",
                (context.actor_id, update.event_id, update.event_revision, list(context.scope_ids), update.appraisal,
                 *values, update.half_life_seconds, update.occurred_at, document(update.source_refs)))
        return {"applied": True, "event_id": update.event_id}

    async def affect(self, context):
        await self.identity.check(context)
        now = datetime.now(UTC)
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.affect_events WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND occurred_at>%s ORDER BY occurred_at DESC LIMIT 64",
                (context.actor_id, list(context.scope_ids), now - timedelta(days=30)))).fetchall()
        totals = dict(valence=0.0, arousal=0.0, stress=0.0, social_need=0.0, confidence=0.0)
        feelings, missing = [], []
        for row in rows:
            try:
                await self.identity.check(context, source_refs(row["source_refs"]))
            except AppError as error:
                missing.append(error.code)
                continue
            weight = math.exp2(-max(0, (now - row["occurred_at"]).total_seconds()) / row["half_life_seconds"])
            if weight < .01:
                continue
            for key in totals:
                totals[key] += row[key] * weight
            feelings.append({"event_id": row["event_id"], "appraisal": row["appraisal"], "intensity": round(weight, 4)})
        totals = {key: round(max(-1 if key == "valence" else 0, min(1, value)), 4) for key, value in totals.items()}
        return {"actor_id": context.actor_id, **totals, "feelings": feelings,
                "observed_at": now, "missing": sorted(set(missing))}
