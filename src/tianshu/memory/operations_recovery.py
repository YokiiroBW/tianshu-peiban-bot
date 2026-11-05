"""Operations can wake and observe one original consolidation, never replay unknown work."""

from datetime import UTC, datetime

from aiohttp import ClientError

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from . import codec
from .authority import context_record
from .consolidation import context_from_bound
from .consolidation_retry import explicit_retry_available, retryable_code, rpc_recovery_available
from .model_outcome import observe_original


class ConsolidationOperations:
    """The domain remains the owner of the frozen input and physical budget."""

    def __init__(self, memory):
        self.memory = memory
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.guard_on = None

    @staticmethod
    def _target(target):
        if (not isinstance(target,dict) or target.get("kind") != "memory_batch"
                or not isinstance(target.get("id"),str) or not target["id"]):
            raise AppError("invalid_memory_recovery_target")
        return target["id"]

    async def _batch(self, target):
        batch_id = self._target(target)
        async with self.database.transaction(read_only=True) as conn:
            return await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                (batch_id,))).fetchone()

    @staticmethod
    def _snapshot(batch):
        return body_hash({key:batch.get(key) for key in ("batch_id","partition_id","status","error_code",
            "step_ids","selected_step_id","proposal_digest","dependency_refs","read_set","manifest","retry_due_at")})

    async def _facts_on(self, conn, snapshot, context, request, outcome, *, guard=None):
        await self.memory.consolidation._assert_batch_generation_on(conn,snapshot)
        if not await self.sources.validate_persistence(conn,context,codec.refs(snapshot["dependency_refs"])):
            return {"eligible":False,"reason":"source_unavailable"}
        await self.identity.check_context(context,connection=conn)
        state = await self.memory.consolidation._lock_owned_partition(conn,snapshot["partition_id"],None)
        batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
            (snapshot["batch_id"],))).fetchone()
        if not state or not batch or self._snapshot(batch) != self._snapshot(snapshot):
            return {"eligible":False,"reason":"memory_batch_changed"}
        if batch["manifest"].get("remote_wait_unresolved") or batch["manifest"].get("original_observation_only"):
            return {"eligible":False,"reason":"memory_original_observation_only"}
        now = datetime.now(UTC)
        if (state["active_batch_id"] is not None or state.get("lease_until") and state["lease_until"] > now
                or not batch["step_ids"] or batch["step_ids"][-1] != request.intent_id):
            return {"eligible":False,"reason":"memory_batch_running_or_changed"}
        if batch["context_fingerprint"] != body_hash(context_record(context)):
            return {"eligible":False,"reason":"memory_authority_changed"}
        if guard:
            # Match the domain's source -> partition -> batch -> lineage lock
            # order; the guard precedes every domain mutation in this txn.
            await self.guard_on(conn,guard[0],guard[1])
        recovery = batch["manifest"].get("recovery")
        if self.memory.recovery_policy is None or not recovery:
            return {"eligible":False,"reason":"memory_recovery_lineage_unavailable"}
        lineage = await (await conn.execute("SELECT l.* FROM runtime.recovery_lineages l "
            "WHERE l.lineage_id=%s FOR UPDATE OF l",(recovery["lineage_id"],))).fetchone()
        if (not lineage or lineage["owner"] != "memory" or lineage["domain"] != "memory"
                or lineage["current_version"] != recovery["version"]
                or lineage["data_generation"] != batch["manifest"]["data_generation"]
                or lineage["paused"] or lineage["cancelled"] or lineage.get("source_withdrawn")
                or lineage["claim_token"] and lineage["claim_expires_at"] and lineage["claim_expires_at"] > now):
            return {"eligible":False,"reason":"memory_recovery_not_available"}
        root_id = batch["manifest"].get("reorganization",{}).get("root_batch_id") or batch["batch_id"]
        root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
            (root_id,))).fetchone()
        if not root or body_hash(root["manifest"].get("frozen")) != lineage["material_digest"]:
            return {"eligible":False,"reason":"memory_recovery_material_changed"}
        local = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s "
            "ORDER BY number DESC LIMIT 1",(batch["batch_id"],))).fetchone()
        previous = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number DESC LIMIT 1",(lineage["lineage_id"],))).fetchone()
        receipt = outcome.receipt
        if (not local or not previous or not receipt or request.data_generation != lineage["data_generation"]
                or local["step_id"] != request.intent_id or local["model_attempt_id"] != request.attempt_id
                or previous["attempt_id"] != request.attempt_id or previous["status"] != receipt.status
                or local.get("recovery_policy_state") != receipt.status
                or getattr(receipt,"cancellation_reason",None) == "explicit_cancel"):
            return {"eligible":False,"reason":"memory_original_attempt_unresolved"}
        mode = None
        if receipt.status == "succeeded" and outcome.result is not None and batch["status"] == "failed":
            if rpc_recovery_available(batch) and not local["rejected_error"]:
                mode = "observe_original"
            elif (explicit_retry_available(batch)
                    and batch["error_code"] not in {"memory_consolidation_duplicate_create",
                                                    "memory_consolidation_unknown_endpoint"}
                    and batch["manifest"].get("budget",self.memory.consolidation._budget_record())
                        == self.memory.consolidation._budget_record()):
                mode = "resume_semantic"
        elif (receipt.status == "failed" and batch["status"] == "quarantined"
                and retryable_code(receipt.error_code,receipt.status)
                and batch.get("retry_due_at") is not None and batch["retry_due_at"] <= now
                and lineage.get("due_at") is not None and lineage["due_at"] <= now):
            mode = "wake_due"
        if mode is None:
            return {"eligible":False,"reason":"memory_original_not_recoverable"}
        if mode != "observe_original":
            budget = await self.memory.recovery_policy.computation_budget_on(conn,lineage["lineage_id"],
                owner=recovery["owner"],expected_version=recovery["version"])
            if not budget["can_retry"]:
                return {"eligible":False,"reason":budget["reason"]}
            waiting = await self.memory.recovery_policy.inspect_wait_on(conn,lineage["lineage_id"],
                owner=recovery["owner"],expected_version=recovery["version"])
            if waiting is not None:
                return {"eligible":False,"reason":"memory_recovery_dependency_wait"}
            if root_id != batch["batch_id"]:
                remainder = await (await conn.execute("SELECT retry_remaining FROM memory.consolidation_reorganizations "
                    "WHERE root_batch_id=%s",(root_id,))).fetchone()
                if not remainder or remainder["retry_remaining"] < 1:
                    return {"eligible":False,"reason":"memory_reorganization_budget_exhausted"}
        for episode in sorted(batch["manifest"]["episodes"],key=lambda item:item["episode_id"]):
            row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                (episode["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"] != episode["revision"]
                    or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]):
                return {"eligible":False,"reason":"memory_episode_changed"}
        frozen = batch["manifest"]["steps"].get(request.intent_id)
        if not frozen:
            return {"eligible":False,"reason":"memory_frozen_input_unavailable"}
        await self.memory.claims.revisions.validate_read_set_on(conn,context,frozen)
        return {"eligible":True,"version":recovery["version"],"material_id":root_id,
            "material_digest":lineage["material_digest"],"dependency":lineage["dependency"],
            "original_state":receipt.status,"paused":False,"cancelled":False,
            "policy_domain":"memory","owner":"memory","subject_id":lineage["subject_id"],
            "lineage_id":lineage["lineage_id"],"max_attempts":lineage["max_attempts"],
            "batch_id":batch["batch_id"],"snapshot":self._snapshot(batch),"mode":mode,
            "original_attempt_id":request.attempt_id,"due_at":batch.get("retry_due_at") or batch["updated_at"]}

    async def inspect(self, _incident_or_recovery, target):
        batch = await self._batch(target)
        if not batch or batch["status"] not in {"failed","quarantined"} or not batch["step_ids"]:
            return {"eligible":False,"reason":"memory_batch_not_recoverable"}
        bound = batch["manifest"].get("authority_bound")
        if not bound or not self.memory.consolidation.steps:
            return {"eligible":False,"reason":"memory_original_unavailable"}
        try:
            context = context_from_bound(bound)
            await self.identity.check_context(context)
            _, request = await self.memory.consolidation.steps.recorded(batch["step_ids"][-1])
            outcome = await observe_original(self.memory.consolidation.steps.models,request)
            if not outcome.receipt or outcome.status in {"queued","running","unknown"}:
                return {"eligible":False,"reason":"memory_original_attempt_unresolved","original_state":"unknown"}
            async with self.database.transaction() as conn:
                return await self._facts_on(conn,batch,context,request,outcome)
        except AppError as error:
            if error.status in {401,403,404,409}:
                return {"eligible":False,"reason":error.code}
            raise

    async def candidates(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 128:
            raise AppError("invalid_memory_recovery_candidate_limit")
        async with self.database.transaction(read_only=True) as conn:
            rows = await (await conn.execute("SELECT batch_id FROM memory.consolidation_batches "
                "WHERE status IN ('failed','quarantined') AND manifest->>'data_generation'="
                "(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "AND (status='failed' OR retry_due_at<=now()) "
                "ORDER BY COALESCE(retry_due_at,updated_at),batch_id LIMIT %s",(min(limit*4,512),))).fetchall()
        values = []
        for row in rows:
            target = {"kind":"memory_batch","id":row["batch_id"]}
            try:
                facts = await self.inspect(None,target)
            except (AppError,TimeoutError,OSError,ClientError):
                continue
            if facts.get("eligible"):
                values.append({"role":"bot-background","material_id":facts["material_id"],"target":target,
                    "dependency":facts["dependency"],"due_at":facts["due_at"],"eligible":True,
                    "version":facts["version"],"error_family":"memory_consolidation_failed"})
                if len(values) >= limit:
                    break
        return tuple(values)

    async def invoke(self, recovery, target, facts, attempt_id):
        if self.guard_on is None:
            raise AppError("memory_recovery_guard_unavailable",status=503)
        batch = await self._batch(target)
        if not batch or not batch["step_ids"]:
            return {"state":"failed","reason":"memory_batch_unavailable"}
        context = context_from_bound(batch["manifest"]["authority_bound"])
        _, request = await self.memory.consolidation.steps.recorded(batch["step_ids"][-1])
        outcome = await observe_original(self.memory.consolidation.steps.models,request)
        if not outcome.receipt or outcome.status in {"queued","running","unknown"}:
            return {"state":"unknown","reason":"memory_original_attempt_unresolved"}
        async with self.database.transaction() as conn:
            fresh = await self._facts_on(conn,batch,context,request,outcome,
                guard=(attempt_id,recovery["owner_token"]))
            if (not fresh.get("eligible") or any(fresh.get(key) != facts.get(key) for key in
                    ("version","material_id","material_digest","dependency","lineage_id","batch_id",
                     "snapshot","mode","original_attempt_id","original_state"))):
                return {"state":"failed","reason":fresh.get("reason") or "memory_batch_changed"}
            retries = self.memory.consolidation.retries
            if fresh["mode"] == "resume_semantic":
                queued = await retries.resume_on(conn,batch,context,attempt_id,targeted=True)
            elif fresh["mode"] == "observe_original":
                queued = await retries.resume_rpc_on(conn,batch,context,attempt_id,targeted=True)
            else:
                queued = await retries.wake_due_on(conn,batch,context,attempt_id)
            if not queued:
                return {"state":"failed","reason":"memory_retry_not_available"}
        return {"state":"accepted","attempt_id":attempt_id,"batch_id":batch["batch_id"]}

    async def _observe_reorganization_on(self, conn, context, parent):
        parts = await (await conn.execute("WITH RECURSIVE tree AS ("
            "SELECT parent_batch_id,child_batch_id,state FROM memory.consolidation_reorganization_parts WHERE parent_batch_id=%s "
            "UNION ALL SELECT p.parent_batch_id,p.child_batch_id,p.state FROM memory.consolidation_reorganization_parts p "
            "JOIN tree t ON p.parent_batch_id=t.child_batch_id WHERE t.state='superseded') "
            "SELECT t.*,b.status AS child_status,b.manifest,b.dependency_refs,b.commit_refs,b.committed_at "
            "FROM tree t LEFT JOIN memory.consolidation_batches b ON b.batch_id=t.child_batch_id",
            (parent["batch_id"],))).fetchall()
        if not parts:
            return {"state":"unknown","reason":"memory_reorganization_receipts_missing"}
        if any(item["state"] == "failed" or item["child_status"] in {"failed","stale","revoked"} for item in parts):
            return {"state":"failed","reason":"memory_reorganization_child_failed"}
        if any(item["state"] not in {"applied","superseded"} or item["child_status"] != item["state"]
                for item in parts):
            return {"state":"pending","reason":"memory_reorganization_incomplete"}
        leaves = [item for item in parts if item["state"] == "applied"]
        if not leaves:
            return {"state":"unknown","reason":"memory_reorganization_receipts_missing"}
        expected = {(item["episode_id"],item["sequence"]) for item in parent["manifest"]["episodes"]}
        actual = []
        for child in leaves:
            child_batch = {"manifest":child["manifest"]}
            await self.memory.consolidation._assert_batch_generation_on(conn,child_batch)
            child_context = context_from_bound(child["manifest"]["authority_bound"])
            await self.identity.check_context(child_context,connection=conn)
            if not await self.sources.validate_persistence(conn,child_context,codec.refs(child["dependency_refs"])):
                return {"state":"failed","reason":"source_unavailable"}
            if not child["committed_at"] or child["commit_refs"].get("batch_id") != child["child_batch_id"]:
                return {"state":"unknown","reason":"memory_commit_receipt_incomplete"}
            actual.extend((item["episode_id"],item["sequence"]) for item in child["commit_refs"].get("coverage",()))
        if len(actual) != len(expected) or set(actual) != expected:
            return {"state":"unknown","reason":"memory_reorganization_coverage_incomplete"}
        return {"state":"verified","batch_id":parent["batch_id"],"child_batches":[item["child_batch_id"] for item in leaves],
            "observation_ref":"memory.reorganization:"+parent["batch_id"]}

    async def observe(self, _recovery, target, facts, _attempt_id):
        batch = await self._batch(target)
        if not batch:
            return {"state":"failed","reason":"memory_batch_unavailable"}
        try:
            context = context_from_bound(batch["manifest"]["authority_bound"])
            async with self.database.transaction() as conn:
                await self.memory.consolidation._assert_batch_generation_on(conn,batch)
                if not await self.sources.validate_persistence(conn,context,codec.refs(batch["dependency_refs"])):
                    return {"state":"failed","reason":"source_unavailable"}
                await self.identity.check_context(context,connection=conn)
                current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                    (batch["batch_id"],))).fetchone()
                if not current or current["manifest"].get("recovery",{}).get("lineage_id") != facts.get("lineage_id"):
                    return {"state":"failed","reason":"memory_batch_changed"}
                if current["status"] == "applied":
                    coverage = current["commit_refs"].get("coverage",())
                    expected = {(item["episode_id"],item["sequence"]) for item in current["manifest"]["episodes"]}
                    actual = [(item["episode_id"],item["sequence"]) for item in coverage]
                    if (not current["committed_at"] or current["commit_refs"].get("batch_id") != current["batch_id"]
                            or len(actual) != len(expected) or set(actual) != expected):
                        return {"state":"unknown","reason":"memory_commit_receipt_incomplete"}
                    return {"state":"verified","batch_id":current["batch_id"],
                        "observation_ref":"memory.batch:"+current["batch_id"]}
                if current["status"] == "superseded":
                    return await self._observe_reorganization_on(conn,context,current)
                if current["status"] in {"failed","stale","revoked"}:
                    return {"state":"failed","reason":current["error_code"] or current["status"]}
                last = await (await conn.execute("SELECT status FROM memory.consolidation_attempts WHERE batch_id=%s "
                    "ORDER BY number DESC LIMIT 1",(current["batch_id"],))).fetchone()
                if last and last["status"] == "unknown":
                    return {"state":"unknown","reason":"memory_original_attempt_unresolved"}
                return {"state":"pending","reason":current["status"]}
        except (AppError,KeyError) as error:
            if isinstance(error,AppError) and error.status not in {401,403,404,409}:
                raise
            return {"state":"failed","reason":getattr(error,"code","memory_batch_unavailable")}
