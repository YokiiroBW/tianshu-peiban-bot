"""Author controls observe or cancel the original chapter/diary attempt."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, signature
from tianshu.models.timing import waiting_budget
from .validation import revision


class GenerationRecovery:
    """Bind authored requests to the runtime's single physical-attempt budget."""

    def __init__(self, policy):
        self.policy = policy

    async def inherit_on(self, connection, generation_id, previous):
        row = await (await connection.execute(
            "UPDATE life.generation_recipes AS child SET recovery_lineage_id=old.recovery_lineage_id,"
            "recovery_owner=old.recovery_owner,recovery_material_id=old.recovery_material_id,"
            "recovery_material_digest=old.recovery_material_digest,"
            "recovery_dependency=old.recovery_dependency,recovery_version=old.recovery_version "
            "FROM life.generation_recipes old WHERE child.generation_id=%s AND old.generation_id=%s "
            "AND old.recovery_lineage_id IS NOT NULL AND child.recovery_lineage_id IS NULL "
            "RETURNING child.generation_id", (generation_id, previous))).fetchone()
        if not row:
            raise AppError("recovery_original_lineage_missing", status=409)

    async def reserve_on(self, connection, kind, item_id, request, *, quota_group):
        row = await (await connection.execute(
            "SELECT * FROM life.generation_recipes WHERE generation_id=%s FOR UPDATE", (request.attempt_id,))).fetchone()
        if not row or row["kind"] != kind or row["item_id"] != item_id:
            raise AppError("generation_recipe_missing", status=409)
        data_generation = frozen_data_generation(row["recipe"])
        frozen = await (await connection.execute(
            "SELECT request FROM conversation.generation_requests WHERE attempt_id=%s", (request.attempt_id,))).fetchone()
        if not frozen or frozen["request"] is None:
            raise AppError("generation_request_not_found", status=409)
        digest = signature(frozen["request"])
        route = request.route_id or "configured:" + request.function
        dependency = "models:quota:" + quota_group
        identity, owner, version = row["recovery_lineage_id"], row["recovery_owner"], row["recovery_version"]
        table = "life.diaries" if kind == "diary" else "life.chapters"
        item = await (await connection.execute(f"SELECT current_revision FROM {table} WHERE id=%s FOR UPDATE", (item_id,))).fetchone()
        if not item:
            raise AppError("generation_no_longer_current", status=409)
        revision_table = "life.diary_revisions" if kind == "diary" else "life.chapter_revisions"
        item_column = "diary_id" if kind == "diary" else "chapter_id"
        saved = await (await connection.execute(
            f"SELECT coalesce(max(revision),0) AS revision FROM {revision_table} "
            f"WHERE {item_column}=%s AND model_attempt_id IS NOT NULL", (item_id,))).fetchone()
        # Author edits and other administrative revisions cannot reset a failed
        # generation budget. Only a previously saved model result starts the
        # next authored generation cycle for this same object.
        material_id = f"{kind}:{item_id}:generated:{saved['revision']}"
        material_version = signature({"recipe": row["recipe"], "source_refs": row["source_refs"]})
        previous = await (await connection.execute(
            "SELECT recovery_lineage_id,recovery_owner,recovery_material_digest,recovery_dependency,recovery_version "
            "FROM life.generation_recipes WHERE recovery_material_id=%s AND generation_id<>%s "
            "AND recovery_lineage_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (material_id,request.attempt_id))).fetchone()
        material_digest = row["recovery_material_digest"] or (
            previous["recovery_material_digest"] if previous else material_version)
        prior = row if identity else previous
        if prior and prior["recovery_dependency"] != dependency:
            if row["recovery_request_digest"]:
                raise AppError("recovery_dependency_changed", status=409)
            await self.policy.update_dependency_on(connection,prior["recovery_lineage_id"],
                owner=prior["recovery_owner"],expected_version=prior["recovery_version"],
                expected_dependency=prior["recovery_dependency"],new_dependency=dependency)
            if identity:
                await connection.execute("UPDATE life.generation_recipes SET recovery_dependency=%s WHERE generation_id=%s",
                    (dependency,request.attempt_id))
        if identity is None:
            if row["previous_generation_id"]:
                raise AppError("recovery_original_lineage_missing", status=409)
            owner = f"life.{material_id}"
            version = material_version
            lineage = await self.policy.register_on(connection, domain="life",
                subject_id=request.context.actor_id, owner=owner,
                material_id=material_id, material_digest=material_digest, dependency=dependency,
                current_version=version, max_attempts=3,data_generation=data_generation)
            identity = lineage["lineage_id"]
            if previous and previous["recovery_lineage_id"] != identity:
                raise AppError("recovery_lineage_conflict", status=409)
            if lineage["attempts"] >= lineage["max_attempts"]:
                raise AppError("recovery_budget_exhausted", status=409)
            if lineage["current_version"] != version:
                if lineage["attempts"] and lineage["due_at"] is None:
                    raise AppError("recovery_original_not_failed", status=409)
                await self.policy.update_version_on(connection,identity,owner=owner,
                    expected_version=lineage["current_version"],current_version=version)
            await connection.execute("UPDATE life.generation_recipes SET recovery_lineage_id=%s,"
                "recovery_owner=%s,recovery_material_id=%s,recovery_material_digest=%s,recovery_dependency=%s,"
                "recovery_version=%s WHERE generation_id=%s",
                (identity, owner, material_id, material_digest, dependency, version, request.attempt_id))
        else:
            lineage = await self.policy.register_on(connection, domain="life", subject_id=request.context.actor_id,
                owner=owner, material_id=material_id, material_digest=material_digest, dependency=dependency,
                current_version=version, max_attempts=3,data_generation=data_generation)
            if lineage["lineage_id"] != identity or row["recovery_material_id"] != material_id:
                raise AppError("recovery_lineage_conflict", status=409)
        claim = None
        if lineage["attempts"] and not row["recovery_request_digest"]:
            if lineage["attempts"] >= lineage["max_attempts"]:
                raise AppError("recovery_budget_exhausted", status=409)
            if lineage["due_at"] is None:
                raise AppError("recovery_original_not_failed", status=409)
            claimed = await self.policy.claim_lineage_due_on(connection,identity,owner=owner)
            if not claimed:
                raise AppError("recovery_not_due", status=409)
            claim = claimed["claim_token"]
        reservation = await self.policy.reserve_attempt_on(connection, identity, owner=owner,
            expected_version=version, attempt_id=request.attempt_id, request_digest=digest, route=route,
            original_state="failed" if lineage["attempts"] else "none", claim_token=claim)
        if reservation.status != "reserved":
            raise AppError("recovery_attempt_not_dispatchable", status=409)
        await connection.execute("UPDATE life.generation_recipes SET recovery_request_digest=%s,"
            "recovery_route=%s,recovery_probe_token=coalesce(recovery_probe_token,%s) WHERE generation_id=%s",
            (digest, route, reservation.probe_token, request.attempt_id))

    async def observe_on(self, connection, attempt_id, status, code=None, *, retry_after=None):
        """Record Models' physical receipt, never infer business completion."""
        from tianshu.runtime.recovery_policy import FailureCategory, classify_failure
        from tianshu.models.retry import transient_model_failure

        row = await (await connection.execute(
            "SELECT * FROM life.generation_recipes WHERE generation_id=%s FOR UPDATE", (attempt_id,))).fetchone()
        if not row or not row["recovery_request_digest"]:
            return "unregistered", None
        reservation = await self.policy.reserve_attempt_on(connection, row["recovery_lineage_id"],
            owner=row["recovery_owner"], expected_version=row["recovery_version"], attempt_id=attempt_id,
            request_digest=row["recovery_request_digest"], route=row["recovery_route"], original_state="none")
        state, probe = reservation.status, row["recovery_probe_token"]
        if status in {"queued", "running"}:
            return state, None
        if status in {"unknown", "absent"}:
            if state == "reserved":
                await self.policy.fail_attempt_on(connection, attempt_id, code=code or "model_outcome_unknown",
                    category=classify_failure(code, external_outcome_unknown=True), probe_token=probe)
                return "unknown", None
            return state, None
        if status == "succeeded":
            if state == "failed":
                raise AppError("recovery_attempt_outcome_conflict", status=409)
            if state == "unknown":
                await self.policy.resolve_unknown_on(connection, attempt_id,
                    observation_ref=f"models:{attempt_id}:succeeded", state="succeeded", probe_token=probe)
            elif state == "reserved":
                await self.policy.succeed_attempt_on(connection, attempt_id, probe_token=probe)
            return "succeeded", None
        if status in {"failed", "cancelled"}:
            if state == "succeeded":
                raise AppError("recovery_attempt_outcome_conflict", status=409)
            if status == "cancelled":
                code = code if code and "cancel" in code else "cancelled"
                category = FailureCategory.STATE_CHANGED
            else:
                category = classify_failure(code)
            if category == FailureCategory.OTHER and transient_model_failure(code):
                category = FailureCategory.TRANSPORT
            if state == "unknown":
                due = await self.policy.resolve_unknown_on(connection, attempt_id,
                    observation_ref=f"models:{attempt_id}:{status}", state="failed", code=code or status,
                    category=category, retry_after=retry_after, probe_token=probe)
            elif state == "reserved":
                due = await self.policy.fail_attempt_on(connection, attempt_id, code=code or status,
                    category=category, retry_after=retry_after, probe_token=probe)
            else:
                return state, None
            return "failed", due
        return state, None

    async def cancel_on(self, connection, attempt_id):
        row = await (await connection.execute(
            "SELECT recovery_lineage_id,recovery_owner,recovery_version FROM life.generation_recipes "
            "WHERE generation_id=%s FOR UPDATE", (attempt_id,))).fetchone()
        if row and row["recovery_lineage_id"]:
            await self.policy.update_version_on(connection, row["recovery_lineage_id"],
                owner=row["recovery_owner"], expected_version=row["recovery_version"],
                current_version=row["recovery_version"], cancelled=True)


def frozen_data_generation(recipe, supplied=None):
    """Use the installation generation captured by the original domain request."""
    recorded = recipe.get("data_generation") if isinstance(recipe, dict) else None
    if not isinstance(recorded, str) or not recorded:
        raise AppError("job_generation_missing", status=409)
    if supplied is not None and supplied != recorded:
        raise AppError("job_generation_expired", status=409)
    return recorded


def saved_statistic(kind, item_id, content_revision, actor_id, created_at, route_id):
    return {"family":"life", "event_id":f"life.{kind}:{item_id}", "revision":content_revision,
        "occurred_at":created_at, "module":"life", "actor_id":actor_id, "route_id":route_id,
        "dimensions":{"state":"succeeded"}, "metrics":{"business_success":1}}


async def enqueue_statistics_backfill_on(life, connection, *, delay=30, data_generation=None):
    """The durable pending pointer survives even when the queue is unavailable."""
    try:
        async with connection.transaction():
            if data_generation is None:
                data_generation = await life.jobs.current_generation_on(connection)
            await life.jobs.enqueue(connection,JobSpec("life.experience_statistics_backfill",
                "life-experience-statistics:"+str(uuid4()),{"data_generation":data_generation},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=delay),data_generation=data_generation),
                queueing_lock="life-experience-statistics")
    except Exception:
        pass


async def record_saved_on(life, connection, statistics, *, kind, item_id, content_revision, actor_id, created_at, route_id=None, data_generation=None):
    """Keep an exact replay pointer even if the optional projection is down."""
    await connection.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('life.experience.statistics',0))")
    await connection.execute("INSERT INTO life.experience_statistics_pending(kind,item_id,content_revision) "
        "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (kind,item_id,content_revision))
    event = saved_statistic(kind,item_id,content_revision,actor_id,created_at,route_id)
    projected = False
    if statistics is not None:
        try:
            async with connection.transaction():
                projected = await statistics.record_safely_on(connection,event)
        except Exception:
            projected = False
    if projected:
        await connection.execute("DELETE FROM life.experience_statistics_pending "
            "WHERE kind=%s AND item_id=%s AND content_revision=%s", (kind,item_id,content_revision))
    else:
        if statistics is not None:
            try:
                async with connection.transaction():
                    await statistics.mark_producer_on(connection,"life.experience",complete=False,
                        detail="authored revisions pending")
            except Exception:
                pass
        await enqueue_statistics_backfill_on(life,connection,data_generation=data_generation)


async def backfill_saved_statistics(life, statistics, kind, *, limit=128, data_generation):
    """Replay bounded pending pointers from immutable authored revisions."""
    if statistics is None:
        return {"complete":False,"reason":"statistics_unavailable","processed":0}
    if type(limit) is not int or not 1 <= limit <= 512:
        raise ValueError("invalid_statistics_backfill_limit")
    if kind == "diary":
        joins = ("LEFT JOIN life.diary_revisions r ON r.diary_id=p.item_id AND r.revision=p.content_revision "
                 "LEFT JOIN life.diaries d ON d.id=p.item_id")
        actor = "d.actor_id"
    elif kind == "chapter":
        joins = ("LEFT JOIN life.chapter_revisions r ON r.chapter_id=p.item_id AND r.revision=p.content_revision "
                 "LEFT JOIN life.chapters c ON c.id=p.item_id LEFT JOIN life.works w ON w.id=c.work_id")
        actor = "w.actor_id"
    else:
        raise ValueError("invalid_experience_kind")
    async with life.database.transaction() as connection:
        await life.jobs.assert_generation_on(connection,data_generation)
        rows = await (await connection.execute(
            f"SELECT p.*,r.created_at,r.recipe,{actor} AS actor_id FROM life.experience_statistics_pending p "
            f"{joins} WHERE p.kind=%s ORDER BY p.item_id,p.content_revision LIMIT %s FOR UPDATE OF p SKIP LOCKED",
            (kind,limit))).fetchall()
        processed = 0
        for row in rows:
            if row["created_at"] is None or row["actor_id"] is None:
                continue
            receipt = (row["recipe"] or {}).get("model_receipt") or {}
            event = saved_statistic(kind,row["item_id"],row["content_revision"],row["actor_id"],
                row["created_at"],receipt.get("route_id"))
            if await statistics.record_safely_on(connection,event):
                await connection.execute("DELETE FROM life.experience_statistics_pending "
                    "WHERE kind=%s AND item_id=%s AND content_revision=%s",
                    (kind,row["item_id"],row["content_revision"]))
                processed += 1
        remaining = (await (await connection.execute(
            "SELECT count(*) AS count FROM life.experience_statistics_pending WHERE kind=%s", (kind,))).fetchone())["count"]
    return {"complete":remaining==0,"processed":processed,"pending":remaining}


async def submit_frozen(life, models, generations, material_key, request, *, guard, completion, kind, item_id, recovery):
    """Admit the saved intent and release the domain worker after acceptance."""
    if recovery is None:
        raise AppError("recovery_policy_unavailable", status=503)
    if not request.data_generation or request.data_generation != completion.data_generation:
        raise AppError("job_generation_expired", status=409)
    profile = await models.generation_profile(replace(request,budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=30))))
    quota_group = profile.get("quota_group")
    if not isinstance(quota_group,str) or not quota_group:
        raise AppError("model_quota_group_unavailable", status=503)
    if request.route_id is not None and (profile.get("route_id") != request.route_id
            or profile.get("route_revision") != request.route_revision):
        raise AppError("model_route_changed", status=409)
    async with life.database.transaction() as connection:
        await life.jobs.assert_generation_on(connection,completion.data_generation)
        request = await generations.prepare(material_key, request, connection=connection, guard=guard)
        admission = waiting_budget(request)
        if admission is not None and admission.deadline_at <= datetime.now(UTC):
            raise AppError("generation_request_expired", status=409)
        await recovery.reserve_on(connection, kind, item_id, request,quota_group=quota_group)
        await connection.execute(
            "UPDATE conversation.generation_requests SET state='claimed',claimed_at=coalesce(claimed_at,now()) "
            "WHERE material_key=%s AND state='prepared'", (material_key,))
    receipt = await models.submit(request, completion=completion)
    if receipt is None or receipt.attempt_id != request.attempt_id:
        raise AppError("generation_receipt_identity_mismatch", status=502)
    table = "life.diaries" if kind == "diary" else "life.chapters"
    async with life.database.transaction(read_only=True) as connection:
        row = await (await connection.execute(
            f"SELECT generation_id,generation_cancel_requested FROM {table} WHERE id=%s", (item_id,))).fetchone()
    # Cancellation can race the gap between the domain guard and admission.
    if row and row["generation_id"] == request.attempt_id and row["generation_cancel_requested"]:
        await models.cancel(request.attempt_id, reason="explicit_cancel")
    return receipt


