"""Operations observes and wakes a single original Life planning generation."""

from datetime import UTC, datetime, timedelta

from tianshu.conversation.codec import context_from_record, signature
from tianshu.conversation.generation import attempt_for
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from . import plan_recovery, rbg_plan_retirement
from .planning import wake_lock, wake_payload
from .recovery import scheduled_context


ERROR_FAMILIES = frozenset({"planner_observation_unavailable","planner_original_deadline_passed",
    "planner_stage_timeout","planner_terminal_pointer","planner_preparation_stalled","planner_unknown",
    "planner_retired_unknown"})


def _material(mark, generation, request, data_generation):
    return signature({"actor_id":mark["actor_id"],"revision":mark["revision"],
        "generation_key":mark["generation_key"],"generation_consumed":mark["generation_consumed"],
        "generation_state":generation["state"],"recovery_state":generation.get("recovery_state"),
        "lineage_id":generation.get("recovery_lineage_id"),"request_state":(request or {}).get("state"),
        "data_generation":data_generation})


def _preparation(mark, data_generation):
    return signature({"actor_id":mark["actor_id"],"revision":mark["revision"],
        "generation_key":mark["generation_key"],"change_watermark":mark["change_watermark"],
        "data_generation":data_generation})


def _retired_material(generation):
    return signature({"actor_id":generation["actor_id"],"generation_key":generation["generation_key"],
        "lineage_id":generation["recovery_lineage_id"],"retired_at":generation["retired_at"],
        "data_generation":str(generation["retirement_data_generation"])})


