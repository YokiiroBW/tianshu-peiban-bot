"""Operations may reconcile one original activity Action, never replay it."""
from tianshu.conversation.codec import context_from_record, signature, source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from .continuation import READ_CAPABILITIES, action_resolved
from .recovery import scheduled_context


ERROR_FAMILIES = ("outcome_unknown", "original_action_unresolved", "capability_running")


def material(row):
    return signature({"activity_id": row["id"], "action_id": row["action_id"],
        "checkpoint_step": row["checkpoint_step"], "source_refs": row["source_refs"],
        "frozen_action": (row["checkpoint_data"] or {}).get("frozen_action")})


class LifeOperationsRecovery:
    def __init__(self, worker, guard_on):
        self.worker, self.life, self.guard_on = worker, worker.life, guard_on

    async def candidates(self, *, limit=32):
        """Offer bounded, source-backed observations from actual waiting activities."""
        limit = max(1, min(int(limit), 250))
        async with self.life.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT id,actor_id,revision,state,waiting_reason,action_id,checkpoint_data,updated_at "
                "FROM life.activities WHERE state IN ('running','dormant') AND context IS NOT NULL "
                "AND action_id IS NOT NULL AND waiting_reason=ANY(%s) "
                "AND NULLIF(checkpoint_data->>'capability_id','') IS NOT NULL "
                "AND checkpoint_data->>'completed_action_id' IS DISTINCT FROM action_id "
                "AND NOT COALESCE(checkpoint_data->>'resolved_action_id'=action_id "
                "AND checkpoint_data->>'capability_id'=ANY(%s) AND checkpoint_data->>'resolved_effect'='none' "
                "AND checkpoint_data->>'resolved_delivery'='not_requested' AND "
                "(checkpoint_data->>'resolved_execution'='failed' OR "
                "(checkpoint_data->>'resolved_execution'='cancelled' AND checkpoint_data->>'resolved_error_code'='capability_timeout')),false) "
                "ORDER BY updated_at,id LIMIT %s", (list(ERROR_FAMILIES), list(READ_CAPABILITIES), limit))).fetchall()
        result = []
        for row in rows:
            capability = (row["checkpoint_data"] or {}).get("capability_id")
            if not isinstance(capability, str) or not capability or action_resolved(row):
                continue
            result.append({"eligible": True, "due_at": row["updated_at"], "dependency": capability,
                "error_family": row["waiting_reason"], "version": str(row["revision"]),
                "material_id": row["action_id"], "role": "bot-background",
                "executing": row["waiting_reason"] == "capability_running",
                "target": {"kind": "life.activity", "id": row["id"], "revision": row["revision"]}})
        return result

    async def _row(self, target):
        identifier = target.get("activity_id") or target.get("id")
        if not identifier:
            return None
        async with self.life.database.transaction(read_only=True) as connection:
            row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s", (identifier,))).fetchone()
        if row and target.get("actor_id") and row["actor_id"] != target["actor_id"]:
            return None
        return row

    async def inspect(self, _incident_or_recovery, target):
        row = await self._row(target)
        if not row or not row["context"] or not row["action_id"]:
            return {"eligible": False, "reason": "original_action_unavailable"}
        if target.get("revision") is not None and str(target["revision"]) != str(row["revision"]):
            return {"eligible": False, "reason": "activity_changed"}
        context = await scheduled_context(self.life, row["context"])
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        paused, cancelled = row["state"] == "paused", row["state"] == "cancelled"
        eligible = row["state"] in {"running", "dormant"} and row["waiting_reason"] in ERROR_FAMILIES
        return {"eligible": eligible, "reason": None if eligible else "activity_not_waiting_for_original",
            "version": str(row["revision"]), "material_id": row["action_id"], "material_digest": material(row),
            "dependency": (row["checkpoint_data"] or {}).get("capability_id") or "capability",
            "original_state": "unknown" if row["waiting_reason"] == "outcome_unknown" else "running",
            "paused": paused, "cancelled": cancelled}

    async def invoke(self, recovery, target, facts, attempt_id):
        if self.guard_on is None:
            return {"state": "unavailable", "reason": "recovery_guard_unavailable"}
        row = await self._row(target)
        if not row or not row["context"]:
            return {"state": "failed", "reason": "activity_not_found"}
        context = await scheduled_context(self.life, row["context"])
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        async with self.life.database.transaction() as connection:
            await self.guard_on(connection, attempt_id, recovery["owner_token"])
            current = await self.life.lock_row(connection, context,
                "SELECT * FROM life.activities WHERE id=%s AND actor_id=%s FOR UPDATE", (row["id"], context.actor_id))
            if (not current or str(current["revision"]) != str(facts["version"]) or current["action_id"] != facts["material_id"]
                    or material(current) != facts["material_digest"] or current["state"] not in {"running", "dormant"}
                    or current["waiting_reason"] not in ERROR_FAMILIES):
                return {"state": "failed", "reason": "activity_changed"}
            await self.life.jobs.enqueue(connection, JobSpec("life.recovery_observe_activity", "life-recovery:" + attempt_id,
                {"activity_id": row["id"], "expected_revision": current["revision"], "context": wire(context),
                 "attempt_id": attempt_id, "owner_token": recovery["owner_token"]}, subject_id=context.actor_id))
        return {"state": "accepted", "activity_id": row["id"], "action_id": current["action_id"]}

    async def observe(self, _recovery, target, facts, _attempt_id):
        row = await self._row(target)
        if not row or row["action_id"] != facts["material_id"] or not row["context"]:
            return {"state": "failed", "reason": "original_action_changed"}
        if row["state"] in {"paused", "cancelled"}:
            return {"state": "failed", "reason": "activity_" + row["state"]}
        context = await scheduled_context(self.life, context_from_record(row["context"]))
        await self.life.identity.check(context, source_refs(row["source_refs"]))
        try:
            current = await self.worker.actions.get(row["action_id"], context)
        except AppError as error:
            if error.status == 404:
                return {"state": "unknown", "reason": "original_action_not_found"}
            raise
        receipt = current.get("receipt") or {}
        execution, effect = receipt.get("execution"), receipt.get("effect")
        if execution in {"pending", "running"}:
            return {"state": "pending", "action_id": row["action_id"]}
        if not execution or execution == "unknown" or effect == "unknown":
            return {"state": "unknown", "action_id": row["action_id"]}
        if execution != "succeeded":
            return {"state": "failed", "reason": receipt.get("error_code") or execution, "action_id": row["action_id"]}
        if row["last_result_revision"] < current["result_revision"]:
            return {"state": "pending", "reason": "life_receipt_not_applied", "action_id": row["action_id"]}
        return {"state": "verified", "action_id": row["action_id"], "receipt_id": receipt.get("receipt_id"),
            "result_revision": current["result_revision"], "activity_revision": row["revision"]}

    def registration(self):
        return {"name": "life.activity.reconcile", "error_families": ERROR_FAMILIES,
            "inspect": self.inspect, "invoke": self.invoke, "observe": self.observe, "max_attempts": 3,
            "observation_only": True}