class GenerationControl:
    def __init__(self, life, writing, diaries):
        self.life, self.writing, self.diaries = life, writing, diaries

    async def backfill_statistics(self, data_generation):
        diary = await self.diaries.backfill_statistics(data_generation=data_generation)
        chapter = await self.writing.backfill_statistics(data_generation=data_generation)
        statistics = self.diaries.statistics
        complete = False
        if statistics is not None and statistics is self.writing.statistics:
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('life.experience.statistics',0))")
                pending = (await (await connection.execute(
                    "SELECT count(*) AS count FROM life.experience_statistics_pending")).fetchone())["count"]
                complete = pending == 0 and diary["complete"] and chapter["complete"]
                try:
                    async with connection.transaction():
                        await statistics.mark_producer_on(connection,"life.experience",
                            watermark=datetime.now(UTC).isoformat(),complete=complete,
                            detail=None if complete else f"authored revisions pending: {pending}")
                except Exception:
                    complete = False
        if not complete:
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                await enqueue_statistics_backfill_on(self.life,connection,delay=60,data_generation=data_generation)
        return {"diary":diary,"chapter":chapter,"complete":complete}

    @staticmethod
    def query(kind, *, lock=False):
        if kind == "chapter":
            sql = "SELECT c.* FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE c.id=%s AND w.actor_id=%s AND w.scope_ids<@%s::text[]"
            return sql + (" FOR UPDATE OF c" if lock else "")
        if kind == "diary":
            return "SELECT * FROM life.diaries WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]" + (" FOR UPDATE" if lock else "")
        raise ValueError(kind)

    async def request(self, context, body, *, kind, operation):
        await self.life.identity.check(context)
        async def apply(connection):
            # Cancellation handles execution metadata even after source loss.
            await self.life.validate_persistence(connection, context)
            row = (await self.diaries._diary(context,body.get("id"),connection,lock=True)
                if kind=="diary" and operation=="reconcile" else await (await connection.execute(self.query(kind, lock=True),
                    (body.get("id"), context.actor_id, list(context.scope_ids)))).fetchone())
            if not row:
                raise AppError(kind+"_not_found", status=404)
            revision(row, body.get("expected_revision"))
            if not row["generation_id"]:
                raise AppError("generation_not_started", status=409)
            if operation=="reconcile" and row["state"]=="collecting":
                return CommandResult(body["client_request_id"],{"id":row["id"],"state":"collecting"},row["revision"],attempt_id=row["generation_id"])
            if kind=="diary" and operation=="reconcile":
                await self.diaries.reconcile_saved(context,row,connection)
            current = row["revision"]+1
            if row["state"] in {"collecting", "generating", "unknown", "cancelling"}:
                data_generation = frozen_data_generation(row["generation_recipe"])
                await self.life.jobs.assert_generation_on(connection,data_generation)
                table = "life.chapters" if kind == "chapter" else "life.diaries"
                service = self.writing if kind == "chapter" else self.diaries
                if operation == "cancel" and service.recovery is not None:
                    await service.recovery.cancel_on(connection, row["generation_id"])
                await connection.execute(f"UPDATE {table} SET state=%s,reason=%s,revision=%s,generation_cancel_requested=generation_cancel_requested OR %s WHERE id=%s",
                    ("cancelling" if operation=="cancel" else row["state"], "cancel_requested" if operation=="cancel" else row["reason"], current, operation=="cancel", row["id"]))
                await self.life.jobs.enqueue(connection, JobSpec("life.generation_control",
                    f"generation-{operation}:{kind}:{row['generation_id']}:{current}",
                    {"kind":kind,"item_id":row["id"],"generation_id":row["generation_id"],"context":wire(context),"operation":operation,"data_generation":data_generation},
                    ServiceClass.INTERACTIVE, subject_id=context.actor_id,data_generation=data_generation))
            else:
                current = row["revision"]
            return CommandResult(body["client_request_id"], {"id":row["id"],"state":"cancelling" if operation=="cancel" and current!=row["revision"] else row["state"]},
                current, attempt_id=row["generation_id"])
        return await self.life.commands.execute(context, f"life.{'chapters' if kind=='chapter' else 'diaries'}.{operation}", body["client_request_id"], signature(body), apply)

    async def work(self, kind, item_id, generation_id, context, operation, data_generation):
        context = context_from_record(context)
        if kind == "diary":
            return await self.diaries.control_generation(item_id,generation_id,context,operation,data_generation=data_generation)
        await self.life.identity.check(context)
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(self.query(kind), (item_id,context.actor_id,list(context.scope_ids)))).fetchone()
        if not row or row["generation_id"] != generation_id or row["state"] not in {"generating","unknown","cancelling","collecting"}:
            return
        frozen_data_generation(row["generation_recipe"],data_generation)
        if kind=="chapter":
            original=context_from_record(row["generation_context"])
            if original.principal_id!=context.principal_id:
                raise AppError("generation_observation_context_mismatch",status=403)
            if operation=="cancel":
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,data_generation)
                try:
                    await self.writing.models.cancel(generation_id)
                except AppError as error:
                    if error.status!=404:
                        raise
            return await self.writing.generate(item_id,generation_id,data_generation=data_generation)

    async def recover(self):
        async with self.life.database.transaction() as connection:
            active_generation = await self.life.jobs.current_generation_on(connection)
            await self.life.jobs.assert_generation_on(connection,active_generation)
            for kind,table,context_column in (("chapter","life.chapters","generation_context"),("diary","life.diaries","context")):
                rows = await (await connection.execute(f"SELECT * FROM {table} WHERE state IN ('generating','unknown','cancelling','collecting')")).fetchall()
                for row in rows:
                    data_generation = frozen_data_generation(row["generation_recipe"])
                    if data_generation != active_generation:
                        continue
                    if row["state"]=="collecting":
                        task,payload="life.diary_collect",{"diary_id":row["id"],"generation_id":row["generation_id"],"cursor":row["generation_recipe"]["cursor"],"data_generation":data_generation}
                    elif row["state"]=="generating":
                        task="life.chapter_generate" if kind=="chapter" else "life.diary_generate"
                        payload={"chapter_id" if kind=="chapter" else "diary_id":row["id"],"generation_id":row["generation_id"],"data_generation":data_generation}
                    else:
                        task,payload="life.generation_control",{"kind":kind,"item_id":row["id"],"generation_id":row["generation_id"],"context":row[context_column],"operation":"cancel" if row["state"]=="cancelling" else "reconcile","data_generation":data_generation}
                    await self.life.jobs.enqueue(connection,JobSpec(task,f"generation-recover:{kind}:{row['generation_id']}:{datetime.now(UTC).isoformat()}",payload,ServiceClass.INTERACTIVE,subject_id=row.get("actor_id"),data_generation=data_generation))
            await enqueue_statistics_backfill_on(self.life,connection,delay=0,data_generation=active_generation)
