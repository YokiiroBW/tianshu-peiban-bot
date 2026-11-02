"""Closed local days become diary work only when their actual material changes."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from tianshu.conversation.codec import context_from_record, document, signature
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import OriginKind


UNRESOLVED = frozenset({"collecting", "generating", "unknown", "cancelling"})


def diary_experience(episode):
    actual = episode.actual_result or {}
    return (episode.origin_kind in {OriginKind.HUMAN, OriginKind.OBSERVATION, OriginKind.TOOL_RESULT, OriginKind.ACTOR}
            and not actual.get("internal") and actual.get("derived_kind") not in {"reflection", "diary"})


def automatic_view(row, diary):
    pending = row["reviewed_revision"] < row["revision"]
    active = row["generation_id"] == diary["generation_id"] and row["generation_id"]
    error = row["last_error"] or (diary["reason"] if active and diary["state"] in {"failed","cancelled"} else None)
    state = (diary["state"] if active and diary["state"] in UNRESOLVED
             else "blocked" if error else "pending" if pending else "reviewed")
    return {"state":state,"day":row["day"].isoformat(),"timezone":row["timezone"],
            "material_revision":row["revision"],"reviewed_revision":row["reviewed_revision"],
            "attempt_id":row["generation_id"],"closed_at":row["closed_at"],
            "last_reviewed_at":row["last_reviewed_at"],"last_error":error}


class DiaryAutomation:
    def __init__(self, diaries):
        self.diaries, self.life = diaries, diaries.life

    async def changed(self, context, watermark, *, episode=None, connection=None):
        if episode is None or not diary_experience(episode) or not episode.source_refs:
            return
        if connection is None:
            await self.life.identity.check(context, episode.source_refs)
            async with self.life.database.transaction() as transaction:
                return await self.changed(context, watermark, episode=episode, connection=transaction)
        await self.life.validate_persistence(connection, context, episode.source_refs)
        # No daily polling or history seed: day_closed owns the initial review.
        # Later events wake only an already closed day whose author could read it.
        rows = await (await connection.execute(
            "UPDATE life.diary_days SET material_revisions=jsonb_set(material_revisions,ARRAY[%s],to_jsonb(%s::bigint)),"
            "revision=revision+1,last_error=NULL,updated_at=now() WHERE actor_id=%s "
            "AND day=(%s::timestamptz AT TIME ZONE timezone)::date AND scope_ids @> %s::text[] "
            "AND coalesce((material_revisions->>%s)::bigint,0)<%s RETURNING *",
            (episode.episode_id, episode.revision, context.actor_id, episode.occurred_at,
             list(episode.scope_ids), episode.episode_id, episode.revision))).fetchall()
        for row in rows:
            await self.enqueue(connection, row, delay=30)

    async def day_closed(self, context, day, *, connection=None):
        try:
            day = date.fromisoformat(day) if isinstance(day, str) else day
            if not isinstance(day, date) or isinstance(day, datetime):
                raise ValueError
        except (TypeError, ValueError):
            raise AppError("invalid_diary_day") from None
        if connection is None:
            await self.life.identity.check(context)
            async with self.life.database.transaction() as transaction:
                return await self.day_closed(context, day, connection=transaction)
        await self.life.validate_persistence(connection, context)
        state = await (await connection.execute(
            "SELECT timezone FROM life.routines WHERE actor_id=%s", (context.actor_id,))).fetchone()
        if state is None:
            state = await self.life._state(context, connection)
        scope = signature(sorted(context.scope_ids))
        row = await (await connection.execute(
            "INSERT INTO life.diary_days(actor_id,scope_key,day,timezone,context,scope_ids) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key,day) DO UPDATE SET updated_at=now() RETURNING *",
            (context.actor_id, scope, day, state["timezone"], document(context), list(context.scope_ids)))).fetchone()
        if row["reviewed_revision"] < row["revision"] or row["generation_id"]:
            await self.enqueue(connection, row)
        return {"day": day.isoformat(), "state": "queued" if row["reviewed_revision"] < row["revision"] else "reviewed"}

    async def enqueue(self, connection, row, *, delay=0, reason="material"):
        await self.life.jobs.enqueue(connection, JobSpec(
            "life.diary_review", f"diary-review:{row['actor_id']}:{row['scope_key']}:{row['day']}:{row['revision']}:{uuid4()}",
            {"actor_id": row["actor_id"], "scope_key": row["scope_key"], "day": row["day"].isoformat(),
             "expected_revision": row["revision"]}, scheduled_at=datetime.now(UTC)+timedelta(seconds=delay),
            subject_id=row["actor_id"]),queueing_lock=f"diary-review:{row['actor_id']}:{row['scope_key']}:{row['day']}:{row['revision']}")

    async def review(self, actor_id, scope_key, day, expected_revision):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.diary_days WHERE actor_id=%s AND scope_key=%s AND day=%s",
                (actor_id, scope_key, date.fromisoformat(day)))).fetchone()
        if not row or (not row["generation_id"] and row["reviewed_revision"] >= row["revision"]):
            return {"state": "reviewed"}
        if not row["generation_id"] and row["blocked_revision"]>=row["revision"]:
            return {"state":"blocked","reason":row["last_error"]}
        previous = context_from_record(row["context"])
        try:
            context = await self.life.identity.refresh_scheduled_context(previous, permission="life.manage")
            # A current epoch grants observation, never a different target or audience.
            self.diaries.generations.observer_context(previous, context)
            async with self.life.database.transaction(read_only=True) as connection:
                diary = await (await connection.execute(
                    "SELECT * FROM life.diaries WHERE actor_id=%s AND day=%s", (actor_id, row["day"]))).fetchone()
            if diary and not set(diary["scope_ids"]).issubset(context.scope_ids):
                raise AppError("diary_scope_unavailable", status=403)
            if diary and diary["state"] in UNRESOLVED:
                if diary["state"] == "collecting":
                    return {"state": "collecting", "attempt_id": diary["generation_id"]}
                if (row["generation_id"]==diary["generation_id"] and row["generation_revision"]<row["revision"]
                        and not diary["generation_cancel_requested"]):
                    receipt=await self.diaries.models.status(diary["generation_id"])
                    if not receipt or receipt.status not in {"queued","running"}:
                        from .computation_retry import freeze_recipe
                        from tianshu.conversation.codec import source_refs
                        async with self.life.database.transaction() as connection:
                            if diary["generation_recipe"] is not None:
                                await freeze_recipe(self.life,connection,"diary",diary["id"],diary["generation_id"],context,
                                    diary["generation_recipe"],source_refs(diary["source_refs"]))
                        await self.diaries._generation_state(diary["id"],diary["generation_id"],"failed","diary_material_changed",settle=True)
                        return {"state":"changed_since_review"}
                return await self.diaries.generate(diary["id"], diary["generation_id"])
            if row["generation_id"]:
                async with self.life.database.transaction() as connection:
                    await self.settle(connection, row["generation_id"],
                        error=diary.get("reason") if diary and diary["state"] in {"failed", "cancelled"} else None)
                # A later event has its own queued review. This job only settles its original.
                return {"state": "blocked" if diary and diary["state"] in {"failed","cancelled"} else "reviewed"}
            result = await self.diaries.request(context,
                {"client_request_id": "auto-diary:"+signature((actor_id,scope_key,day,row["revision"])),
                 "day": day, "expected_revision": diary["revision"] if diary else 0},
                automatic={"actor_id": actor_id, "scope_key": scope_key, "day": day,
                           "revision": row["revision"], "timezone": row["timezone"]})
            return result.data
        except AppError as error:
            if error.code in {"diary_generation_unresolved", "revision_conflict", "diary_review_no_longer_current"}:
                return {"state": "changed_since_review"}
            async with self.life.database.transaction() as connection:
                await connection.execute(
                    "UPDATE life.diary_days SET last_error=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND day=%s",
                    (error.code, actor_id, scope_key, row["day"]))
            raise

    async def matching_day(self, connection, context, day, *, scope_ids=(), timezone=None):
        """Resolve an existing author's day without widening its original scene."""
        scope_key = signature(sorted(context.scope_ids))
        scene = {key:getattr(context,key) for key in (
            "principal_id","actor_id","person_id","conversation_id","conversation_kind","delivery_allowed")}
        rows = await (await connection.execute(
            "SELECT * FROM life.diary_days WHERE actor_id=%s AND day=%s AND scope_ids<@%s::text[] "
            "AND scope_ids @> %s::text[] AND context @> %s::jsonb AND context->'audience'<@%s::jsonb "
            "AND (%s::text IS NULL OR timezone=%s) ORDER BY (scope_key=%s) DESC,scope_key LIMIT 2 FOR UPDATE",
            (context.actor_id,day,list(context.scope_ids),list(scope_ids),document(scene),document(context.audience),
             timezone,timezone,scope_key))).fetchall()
        if not rows:
            return None
        # The original exact scene remains authoritative. A wider author view
        # may identify one narrower scene, never choose among several of them.
        if len(rows)>1 and rows[0]["scope_key"] != scope_key:
            raise AppError("diary_day_scope_ambiguous",status=409)
        row = rows[0]
        original = context_from_record(row["context"])
        if set(original.scope_ids) != set(row["scope_ids"]) or signature(sorted(original.scope_ids)) != row["scope_key"]:
            return None
        reader = await self.diaries._reader(original)
        await self.life.validate_persistence(connection,reader)
        return row,reader

    async def bind(self, connection, automatic, generation_id, *, context=None, day=None):
        refresh = automatic is None
        if refresh:
            if context is None or day is None:
                return None,context
            matched = await self.matching_day(connection,context,day)
            if matched is None:
                return None,context
            row,context = matched
            automatic = {"actor_id":row["actor_id"],"scope_key":row["scope_key"],"day":row["day"].isoformat(),
                         "revision":row["revision"],"timezone":row["timezone"]}
        row = await (await connection.execute(
            "UPDATE life.diary_days SET generation_id=%s,generation_revision=%s,last_error=NULL,updated_at=now() "
            "WHERE actor_id=%s AND scope_key=%s AND day=%s AND revision>=%s AND (%s OR reviewed_revision<%s) "
            "AND generation_id IS NULL RETURNING actor_id",
            (generation_id, automatic["revision"], automatic["actor_id"], automatic["scope_key"],
             date.fromisoformat(automatic["day"]), automatic["revision"], refresh, automatic["revision"]))).fetchone()
        if row is None:
            raise AppError("diary_review_no_longer_current", status=409)
        return automatic,context

    async def settle(self, connection, generation_id, *, error=None, recipe=None, reviewed_at=None):
        rows = await (await connection.execute(
            "SELECT * FROM life.diary_days WHERE generation_id=%s FOR UPDATE", (generation_id,))).fetchall()
        for row in rows:
            revisions = dict(row["material_revisions"])
            for identifier, version in (recipe or {}).get("material_revisions", ()):
                revisions[identifier] = max(version, revisions.get(identifier, 0))
            current = await (await connection.execute(
                "UPDATE life.diary_days SET reviewed_revision=CASE WHEN %s THEN reviewed_revision ELSE greatest(reviewed_revision,generation_revision) END,"
                "blocked_revision=CASE WHEN %s THEN greatest(blocked_revision,generation_revision) ELSE blocked_revision END,"
                "generation_id=NULL,generation_revision=NULL,material_revisions=%s,last_reviewed_at=CASE WHEN %s THEN last_reviewed_at ELSE COALESCE(%s,now()) END,"
                "last_error=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND day=%s RETURNING *",
                (bool(error),bool(error),document(revisions),bool(error),reviewed_at,error, row["actor_id"], row["scope_key"], row["day"]))).fetchone()
            if max(current["reviewed_revision"],current["blocked_revision"]) < current["revision"]:
                await self.enqueue(connection, current, reason="after-original")

    async def recover(self):
        # Only registered closed days are recovered; an installation/restart does
        # not invent missing days or ask the model to recreate a history.
        async with self.life.database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.diary_days WHERE (reviewed_revision<revision AND blocked_revision<revision) OR generation_id IS NOT NULL "
                "ORDER BY day,actor_id,scope_key FOR UPDATE")).fetchall()
            for row in rows:
                await self.enqueue(connection, row, reason="recover:"+datetime.now(UTC).isoformat())
