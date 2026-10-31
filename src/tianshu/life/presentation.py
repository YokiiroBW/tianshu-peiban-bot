"""The room and wardrobe are fictional state; image engines consume one checked scene snapshot."""
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.types import MediaRef
from tianshu.sources.dependencies import unique_refs
from tianshu.conversation.codec import document, signature, source_refs
from .validation import revision, text, timezone, timestamp


class LifePresentation:
    def __init__(self, life, *, media):
        self.life, self.media = life, media

    async def _reference(self, context, value):
        try:
            supplied = MediaRef(**value)
        except (TypeError, ValueError):
            raise AppError("invalid_media_reference") from None
        artifact = await self.media.reference(context, supplied.media_id)
        if artifact.media != supplied or not artifact.media.mime_type.startswith("image/"):
            raise AppError("image_reference_required")
        return artifact

    async def outfits(self, context, *, cursor=None, limit=40):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.outfits WHERE actor_id=%s AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, list(context.scope_ids), cursor, cursor, limit + 1))).fetchall()
        return await self.life._page(context, rows, limit)

    async def save_outfit(self, context, body):
        await self.life.identity.check(context)
        description = text(body.get("description"), "服装描述", 1000)
        appearance = text(body.get("appearance"), "服装外观", 8000)
        reference = body.get("reference")
        artifact = await self._reference(context, reference) if reference is not None else None
        refs = artifact.source_refs if artifact else ()
        async def apply(connection):
            outfit_id = body.get("id") or str(uuid4())
            row = await self.life.lock_row(connection, context, "SELECT * FROM life.outfits WHERE id=%s FOR UPDATE", (outfit_id,), additional_refs=refs)
            if row and (row["actor_id"] != context.actor_id or not set(row["scope_ids"]).issubset(context.scope_ids)):
                raise AppError("outfit_not_found", status=404)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            next_revision = row["revision"] + 1 if row else 1
            await connection.execute(
                "INSERT INTO life.outfits(id,actor_id,description,appearance,reference,activity_kinds,scope_ids) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET description=excluded.description,appearance=excluded.appearance,reference=excluded.reference,"
                "activity_kinds=excluded.activity_kinds,revision=%s",
                (outfit_id, context.actor_id, description, appearance, document(reference) if reference else None,
                 body.get("activity_kinds", []), list(context.scope_ids), next_revision))
            await connection.execute("UPDATE life.outfits SET source_refs=%s WHERE id=%s", (document(refs), outfit_id))
            return CommandResult(body["client_request_id"], {"id": outfit_id, "revision": next_revision}, next_revision)
        return await self.life.commands.execute(context, "life.outfits.save", body["client_request_id"], signature(body), apply)

    async def select_outfit(self, context, body):
        await self.life.identity.check(context)
        async def apply(connection):
            row = await self.life.lock_row(connection, context,
                "SELECT * FROM life.outfits WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[] FOR UPDATE",
                (body.get("id"), context.actor_id, list(context.scope_ids)))
            if not row:
                raise AppError("outfit_not_found", status=404)
            state = await (await connection.execute("SELECT * FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))).fetchone()
            revision(state, body.get("expected_revision"))
            await connection.execute("UPDATE life.actor_state SET outfit_id=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s", (row["id"], context.actor_id))
            return CommandResult(body["client_request_id"], {"outfit_id": row["id"]}, state["revision"] + 1)
        return await self.life.commands.execute(context, "life.outfits.select", body["client_request_id"], signature(body), apply)

    async def delete_outfit(self, context, body):
        await self.life.identity.check(context)
        async def apply(connection):
            row = await self.life.lock_row(connection, context,
                "SELECT * FROM life.outfits WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[] FOR UPDATE",
                (body.get("id"), context.actor_id, list(context.scope_ids)))
            if not row:
                raise AppError("outfit_not_found", status=404)
            revision(row, body.get("expected_revision"))
            await connection.execute("UPDATE life.actor_state SET outfit_id=NULL,revision=revision+1 WHERE actor_id=%s AND outfit_id=%s", (context.actor_id, row["id"]))
            await connection.execute("DELETE FROM life.outfits WHERE id=%s", (row["id"],))
            return CommandResult(body["client_request_id"], {"id": row["id"], "removed": True}, row["revision"]+1)
        return await self.life.commands.execute(context, "life.outfits.delete", body["client_request_id"], signature(body), apply)

    async def room(self, context):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            state = await self.life._state(context, connection)
        return {"actor_id": context.actor_id, "revision": state["revision"], "timezone": state["timezone"],
                "world_id": state["world_id"], "room_id": state["room_id"], "controls": state["room_controls"], "weather_location": state["weather_location"],
                "fictional": True, "observed_at": datetime.now(UTC)}

    async def save_room(self, context, body):
        await self.life.identity.check(context)
        controls = body.get("controls", {})
        if not isinstance(controls, dict) or set(controls) - {"window", "sheer", "curtain", "desk_light", "ceiling_light"}:
            raise AppError("invalid_room_control")
        for key, value in controls.items():
            if key in {"desk_light", "ceiling_light"}:
                valid = type(value) is bool
            else:
                valid = type(value) in {int, float} and 0 <= value <= 1
            if not valid:
                raise AppError("invalid_room_control")
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (context.actor_id,))).fetchone()
            if not row:
                raise AppError("actor_not_initialized", status=409)
            if body.get("id") and row is None:
                raise AppError("object_not_found", status=404)
            revision(row, body.get("expected_revision"))
            selected = {**row["room_controls"], **controls}
            zone = timezone(body.get("timezone", row["timezone"]))
            location = body.get("weather_location", row["weather_location"])
            if location is not None:
                location = text(location, "天气位置", 128)
            await connection.execute("UPDATE life.actor_state SET room_controls=%s,timezone=%s,weather_location=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s", (document(selected), zone, location, context.actor_id))
            return CommandResult(body["client_request_id"], {"controls": selected, "timezone": zone, "weather_location": location, "fictional": True}, row["revision"] + 1)
        return await self.life.commands.execute(context, "life.room.save", body["client_request_id"], signature(body), apply)

    async def scene(self, context, *, activity_id=None, outfit_id=None):
        state = await self.life.state(context)
        actor = await self.life.companion.actor(context)
        activity = state["activity"]
        if activity_id and (activity is None or activity["id"] != activity_id):
            async with self.life.database.transaction(read_only=True) as connection:
                activity = await (await connection.execute(
                    "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",
                    (activity_id, context.actor_id, list(context.scope_ids)))).fetchone()
            if not activity:
                raise AppError("activity_not_found", status=404)
            await self.life.identity.check(context, source_refs(activity["source_refs"]))
        weather = None
        if self.life.weather and state["weather_location"]:
            weather = await self.life.weather.observe(context, state["weather_location"])
            if weather and weather.get("expires_at") and timestamp(weather["expires_at"]) < datetime.now(UTC):
                weather = None
        now = datetime.now(UTC)
        outfit = state["outfit"]
        if outfit_id and (outfit is None or outfit["id"] != outfit_id):
            async with self.life.database.transaction(read_only=True) as connection:
                outfit = await (await connection.execute(
                    "SELECT * FROM life.outfits WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]",
                    (outfit_id, context.actor_id, list(context.scope_ids)))).fetchone()
            if not outfit:
                raise AppError("outfit_not_found", status=404)
            await self.life.identity.check(context, source_refs(outfit["source_refs"]))
        refs = unique_refs(source_refs((activity or {}).get("source_refs", ())), source_refs((outfit or {}).get("source_refs", ())), source_refs((weather or {}).get("source_refs", ())))
        references = []
        if outfit and outfit["reference"]:
            artifact = await self._reference(context, outfit["reference"])
            references.append(artifact.media)
            refs = unique_refs(refs, artifact.source_refs)
        return {"actor_id": context.actor_id, "revision": signature([actor["revision"], state["revision"],
                (activity or {}).get("revision"), (outfit or {}).get("revision"), weather]),
                "captured_at": now, "timezone": state["timezone"], "local_time": now.astimezone(ZoneInfo(state["timezone"])).isoformat(),
                "character": actor["name"], "activity": {key: activity[key] for key in ("id", "title", "kind", "state")} if activity else {},
                "place": {"world_id": state["world_id"], "room_id": state["room_id"]},
                "weather": weather, "outfit": {"id": outfit["id"], "description": outfit["description"], "appearance": outfit["appearance"]} if outfit else None,
                "references": tuple(references), "source_refs": tuple(refs)}

    async def sharing(self, context, *, action_id, window):
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            candidate = await (await connection.execute(
                "SELECT * FROM life.contact_candidates WHERE action_id=%s AND actor_id=%s AND state IN ('pending','waiting') ORDER BY created_at LIMIT 1", (action_id, context.actor_id))).fetchone()
        if not candidate:
            return {"decision": "archive", "target_ref": None, "due_at": None, "reason": "no_current_share_intent"}
        if candidate["context"]["principal_id"] != context.principal_id:
            raise AppError("forbidden", status=403)
        await self.life.identity.check(context, source_refs(candidate["source_refs"]))
        decision = self.life.attention.decide(candidate, await self.life.contacts.preferences(context), await self.life.contacts.history(context))
        return {"decision": decision.decision, "target_ref": decision.target_ref,
                "due_at": decision.due_at, "reason": decision.reason}
