"""Persistent reading positions; fetched coverage and analysis are distinct."""
from dataclasses import replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef
from tianshu.conversation.codec import document, signature, source_refs
from tianshu.runtime.serialization import wire
from .validation import revision


class ReadingService:
    def __init__(self, life, reading_access):
        self.life, self.reading_access = life, reading_access

    async def list(self, context, *, cursor=None, limit=30):
        await self.life.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute("SELECT *,jsonb_build_array(source_ref) AS source_refs FROM life.readings WHERE actor_id=%s AND scope_ids<@%s::text[] AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s", (context.actor_id, list(context.scope_ids), cursor, cursor, limit+1))).fetchall()
        return await self.life._page(context, rows, limit)

    async def _reading(self, context, reading_id, connection, *, lock=False):
        query="SELECT *,jsonb_build_array(source_ref) AS source_refs FROM life.readings WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]"
        parameters=(reading_id,context.actor_id,list(context.scope_ids))
        row=(await self.life.lock_row(connection,context,query+" FOR UPDATE",parameters) if lock else await (await connection.execute(query,parameters)).fetchone())
        if not row:
            raise AppError("reading_not_found", status=404)
        return row

    async def open(self, context, body):
        raw = body.get("source_ref")
        if not isinstance(raw, dict):
            raise AppError("source_ref_required")
        source = SourceRef(raw["source_id"], raw["revision"], raw.get("locator", {}))
        await self.life.identity.check(context, (source,))
        requested = body.get("requested", {"position": (source.locator or {}).get("start", 0), "limit": 12000})
        if not isinstance(requested, dict):
            raise AppError("invalid_reading_range")
        reading_id = str(uuid5(NAMESPACE_URL, f"reading:{context.principal_id}:{context.actor_id}:{body['client_request_id']}"))
        async def apply(connection):
            await self.life.validate_persistence(connection,context,(source,))
            revision(None, body.get("expected_revision"))
            await connection.execute("INSERT INTO life.readings(id,actor_id,source_ref,requested,state,scope_ids) VALUES (%s,%s,%s,%s,'reading',%s)", (reading_id, context.actor_id, document(source), document(requested), list(context.scope_ids)))
            return CommandResult(body["client_request_id"], {"id": reading_id, "revision": 1,
                "source_ref": wire(source), "state": "reading", "continuation": {
                    "capability_id": "life.reading", "arguments": {"operation": "read", "id": reading_id,
                        "expected_revision": 1, "requested": requested}}}, 1)
        async def replay(connection, original):
            row = await self._reading(context, reading_id, connection)
            await self.life.validate_persistence(connection,context,source_refs((row["source_ref"],)))
            return replace(original, data=row, revision=row["revision"])
        return await self.life.commands.execute(context, "life.reading.open", body["client_request_id"], signature(body), apply, on_replay=replay)

    @staticmethod
    def _result(row, fetched, requested, current_revision, state):
        position = fetched["next_position"]
        return {"id": row["id"], **fetched, "source_ref": row["source_ref"],
                "revision": current_revision, "state": state, "analysis_completed": False,
                "completion_basis": "source_read_not_understanding",
                "continuation": {"capability_id": "life.reading", "arguments": {
                    "operation": "read", "id": row["id"], "expected_revision": current_revision,
                    "requested": {**requested, "position": position}}} if position is not None and state == "reading" else None}

    async def read(self,context,body):
        async with self.life.database.transaction(read_only=True) as connection:
            initial=await self._reading(context,body.get("id"),connection)
        source=source_refs((initial["source_ref"],))[0]
        await self.life.identity.check(context,(source,))
        previous=next((item for item in initial["covered"] if item["client_request_id"]==body["client_request_id"]),None)
        if previous is None:
            revision(initial, body.get("expected_revision"))
            if initial["state"] != "reading":
                raise AppError("reading_not_active", status=409)
        requested=previous["request"] if previous else body.get("requested") or ({**initial["requested"],"position":initial["next_position"]} if initial["next_position"] is not None else initial["requested"])
        fetched=await self.life.sources.read(context,source,requested)
        async def apply(connection):
            await self.life.validate_persistence(connection,context,(source,))
            row=await self._reading(context,initial["id"],connection,lock=True)
            revision(row,body.get("expected_revision"))
            if row["state"]!="reading":
                raise AppError("reading_not_active",status=409)
            covered=list(row["covered"])+[{"client_request_id":body["client_request_id"],"request":requested,"coverage":fetched["coverage"],"at":datetime.now(UTC)}]
            lower=(source.locator or {}).get("start",0)
            upper=(source.locator or {}).get("end",fetched["coverage"].total)
            cursor=lower
            ranges=sorted((part["start"],part["end"]) for item in covered for part in (item["coverage"].ranges if hasattr(item["coverage"],"ranges") else item["coverage"].get("ranges",())))
            for start,end in ranges:
                if start>cursor:
                    break
                cursor=max(cursor,end)
            completed=fetched["content"] is not None and upper is not None and cursor>=upper and fetched["next_position"] is None
            await connection.execute("UPDATE life.readings SET covered=%s,next_position=%s,state=%s,revision=revision+1,updated_at=now() WHERE id=%s",(document(covered),document(fetched["next_position"]),"completed" if completed else "reading",row["id"]))
            return CommandResult(body["client_request_id"],self._result(row,fetched,requested,row["revision"]+1,
                "completed" if completed else "reading"),row["revision"]+1)
        async def replay(connection,original):
            await self.life.validate_persistence(connection,context,(source,))
            row=await self._reading(context,initial["id"],connection)
            if not any(item["client_request_id"]==body["client_request_id"] for item in row["covered"]):
                raise AppError("reading_receipt_unavailable",status=409)
            return replace(original,data=self._result(row,fetched,requested,row["revision"],row["state"]),revision=row["revision"])
        return await self.life.commands.execute(context,"life.reading.read",body["client_request_id"],signature(body),apply,on_replay=replay)

    async def set_state(self, context, body, state):
        if state not in {"reading", "paused", "closed"}:
            raise AppError("invalid_reading_state")
        await self.life.identity.check(context)
        async def apply(connection):
            row = await self._reading(context, body.get("id"), connection, lock=True)
            revision(row, body.get("expected_revision"))
            await self.life.validate_persistence(connection,context,source_refs((row["source_ref"],)))
            if row["state"] in {"completed", "closed"} and state != "closed":
                raise AppError("reading_already_finished", status=409)
            await connection.execute("UPDATE life.readings SET state=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, row["id"]))
            return CommandResult(body["client_request_id"], {"id": row["id"], "state": state}, row["revision"]+1)
        return await self.life.commands.execute(context, "life.reading."+state, body["client_request_id"], signature(body), apply)

    async def shared(self, context, grant_id):
        return await self.reading_access.read(context, grant_id)