class RBGPlanOperationsRecovery:
    def __init__(self, worker, guard_on):
        self.worker, self.life, self.guard_on = worker, worker.life, guard_on

    async def _snapshot(self, actor_id):
        async with self.life.database.transaction(read_only=True) as connection:
            data_generation = await self.life.jobs.current_generation_on(connection)
            mark = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s",(actor_id,))).fetchone()
            generation = await plan_recovery.generation_on(connection,mark["generation_key"]) if mark else None
            request = (await rbg_plan_retirement.request_metadata_on(connection,mark["generation_key"])
                       if generation else None)
        return mark,generation,request,data_generation

    async def candidates(self, *, limit=32):
        limit = max(1,min(int(limit),250))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT p.actor_id,p.revision,p.generation_key,p.reason,p.next_due_at "
                "FROM life.plan_watermarks p LEFT JOIN life.plan_generations g ON g.generation_key=p.generation_key "
                "LEFT JOIN conversation.generation_requests r ON r.material_key=p.generation_key "
                "LEFT JOIN life.routines routine ON routine.actor_id=p.actor_id "
                "WHERE (p.generation_key IS NULL AND p.planned_watermark<>p.change_watermark "
                "AND p.change<>'{}'::jsonb "
                "AND p.next_due_at IS NOT NULL AND p.next_due_at<=now() "
                "AND routine.enabled IS DISTINCT FROM false "
                "AND COALESCE(p.reason,'') NOT IN ('routine_disabled','actor_resting','paused_by_user')) OR "
                "(p.generation_key IS NOT NULL AND NOT p.generation_consumed AND "
                "(g.state IN ('superseded','rejected','cancelled','expired') OR g.recovery_state='cancelled' "
                "OR r.state IN ('expired','invalidated') OR p.reason=ANY(%s))) "
                "ORDER BY p.next_due_at NULLS FIRST,p.actor_id LIMIT %s",(list(ERROR_FAMILIES),limit))).fetchall()
            retired = await (await connection.execute(
                "SELECT actor_id,generation_key,base_revision,retirement_observe_after FROM life.plan_generations "
                "WHERE retired_at IS NOT NULL AND retirement_observe_after<=now() "
                "AND retirement_data_generation::text=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "ORDER BY retirement_observe_after,generation_key LIMIT %s",(limit,))).fetchall()
        result = []
        targets = []
        for index in range(max(len(rows),len(retired))):
            if index < len(rows):
                row = rows[index]
                targets.append(({"kind":"life.plan" if row["generation_key"] else "life.plan.prepare",
                    "actor_id":row["actor_id"],"id":row["generation_key"] or row["actor_id"],
                    "revision":row["revision"]},row["reason"],row["next_due_at"]))
            if index < len(retired):
                row = retired[index]
                targets.append(({"kind":"life.plan.retired","actor_id":row["actor_id"],"id":row["generation_key"],
                    "revision":row["base_revision"]},"planner_retired_unknown",row["retirement_observe_after"]))
        for target,reason,due_at in targets[:limit]:
            facts = await self.inspect(None,target)
            if facts.get("reason") in {"actor_disabled","routine_disabled","actor_resting","paused_by_user","planner_not_due"}:
                continue
            result.append({"eligible":facts["eligible"],"reason":facts.get("reason"),"due_at":due_at,
                "dependency":"models:planning","error_family":reason if reason in ERROR_FAMILIES
                    else "planner_preparation_stalled" if target["kind"] == "life.plan.prepare" else "planner_terminal_pointer",
                "version":str(target["revision"]),"material_id":attempt_for(target["id"])
                    if target["kind"] != "life.plan.prepare" else "life-plan-prepare:"+target["actor_id"],
                "role":"bot-background","target":target,
                "business_blocked":target["kind"] != "life.plan.retired",
                "notify_on_change":target["kind"] == "life.plan.retired"})
        return result

    async def _inspect_retired(self, target, *, honor_due=False):
        actor_id,key = target.get("actor_id"),target.get("id")
        if not actor_id or not key:
            return {"eligible":False,"reason":"planner_original_generation_missing"}
        async with self.life.database.transaction(read_only=True) as connection:
            data_generation = await self.life.jobs.current_generation_on(connection)
            old = await plan_recovery.generation_on(connection,key)
            mark = await (await connection.execute(
                "SELECT wake_context FROM life.plan_watermarks WHERE actor_id=%s",(actor_id,))).fetchone()
        if (not old or old["actor_id"] != actor_id or not old.get("retired_at")
                or str(old.get("retirement_data_generation")) != str(data_generation)
                or not old.get("recovery_lineage_id")):
            return {"eligible":False,"reason":"planner_original_generation_missing"}
        if target.get("revision") is not None and str(target["revision"]) != str(old["base_revision"]):
            return {"eligible":False,"reason":"planner_revision_changed"}
        if (honor_due and old["retirement_observe_after"] is None
                and old["retirement_original_status"] in {"succeeded","failed","cancelled"}):
            return {"terminal_status":old["retirement_original_status"]}
        if honor_due and (old["retirement_observe_after"] is None
                or old["retirement_observe_after"] > datetime.now(UTC)):
            return {"deferred":True,"next_due_at":old["retirement_observe_after"]}
        original = context_from_record(old["context"])
        wake = (mark or {}).get("wake_context")
        if wake and context_from_record(wake).principal_id != original.principal_id:
            wake = None
        try:
            context = await scheduled_context(self.life,wake or old["context"])
            await self.life.identity.check(context)
            fence = await self.worker.models.inspect_admission_retirement(attempt_for(key),
                lineage_id=old["recovery_lineage_id"],data_generation=data_generation,context=context,
                original_context=original)
            if fence.get("state") != "sealed":
                raise AppError("model_observation_unavailable",status=409)
        except Exception as error:
            if getattr(error,"database_failure",None) is not None:
                raise
            return {"eligible":False,"reason":getattr(error,"code","model_observation_unavailable")}
        return {"eligible":True,"reason":None,"version":str(old["base_revision"]),
            "material_id":attempt_for(key),"material_digest":_retired_material(old),"dependency":"models:planning",
            "original_state":fence.get("status") or "unknown","stage":"observe_retired",
            "model_observation":fence.get("status") or "missing","data_generation":data_generation}

    async def _record_retired_observation(self, connection, old, status):
        count = old["retirement_observation_count"] + 1 if old["retirement_original_status"] == status else 0
        now = datetime.now(UTC)
        await connection.execute("UPDATE life.plan_generations SET retirement_original_status=%s,"
            "retirement_observed_at=%s,retirement_observation_count=%s,retirement_observe_after=%s "
            "WHERE generation_key=%s",(status,now,count,rbg_plan_retirement.observe_after(status,now,count),
                old["generation_key"]))

    async def observe_due_retirements(self, *, limit=32):
        """Bounded periodic observation of old receipts, without replaying work."""
        limit = max(1,min(int(limit),250))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT actor_id,generation_key,base_revision,retirement_data_generation::text AS data_generation "
                "FROM life.plan_generations WHERE retired_at IS NOT NULL AND retirement_observe_after<=now() "
                "AND retirement_data_generation::text=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "ORDER BY retirement_observe_after,generation_key LIMIT %s",(limit,))).fetchall()
        observed = unavailable = 0
        changes = []
        for row in rows:
            key,actor_id,data_generation = row["generation_key"],row["actor_id"],row["data_generation"]
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                old = await plan_recovery.generation_on(connection,key,lock=True)
                now = datetime.now(UTC)
                if (not old or old["actor_id"] != actor_id or not old.get("retired_at")
                        or str(old.get("retirement_data_generation")) != data_generation
                        or old["retirement_observe_after"] is None or old["retirement_observe_after"] > now):
                    continue
                material = _retired_material(old)
                lease_until = now+timedelta(minutes=10)
                # Persist the claim before the RPC. A crash delays observation,
                # and concurrent Operations workers do not issue another call.
                await connection.execute("UPDATE life.plan_generations SET retirement_observe_after=%s "
                    "WHERE generation_key=%s",(lease_until,key))
            target = {"kind":"life.plan.retired","actor_id":actor_id,"id":key,"revision":row["base_revision"]}
            facts = await self._inspect_retired(target)
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,data_generation)
                current = await plan_recovery.generation_on(connection,key,lock=True)
                if (not current or _retired_material(current) != material
                        or current["retirement_observe_after"] != lease_until):
                    # A real completion notification may have made it due again.
                    continue
                if facts.get("eligible") and facts["material_digest"] == material:
                    status = None if facts["model_observation"] == "missing" else facts["model_observation"]
                    previous = current["retirement_original_status"]
                    await self._record_retired_observation(connection,current,status)
                    observed += 1
                    if status != previous:
                        changes.append({"actor_id":actor_id,"generation_key":key,
                            "attempt_id":facts["material_id"],"previous_status":previous,"status":status})
                else:
                    # Failure to observe is not an observation of the original.
                    count = current["retirement_observation_count"]+1
                    next_due = rbg_plan_retirement.observe_after(current["retirement_original_status"],
                        datetime.now(UTC),count)
                    await connection.execute("UPDATE life.plan_generations SET retirement_observation_count=%s,"
                        "retirement_observe_after=%s WHERE generation_key=%s",(count,next_due,key))
                    unavailable += 1
        return {"observed":observed,"unavailable":unavailable,"changes":changes}

    async def receipt_changed(self, attempt_id, data_generation):
        """A real Models completion may make an existing observation due early."""
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            row = await (await connection.execute(
                "UPDATE life.plan_generations g SET retirement_observe_after=now() "
                "FROM conversation.generation_requests r WHERE r.attempt_id=%s AND r.material_key=g.generation_key "
                "AND g.retired_at IS NOT NULL AND g.retirement_data_generation=%s RETURNING g.generation_key",
                (attempt_id,data_generation))).fetchone()
        return bool(row)

    async def inspect(self, _incident_or_recovery, target):
        if target.get("kind") == "life.plan.retired":
            return await self._inspect_retired(target)
        actor_id = target.get("actor_id")
        if not actor_id:
            return {"eligible":False,"reason":"planner_actor_missing"}
        mark,generation,request,data_generation = await self._snapshot(actor_id)
        if not mark:
            return {"eligible":False,"reason":"planner_actor_missing"}
        if not mark["generation_key"]:
            if (target.get("kind") != "life.plan.prepare" or target.get("id") != actor_id
                    or mark["planned_watermark"] == mark["change_watermark"] or not mark.get("change")):
                return {"eligible":False,"reason":"planner_preparation_not_pending"}
            if mark["next_due_at"] is None or mark["next_due_at"] > datetime.now(UTC):
                return {"eligible":False,"reason":"planner_not_due"}
            if target.get("revision") is not None and str(target["revision"]) != str(mark["revision"]):
                return {"eligible":False,"reason":"planner_revision_changed"}
            if not mark.get("wake_context"):
                return {"eligible":False,"reason":"planner_context_missing"}
            try:
                context = await scheduled_context(self.life,mark["wake_context"])
                await self.life.identity.check(context)
                if context.actor_id != actor_id or not ({"*","life.manage"} & context.permissions):
                    raise AppError("forbidden",status=403)
                actor = await self.life.companion.actor(context)
                if not actor.get("enabled"):
                    return {"eligible":False,"reason":"actor_disabled"}
                async with self.life.database.transaction(read_only=True) as connection:
                    routine = await (await connection.execute(
                        "SELECT * FROM life.routines WHERE actor_id=%s",(actor_id,))).fetchone()
                if routine and not routine["enabled"]:
                    return {"eligible":False,"reason":"routine_disabled"}
                from .day_plan import awake_window
                if awake_window(routine,datetime.now(UTC))[0] is None:
                    return {"eligible":False,"reason":"actor_resting"}
                if mark.get("reason") in {"routine_disabled","actor_resting","paused_by_user"}:
                    return {"eligible":False,"reason":mark["reason"]}
            except AppError as error:
                return {"eligible":False,"reason":error.code}
            return {"eligible":True,"reason":None,"version":str(mark["revision"]),
                "material_id":"life-plan-prepare:"+actor_id,"material_digest":_preparation(mark,data_generation),
                "dependency":"models:planning","original_state":"none","stage":"prepare",
                "data_generation":data_generation}
        if not generation:
            return {"eligible":False,"reason":"planner_original_generation_missing"}
        if target.get("id") and target["id"] != mark["generation_key"]:
            return {"eligible":False,"reason":"planner_generation_changed"}
        if target.get("revision") is not None and str(target["revision"]) != str(mark["revision"]):
            return {"eligible":False,"reason":"planner_revision_changed"}
        if mark["generation_consumed"]:
            return {"eligible":False,"reason":"planner_already_settled"}
        if not generation.get("recovery_lineage_id"):
            return {"eligible":False,"reason":"planner_recovery_lineage_missing"}
        value = mark.get("wake_context") or generation.get("context")
        if not value:
            return {"eligible":False,"reason":"planner_context_missing"}
        try:
            context = await scheduled_context(self.life,value)
            await self.life.identity.check(context)
            if context.actor_id != actor_id or not ({"*","life.manage"} & context.permissions):
                raise AppError("forbidden",status=403)
            fence = await self.worker.models.inspect_admission_retirement(attempt_for(mark["generation_key"]),
                lineage_id=generation["recovery_lineage_id"],data_generation=data_generation,context=context,
                original_context=context_from_record(generation["context"]))
        except Exception as error:
            if getattr(error,"database_failure",None) is not None:
                raise
            return {"eligible":False,"reason":getattr(error,"code","model_observation_unavailable")}
        if fence.get("explicit_cancel") or generation.get("reason") == "explicit_cancel":
            return {"eligible":False,"reason":"explicit_cancel"}
        retirement = rbg_plan_retirement.classification(mark,generation,request,datetime.now(UTC))
        if retirement is None and request is None:
            return {"eligible":False,"reason":"planner_original_request_missing"}
        return {"eligible":True,"reason":None,"version":str(mark["revision"]),
            "material_id":attempt_for(mark["generation_key"]),"material_digest":_material(mark,generation,request,data_generation),
            "dependency":"models:planning","original_state":fence.get("status") or "unknown",
            "stage":"retire_original" if retirement else "observe_original", "retirement_reason":retirement,
            "model_observation":fence.get("status") or "missing", "admission_fence":fence["state"],
            "data_generation":data_generation}

    async def invoke(self, recovery, target, facts, attempt_id):
        if self.guard_on is None:
            return {"state":"unavailable","reason":"recovery_guard_unavailable"}
        if facts.get("stage") == "observe_retired":
            current = await self._inspect_retired(target)
            if (not current.get("eligible") or current["material_id"] != facts["material_id"]
                    or current["material_digest"] != facts["material_digest"]):
                return {"state":"failed","reason":"planner_generation_changed"}
            async with self.life.database.transaction() as connection:
                await self.life.jobs.assert_generation_on(connection,facts["data_generation"])
                await self.guard_on(connection,attempt_id,recovery["owner_token"])
                old = await plan_recovery.generation_on(connection,target["id"],lock=True)
                if not old or _retired_material(old) != facts["material_digest"]:
                    return {"state":"failed","reason":"planner_generation_changed"}
                status = None if current["model_observation"] == "missing" else current["model_observation"]
                await self._record_retired_observation(connection,old,status)
            return {"state":"accepted","attempt_id":facts["material_id"],"stage":"observe_retired"}
        actor_id = target.get("actor_id")
        mark,generation,request,data_generation = await self._snapshot(actor_id)
        if not mark or (not generation and facts.get("stage") != "prepare"):
            return {"state":"failed","reason":"planner_original_generation_missing"}
        value = mark.get("wake_context") or (generation or {}).get("context")
        if not value:
            return {"state":"failed","reason":"planner_context_missing"}
        context = await scheduled_context(self.life,value)
        await self.life.identity.check(context)
        async with self.life.database.transaction() as connection:
            await self.life.jobs.assert_generation_on(connection,data_generation)
            await self.guard_on(connection,attempt_id,recovery["owner_token"])
            current = await (await connection.execute(
                "SELECT * FROM life.plan_watermarks WHERE actor_id=%s FOR UPDATE",(actor_id,))).fetchone()
            old = await plan_recovery.generation_on(connection,current["generation_key"],lock=True) if current else None
            current_request = await rbg_plan_retirement.request_metadata_on(connection,current["generation_key"]) if old else None
            if facts["stage"] == "prepare":
                valid = (current and current["generation_key"] is None
                    and current["planned_watermark"] != current["change_watermark"]
                    and bool(current.get("change")) and current["next_due_at"] is not None
                    and current["next_due_at"] <= datetime.now(UTC)
                    and str(current["revision"]) == str(facts["version"])
                    and _preparation(current,data_generation) == facts["material_digest"])
            else:
                valid = (current and old and not current["generation_consumed"]
                    and str(current["revision"]) == str(facts["version"])
                    and attempt_for(current["generation_key"]) == facts["material_id"]
                    and _material(current,old,current_request,data_generation) == facts["material_digest"])
            if not valid:
                return {"state":"failed","reason":"planner_generation_changed"}
            await self.life.jobs.enqueue(connection,JobSpec("life.plan",
                f"plan-operations:{data_generation}:{actor_id}:{current['revision']}:{attempt_id}",
                wake_payload(actor_id,data_generation),subject_id=actor_id,data_generation=data_generation),
                queueing_lock=wake_lock(actor_id))
        return {"state":"accepted","attempt_id":facts["material_id"],"stage":facts["stage"]}

    async def observe(self, _recovery, target, facts, _attempt_id):
        if facts.get("stage") == "observe_retired":
            current = await self._inspect_retired(target,honor_due=True)
            if current.get("terminal_status"):
                return {"state":"verified","attempt_id":facts["material_id"],
                    "original_status":current["terminal_status"]}
            if current.get("deferred"):
                return {"state":"pending","reason":"historical_observation_not_due",
                    "next_due_at":current["next_due_at"]}
            if not current.get("eligible"):
                return {"state":"unknown","reason":current.get("reason")}
            status = None if current["model_observation"] == "missing" else current["model_observation"]
            if _recovery and self.guard_on is not None:
                async with self.life.database.transaction() as connection:
                    await self.life.jobs.assert_generation_on(connection,facts["data_generation"])
                    await self.guard_on(connection,_attempt_id,_recovery["owner_token"])
                    old = await plan_recovery.generation_on(connection,target["id"],lock=True)
                    if not old or _retired_material(old) != facts["material_digest"]:
                        return {"state":"failed","reason":"planner_generation_changed"}
                    await self._record_retired_observation(connection,old,status)
            if status in {"succeeded","failed","cancelled"}:
                return {"state":"verified","attempt_id":facts["material_id"],"original_status":status}
            return {"state":"pending" if status in {"queued","running"} else "unknown",
                "attempt_id":facts["material_id"],"original_status":status}
        actor_id = target.get("actor_id")
        mark,generation,_request,data_generation = await self._snapshot(actor_id)
        if not mark or data_generation != facts["data_generation"]:
            return {"state":"failed","reason":"planner_generation_changed"}
        if facts.get("stage") == "prepare":
            if generation and generation["state"] == "applied" and mark["generation_consumed"]:
                return {"state":"verified","result":"applied"}
            return {"state":"pending","reason":mark.get("reason")}
        old_key = target.get("id")
        async with self.life.database.transaction(read_only=True) as connection:
            old = await plan_recovery.generation_on(connection,old_key)
        if not old:
            return {"state":"failed","reason":"planner_original_generation_missing"}
        if old.get("retirement_reason") == "explicit_cancel":
            return {"state":"failed","reason":"explicit_cancel"}
        if (old.get("retired_at") and str(old.get("retirement_data_generation")) == str(data_generation)
                and (mark["generation_key"] != old_key or mark["generation_consumed"])):
            return {"state":"verified","attempt_id":facts["material_id"],"retirement_reason":old["retirement_reason"],
                "model_state":old["retirement_model_state"]}
        if old["state"] == "applied" and mark["generation_consumed"]:
            return {"state":"verified","attempt_id":facts["material_id"],"result":"applied"}
        return {"state":"pending","attempt_id":facts["material_id"],"reason":mark.get("reason")}

    def registration(self):
        return {"name":"life.plan.reconcile","error_families":ERROR_FAMILIES,"inspect":self.inspect,
            "invoke":self.invoke,"observe":self.observe,"max_attempts":3,"observation_only":True,
            "domain_managed":True}
