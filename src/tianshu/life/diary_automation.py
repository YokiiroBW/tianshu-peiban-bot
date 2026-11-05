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
            and not actual.get("internal") and not actual.get("technical_backfill")
            and actual.get("derived_kind") not in {"reflection", "diary", "technical_backfill"})


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

    async def changed(self, context, watermark, *, episode=None, connection=None, data_generation=None):
        if episode is None or not diary_experience(episode) or not episode.source_refs:
            return
        if connection is None:
            await self.life.identity.check(context, episode.source_refs)
            async with self.life.database.transaction() as transaction:
                return await self.changed(context, watermark, episode=episode, connection=transaction,data_generation=data_generation)
        if data_generation is not None:
            await self.life.jobs.assert_generation_on(connection,data_generation)
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
            await self.enqueue(connection, row, delay=30,data_generation=data_generation)

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
        scope = self.life.identity.scene_id(context)
        row = await (await connection.execute(
            "INSERT INTO life.diary_days(actor_id,scope_key,day,timezone,context,scope_ids) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key,day) DO UPDATE SET "
            "revision=life.diary_days.revision+CASE WHEN NOT (life.diary_days.scope_ids @> excluded.scope_ids "
            "AND life.diary_days.scope_ids <@ excluded.scope_ids AND (life.diary_days.context->'audience') @> (excluded.context->'audience') "
            "AND (life.diary_days.context->'audience') <@ (excluded.context->'audience')) THEN 1 ELSE 0 END,"
            "scope_ids=excluded.scope_ids,context=excluded.context,updated_at=now() RETURNING *",
            (context.actor_id, scope, day, state["timezone"], document(context), list(context.scope_ids)))).fetchone()
        if row["reviewed_revision"] < row["revision"] or row["generation_id"]:
            await self.enqueue(connection, row)
        return {"day": day.isoformat(), "state": "queued" if row["reviewed_revision"] < row["revision"] else "reviewed"}

    async def enqueue(self, connection, row, *, delay=0, reason="material", data_generation=None):
        if data_generation is None:
            data_generation = await self.life.jobs.current_generation_on(connection)
        await self.life.jobs.assert_generation_on(connection,data_generation)
        await self.life.jobs.enqueue(connection, JobSpec(
            "life.diary_review", f"diary-review:{row['actor_id']}:{row['scope_key']}:{row['day']}:{row['revision']}:{uuid4()}",
            {"actor_id": row["actor_id"], "scope_key": row["scope_key"], "day": row["day"].isoformat(),
             "expected_revision": row["revision"],"data_generation":data_generation}, scheduled_at=datetime.now(UTC)+timedelta(seconds=delay),
            subject_id=row["actor_id"],data_generation=data_generation),queueing_lock=f"diary-review:{row['actor_id']}:{row['scope_key']}:{row['day']}:{row['revision']}")

    async def review(self, actor_id, scope_key, day, expected_revision, data_generation):
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.diary_days WHERE actor_id=%s AND scope_key=%s AND day=%s",
                (actor_id, scope_key, date.fromisoformat(day)))).fetchone()
        if not row or (not row["generation_id"] and row["reviewed_revision"] >= row["revision"] and not row["last_error"]):
            return {"state": "reviewed"}
        previous = context_from_record(row["context"])
        try:
            context = await self.life.identity.refresh_scheduled_context(previous, permission="life.manage")
            # A current epoch grants observation, never a different target or audience.
            self.diaries.generations.observer_context(previous, context)
            async with self.life.database.transaction(read_only=True) as connection:
                diary = await (await connection.execute(
                    "SELECT * FROM life.diaries WHERE actor_id=%s AND scene_key=%s AND day=%s",
                    (actor_id, scope_key, row["day"]))).fetchone()
            if diary and not set(diary["scope_ids"]).issubset(context.scope_ids):
                raise AppError("diary_scope_unavailable", status=403)
            if (not row["generation_id"] and row["blocked_revision"]>=row["revision"]
                    and not (diary and diary["current_revision"])):
                return {"state":"blocked","reason":row["last_error"]}
            if diary and diary["state"] in UNRESOLVED:
                if diary["state"] == "collecting":
                    return {"state": "collecting", "attempt_id": diary["generation_id"]}
                if (row["generation_id"]==diary["generation_id"] and row["generation_revision"]<row["revision"]
                        and not diary["generation_cancel_requested"]):
                    receipt=await self.diaries.models.status(diary["generation_id"])
                    from .computation_retry import finished_unknown, freeze_recipe
                    if receipt is not None and (receipt.attempt_id != diary["generation_id"]
                            or receipt.data_generation != data_generation):
                        await self.diaries._observe_later(diary["id"],diary["generation_id"],actor_id,data_generation=data_generation)
                        return {"state":diary["state"],"attempt_id":diary["generation_id"],"reason":"model_observation_unavailable"}
                    completed_unknown = (finished_unknown(receipt,diary["generation_id"],data_generation)
                        and receipt.finished_at.utcoffset() is not None)
                    if (receipt is not None and receipt.status == "unknown"
                            and receipt.cancellation_reason != "explicit_cancel" and not completed_unknown):
                        await self.diaries._observe_later(diary["id"],diary["generation_id"],actor_id,data_generation=data_generation)
                        return {"state":diary["state"],"attempt_id":diary["generation_id"],"reason":"model_observation_unavailable"}
                    confirmed = (receipt is not None and receipt.attempt_id == diary["generation_id"]
                        and receipt.data_generation == data_generation and receipt.cancellation_reason != "explicit_cancel"
                        and (receipt.status in {"succeeded", "failed"} or completed_unknown))
                    if confirmed:
                        from tianshu.conversation.codec import source_refs
                        async with self.life.database.transaction() as connection:
                            await self.life.jobs.assert_generation_on(connection,data_generation)
                            current=await self.life.lock_row(connection,context,
                                "SELECT * FROM life.diaries WHERE id=%s FOR UPDATE",(diary["id"],))
                            if (not current or current["generation_id"]!=diary["generation_id"]
                                    or current["state"] not in UNRESOLVED or current["generation_cancel_requested"]):
                                return {"state":"changed_since_review"}
                            current_day=await (await connection.execute(
                                "SELECT * FROM life.diary_days WHERE actor_id=%s AND scope_key=%s AND day=%s FOR UPDATE",
                                (actor_id,scope_key,row["day"]))).fetchone()
                            if (not current_day or current_day["generation_id"]!=current["generation_id"]
                                    or current_day["generation_revision"] is None
                                    or current_day["generation_revision"]>=current_day["revision"]):
                                return {"state":"changed_since_review"}
                            if current["generation_recipe"] is not None:
                                await freeze_recipe(self.life,connection,"diary",current["id"],current["generation_id"],context,
                                    current["generation_recipe"],source_refs(current["source_refs"]))
                            if self.diaries.recovery is None:
                                raise AppError("recovery_policy_unavailable",status=503)
                            observed,_ = await self.diaries.recovery.observe_on(connection,current["generation_id"],receipt.status,
                                receipt.error_code,model_receipt=receipt)
                            if observed == "unregistered":
                                raise AppError("recovery_original_lineage_missing",status=409)
                            await self.diaries.recovery.assert_replaceable_on(connection,current["generation_id"])
                            await self.diaries._generation_state(current["id"],current["generation_id"],"failed",
                                "diary_material_changed",settle=True,data_generation=data_generation,connection=connection)
                        return {"state":"changed_since_review"}
                return await self.diaries.generate(diary["id"], diary["generation_id"],data_generation=data_generation)
            if diary and diary["current_revision"] and diary["state"] in {"draft", "published"}:
                # A saved body is authoritative. Repair its closed-day projection
                # from that original success rather than creating another draft.
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await self.diaries.reconcile_saved(context, diary, connection)
                    fresh = await (await connection.execute(
                        "SELECT * FROM life.diary_days WHERE actor_id=%s AND scope_key=%s AND day=%s FOR UPDATE",
                        (actor_id, scope_key, row["day"]))).fetchone()
                    if fresh["reviewed_revision"] < fresh["revision"] or fresh["last_error"]:
                        await connection.execute(
                            "UPDATE life.diary_days SET blocked_revision=greatest(blocked_revision,revision),"
                            "last_error='diary_saved_material_unreconciled',updated_at=now() "
                            "WHERE actor_id=%s AND scope_key=%s AND day=%s",
                            (actor_id, scope_key, row["day"]))
                        return {"state": "blocked", "reason": "diary_saved_material_unreconciled"}
                return {"state": "reviewed"}
            if row["generation_id"]:
                reason = (diary.get("reason") if diary and diary["state"] in {"failed", "cancelled"} else None)
                reason = reason or "diary_saved_body_missing"
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                    await self.settle(connection, row["generation_id"], error=reason,data_generation=data_generation)
                # A later event has its own queued review. This job only settles its original.
                return {"state": "blocked", "reason": reason}
            result = await self.diaries.request(context,
                {"client_request_id": "auto-diary:"+signature((actor_id,scope_key,day,row["revision"])),
                 "day": day, "expected_revision": diary["revision"] if diary else 0},
                automatic={"actor_id": actor_id, "scope_key": scope_key, "day": day,
                           "revision": row["revision"], "timezone": row["timezone"],"data_generation":data_generation})
            return result.data
        except AppError as error:
            if error.code == "job_generation_expired":
                raise
            if error.code in {"diary_generation_unresolved", "revision_conflict", "diary_review_no_longer_current"}:
                return {"state": "changed_since_review"}
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                await connection.execute(
                    "UPDATE life.diary_days SET last_error=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND day=%s",
                    (error.code, actor_id, scope_key, row["day"]))
            raise

    async def matching_day(self, connection, context, day, *, scope_ids=(), timezone=None):
        """Resolve an existing author's day without widening its original scene."""
        scope_key = self.life.identity.scene_id(context)
        rows = await (await connection.execute(
            "SELECT * FROM life.diary_days WHERE actor_id=%s AND day=%s AND scope_ids<@%s::text[] "
            "AND scope_ids @> %s::text[] AND context->'audience'<@%s::jsonb "
            "AND (%s::text IS NULL OR timezone=%s) AND scope_key=%s LIMIT 2 FOR UPDATE",
            (context.actor_id,day,list(context.scope_ids),list(scope_ids),document(context.audience),
             timezone,timezone,scope_key))).fetchall()
        if not rows:
            return None
        if len(rows)>1:
            raise AppError("diary_day_scope_ambiguous",status=409)
        row = rows[0]
        original = context_from_record(row["context"])
        if (set(original.scope_ids) != set(row["scope_ids"])
                or self.life.identity.scene_id(original) != row["scope_key"]):
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

    async def settle(self, connection, generation_id, *, error=None, recipe=None, reviewed_at=None, data_generation=None):
        if data_generation is None and isinstance(recipe,dict):
            data_generation = recipe.get("data_generation")
        if not data_generation:
            raise AppError("job_generation_missing",status=409)
        await self.life.jobs.assert_generation_on(connection,data_generation)
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
                await self.enqueue(connection, current, reason="after-original",data_generation=data_generation)

    async def recover(self):
        # Only registered closed days are recovered; an installation/restart does
        # not invent missing days or ask the model to recreate a history.
        async with self.life.database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.diary_days WHERE (reviewed_revision<revision AND blocked_revision<revision) OR generation_id IS NOT NULL "
                "ORDER BY day,actor_id,scope_key FOR UPDATE")).fetchall()
            for row in rows:
                await self.enqueue(connection, row, reason="recover:"+datetime.now(UTC).isoformat())
