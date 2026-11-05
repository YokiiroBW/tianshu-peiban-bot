"""One conversation-domain writer: freeze once, observe once, apply all targets atomically."""

from copy import deepcopy
from dataclasses import replace
import asyncio
from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from aiohttp import ClientError
from psycopg.types.json import Jsonb

from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.models.codec import request_from_wire
from tianshu.models.budgets import MAX_INLINE_MEDIA_BYTES, MAX_INLINE_MEDIA_PARTS
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext
from . import codec
from .authority import context_record, request_identity
from .extraction_contract import MODEL_PROTOCOL, SCHEMA_VERSION, consolidation_example, consolidation_instruction, parse_model_proposal
from .generation import MODEL_REQUEST_SECONDS, REPAIR_LIMIT, native_request, repairable, repair_request, repair_input_estimate, repair_diagnostic, decode_model_json
from .materials import ConsolidationPolicy, MaterialBuilder, PreparationReads, append_context_material, batch_frozen, closed_episode_split, expand_record_endpoints, freeze_dependency_closure, frozen_mapping, model_messages, has_new_material, material_capabilities, partition_identity, refresh_read_set
from .consolidation_retry import ConsolidationRetries, failure_code
from .consolidation_wait import ConsolidationWait
from .recovery_wait import is_unresolved_wait
from .model_outcome import observe_original
from .preparation import candidate_coverage_reserve
from .proposal import COMPILER_VERSION, compile_proposal
from tianshu.models.retry import transient_model_failure

_ACTIVE = {"prepared", "waiting_model", "ready"}
_OBSERVABLE = _ACTIVE | {"quarantined"}
LEASE_SECONDS = MODEL_REQUEST_SECONDS + 60
LEASE_RENEW_SECONDS = 60
COMMIT_RESERVE_SECONDS = 5
PREPARATION_DEPENDENCY_WAIT = {"recovery_dependency_cooling", "recovery_dependency_probe_in_progress",
                               "recovery_claim_in_progress"}


def context_from_bound(value):
    return TrustedContext(value["principal_id"], value.get("actor_id"), value.get("person_id"),
        value.get("conversation_id"), tuple(value["scope_ids"]), tuple(value["audience"]),
        value["access_epoch"], OriginKind(value["origin_kind"]), frozenset(value.get("permissions", ())),
        value.get("conversation_kind"), bool(value.get("delivery_allowed", False)))


class Consolidation:
    def __init__(self, memory, settings=None):
        self.memory = memory
        self.database, self.identity, self.sources, self.jobs = memory.database, memory.identity, memory.sources, memory.jobs
        self.policy = ConsolidationPolicy.from_settings(settings)
        self.materials = MaterialBuilder(memory, self.policy)
        self.steps = self.counter = None
        self.retries = ConsolidationRetries(self)
        self.waits = ConsolidationWait(self)

    def bind_execution(self, steps, counter):
        self.steps, self.counter = steps, counter

    async def _assert_batch_generation_on(self, conn, batch):
        generation = batch["manifest"].get("data_generation")
        if not generation:
            raise AppError("job_generation_expired", status=409)
        await self.jobs.assert_generation_on(conn, generation)

    async def _register_recovery_on(self, conn, batch_id, frozen, context, request):
        policy = self.memory.recovery_policy
        if policy is None:
            return None
        version = body_hash({"frozen":frozen_mapping(frozen),"authority":context_record(context)})
        lineage = await policy.register_on(conn, domain="memory",subject_id=context.actor_id,
            owner="memory",material_id=batch_id,material_digest=body_hash(frozen_mapping(frozen)),
            dependency="models:"+(request.route_id or request.function),current_version=version,max_attempts=8,
            recovery_kind="pure_computation")
        await policy.reserve_attempt_on(conn,lineage["lineage_id"],owner="memory",expected_version=version,
            attempt_id=request.attempt_id,request_digest=body_hash(request_identity(wire(request))),
            route=request.route_id or request.function,original_state="none")
        return {"lineage_id":lineage["lineage_id"],"version":version,"owner":"memory"}

    async def _reserve_continuation_on(self, conn, recovery, request, *, prior_attempt_id, reason):
        policy = self.memory.recovery_policy
        if policy is None or not recovery:
            return
        claim = await policy.claim_continuation_on(conn,recovery["lineage_id"],owner=recovery["owner"],
            expected_version=recovery["version"],prior_attempt_id=prior_attempt_id,reason=reason)
        await policy.reserve_attempt_on(conn,recovery["lineage_id"],owner=recovery["owner"],
            expected_version=recovery["version"],attempt_id=request.attempt_id,
            request_digest=body_hash(request_identity(wire(request))),route=request.route_id or request.function,
            original_state="succeeded",claim_token=claim["claim_token"])

    async def _track_policy_on(self, conn, batch_id, step_id, recovery):
        if recovery:
            await conn.execute("UPDATE memory.consolidation_attempts SET recovery_lineage_id=%s,"
                "recovery_policy_state='reserved' WHERE batch_id=%s AND step_id=%s",
                (recovery["lineage_id"],batch_id,step_id))

    async def _validate_submission_on(self, conn, batch, context, step_id):
        for episode in sorted(batch["manifest"]["episodes"], key=lambda item:item["episode_id"]):
            row = await (await conn.execute("SELECT revision,sequence,extraction_cursor,content_hash,active "
                "FROM memory.episodes WHERE episode_id=%s FOR UPDATE", (episode["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"] != episode["revision"]
                    or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]
                    or episode.get("content_digest") is not None and row["content_hash"] != episode["content_digest"]):
                raise AppError("memory_consolidation_episode_changed", status=409)
        frozen = batch["manifest"].get("steps", {}).get(step_id)
        if not frozen:
            raise AppError("memory_consolidation_attempt_changed", status=409)
        await self.memory.claims.revisions.validate_read_set_on(conn, context, frozen)

    async def _admit_prepared(self, batch, *, owner):
        """Reserve the frozen request only after its batch is durably prepared."""
        context = context_from_bound(batch["manifest"]["authority_bound"])
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            if not await self.sources.validate_persistence(conn, context, codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable", status=403)
            await self.identity.check_context(context, connection=conn)
            state = await self._lock_owned_partition(conn, batch["partition_id"], owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch["batch_id"],))).fetchone()
            if (not current or current["status"] not in _OBSERVABLE or
                    current["status"] in _ACTIVE and state["active_batch_id"] != batch["batch_id"]):
                raise AppError("memory_consolidation_batch_changed", status=409)
            pending = current["manifest"].get("recovery_pending")
            if not pending:
                return
            if (current["dependency_refs"] != batch["dependency_refs"] or
                    pending != batch["manifest"].get("recovery_pending") or not current["step_ids"]):
                raise AppError("memory_consolidation_batch_changed", status=409)
            step_id = pending.get("step_id", current["step_ids"][-1])
            if current["step_ids"][-1] != step_id:
                raise AppError("memory_consolidation_batch_changed", status=409)
            step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s FOR UPDATE",
                (step_id,))).fetchone()
            if not step or step["state"] != "prepared":
                raise AppError("memory_consolidation_attempt_changed", status=409)
            request = request_from_wire(step["request"])
            if request.intent_id != step_id or request.data_generation != current["manifest"]["data_generation"]:
                raise AppError("memory_consolidation_attempt_changed", status=409)
            await self._validate_submission_on(conn, current, context, step_id)
            if self.memory.recovery_policy is None:
                raise AppError("memory_recovery_policy_unavailable", status=503)
            manifest = deepcopy(current["manifest"])
            if pending["kind"] == "original":
                recovery = await self._register_recovery_on(conn, current["batch_id"], manifest["frozen"], context, request)
            elif pending["kind"] == "continuation" and manifest.get("recovery"):
                recovery = manifest["recovery"]
                await self._reserve_continuation_on(conn, recovery, request,
                    prior_attempt_id=pending["prior_attempt_id"], reason=pending["reason"])
            else:
                raise AppError("memory_recovery_preparation_invalid", status=409)
            manifest["recovery"] = recovery
            del manifest["recovery_pending"]
            await self._track_policy_on(conn, current["batch_id"], step_id, recovery)
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest), current["batch_id"]))

    async def cancel_recovery_on(self, conn, manifest):
        recovery = manifest.get("recovery")
        if self.memory.recovery_policy and recovery:
            await self.memory.recovery_policy.update_version_on(conn,recovery["lineage_id"],
                owner=recovery["owner"],expected_version=recovery["version"],
                current_version=recovery["version"],cancelled=True)

    async def claim_due_recoveries(self, *, generation=None):
        policy = self.memory.recovery_policy
        if policy is None:
            return
        async with self.database.transaction(read_only=True) as conn:
            if generation is not None and await self.jobs.current_generation_on(conn) != generation:
                raise AppError("job_generation_expired",status=409)
            candidates = await (await conn.execute("SELECT b.batch_id,b.partition_id,"
                "b.manifest->'recovery'->>'lineage_id' AS lineage_id FROM memory.consolidation_batches b "
                "JOIN runtime.recovery_lineages l ON l.lineage_id=b.manifest->'recovery'->>'lineage_id' "
                "AND l.owner='memory' AND l.data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "WHERE b.status='quarantined' AND NOT (b.manifest ? 'remote_wait_unresolved') "
                "AND (b.retry_due_at IS NULL OR b.retry_due_at<=now()) "
                "AND l.due_at<=now() AND NOT l.paused AND NOT l.cancelled AND NOT l.source_withdrawn "
                "AND l.attempts<l.max_attempts AND (l.claim_token IS NULL OR l.claim_expires_at<=now()) "
                "AND b.manifest->>'data_generation'=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "ORDER BY l.due_at,b.batch_id LIMIT 64")).fetchall()
        seen = set()
        for candidate in candidates:
            if candidate["lineage_id"] in seen:
                continue
            seen.add(candidate["lineage_id"])
            async with self.database.transaction() as conn:
                if generation is not None:
                    await self.jobs.assert_generation_on(conn,generation)
                state = await (await conn.execute("SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                    (candidate["partition_id"],))).fetchone()
                batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                    (candidate["batch_id"],))).fetchone()
                if (not state or not batch or batch["status"] != "quarantined"
                        or batch["manifest"].get("remote_wait_unresolved")
                        or batch["manifest"].get("recovery",{}).get("lineage_id") != candidate["lineage_id"]
                        or batch.get("retry_due_at") and batch["retry_due_at"] > datetime.now(UTC)):
                    continue
                claim = await policy.claim_lineage_due_on(conn,candidate["lineage_id"],owner="memory",lease_seconds=30)
                if not claim:
                    continue
                await conn.execute("INSERT INTO memory.recovery_due_claims(lineage_id,claim_token,current_version,expires_at) "
                    "VALUES(%s,%s,%s,%s) ON CONFLICT(lineage_id) DO UPDATE SET "
                    "claim_token=excluded.claim_token,current_version=excluded.current_version,expires_at=excluded.expires_at",
                    (claim["lineage_id"],claim["claim_token"],claim["current_version"],claim["claim_expires_at"]))
                await self._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
                    suffix="recovery-claim:"+batch["batch_id"])

    async def _policy_outcome(self, batch_id, step_id, *, status, code=None, observation_ref=None, retry_after=None):
        policy = self.memory.recovery_policy
        if policy is None:
            return
        from tianshu.runtime.recovery_policy import FailureCategory, classify_failure
        receipt = None
        if status == "unknown":
            from .model_outcome import confirmed_computation_unknown, original_status
            _, original = await self.steps.recorded(step_id)
            receipt = await original_status(self.steps.models, original)
            if not confirmed_computation_unknown(receipt):
                return
        async with self.database.transaction() as conn:
            batch = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                (batch_id,))).fetchone()
            if not batch:
                return
            await self._assert_batch_generation_on(conn, batch)
            row = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s "
                "AND step_id=%s FOR UPDATE", (batch_id,step_id))).fetchone()
            if not row or not row.get("recovery_lineage_id") or row.get("recovery_policy_state") in {"succeeded","failed","cancelled"}:
                return
            attempt_id = row["model_attempt_id"]
            previous = row.get("recovery_policy_state")
            if status == "succeeded":
                if previous == "unknown":
                    await policy.resolve_unknown_on(conn,attempt_id,observation_ref=observation_ref or "models:"+attempt_id,
                        state="succeeded")
                else:
                    await policy.succeed_attempt_on(conn,attempt_id)
                settled = "succeeded"
            elif status in {"failed","cancelled"}:
                category = classify_failure(code or "model_outcome_"+status)
                # Models already names these failures as temporary. Preserve
                # that domain evidence when the shared classifier is generic.
                if category == FailureCategory.OTHER and transient_model_failure(code):
                    category = FailureCategory.TRANSPORT
                if previous == "unknown":
                    await policy.resolve_unknown_on(conn,attempt_id,observation_ref=observation_ref or "models:"+attempt_id,
                        state="failed",code=code or "model_outcome_"+status,category=category,retry_after=retry_after)
                else:
                    await policy.fail_attempt_on(conn,attempt_id,code=code or "model_outcome_"+status,
                        category=category,retry_after=retry_after)
                settled = status
            elif status == "unknown" and previous != "unknown":
                recovery = batch["manifest"]["recovery"]
                await policy.record_computation_unknown_on(conn,attempt_id,owner=recovery["owner"],
                    expected_version=recovery["version"],model_receipt=receipt,retry_after=retry_after)
                settled = "unknown"
            else:
                return
            await conn.execute("UPDATE memory.consolidation_attempts SET recovery_policy_state=%s "
                "WHERE batch_id=%s AND step_id=%s",(settled,batch_id,step_id))

    async def _assert_attempt_applicable_on(self, conn, batch, step_id):
        recovery = batch["manifest"].get("recovery")
        policy = self.memory.recovery_policy
        if not recovery or policy is None or step_id is None:
            return
        attempt = await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts "
            "WHERE batch_id=%s AND step_id=%s", (batch["batch_id"], step_id))).fetchone()
        if attempt is None:
            raise AppError("memory_consolidation_attempt_missing", status=409)
        await policy.assert_attempt_applicable_on(conn, attempt["model_attempt_id"],
            owner=recovery["owner"], expected_version=recovery["version"])

    async def _schedule_on(self, conn, partition_id, due, *, suffix="", target_batch_id=None):
        state = await (await conn.execute("SELECT partition_id FROM memory.consolidation_state "
            "WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
        if state is None:
            return None
        payload = {"partition_id":partition_id}
        if target_batch_id is not None:
            payload["batch_id"] = target_batch_id
        lock = ("memory-consolidate:" + partition_id if target_batch_id is None else
                "memory-consolidate-batch:" + partition_id + ":" + target_batch_id)
        return await self.jobs.enqueue(conn, JobSpec("memory.consolidate",
            f"memory-consolidate:{partition_id}:{due.isoformat()}:{suffix}:{uuid4()}",
            payload, ServiceClass.ELASTIC, scheduled_at=due), queueing_lock=lock)

    async def lock_partition_on(self,conn,context,scope_ids):
        scene_id = self.identity.scene_id(context)
        partition_id, scope_key, bound = partition_identity(context, scope_ids, scene_id)
        scene_authority = wire(await self.identity.scene_authority_on(conn, context))
        if context.conversation_kind == "group":
            scene_authority["person_id"] = None
        bound["scene_authority"] = scene_authority
        await conn.execute(
            "INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,conversation_id,scope_key,authority_bound) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (partition_id, context.actor_id, context.principal_id, context.conversation_id, scope_key, Jsonb(bound)))
        state = await (await conn.execute(
            "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
        if state["authority_bound"] != bound or state["scope_key"] != scope_key:
            state = await (await conn.execute("UPDATE memory.consolidation_state SET scope_key=%s,authority_bound=%s "
                "WHERE partition_id=%s RETURNING *", (scope_key,Jsonb(bound),partition_id))).fetchone()
        return state

    async def dirty_on(self, conn, context, row, *, state=None):
        state = state or await self.lock_partition_on(conn,context,row["scope_ids"])
        partition_id=state["partition_id"]
        await conn.execute("UPDATE memory.episodes SET consolidation_partition_id=%s,consolidation_authority=%s "
            "WHERE episode_id=%s AND sequence=%s",
            (partition_id,Jsonb(state["authority_bound"]),row["episode_id"],row["sequence"]))
        now = datetime.now(UTC)
        first = state["first_pending_at"] or now
        due = min(now+timedelta(seconds=self.policy.quiet_seconds), first+timedelta(seconds=self.policy.max_wait_seconds))
        count = await (await conn.execute(
            "SELECT count(*) AS total,COALESCE(sum(length(summary)),0) AS characters FROM "
            "(SELECT summary FROM memory.episodes WHERE consolidation_partition_id=%s "
            "AND active AND extraction_state='pending' AND scope_ids<@%s::text[] LIMIT %s) e",
            (partition_id,list(context.scope_ids),self.policy.max_episodes))).fetchone()
        if count["total"] >= self.policy.max_episodes or count["characters"] >= self.policy.max_source_characters:
            due = now
        await conn.execute(
            "UPDATE memory.consolidation_state SET dirty_generation=dirty_generation+1,first_pending_at=%s,"
            "last_activity_at=%s,wake_due_at=%s,updated_at=now() WHERE partition_id=%s",
            (first, now, due, partition_id))
        # Reuse the pending wake, or leave one successor if the old signal is
        # already doing. An early wake still checks the latest batching due.
        await self._schedule_on(conn, partition_id, due)
        return partition_id

    async def run(self, partition_id, *, target_batch_id=None):
        owner = str(uuid4())
        async with self.database.transaction() as conn:
            run_generation = await self.jobs.current_generation_on(conn)
            await self.jobs.assert_generation_on(conn, run_generation)
            state = await (await conn.execute(
                "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
            if not state:
                return
            now = datetime.now(UTC)
            if is_unresolved_wait(state.get("last_error")) and not state["active_batch_id"]:
                raise AppError("memory_remote_wait_unresolved", status=503)
            if state["lease_until"] and state["lease_until"] > now:
                return
            held = await (await conn.execute("SELECT batch_id FROM memory.consolidation_batches "
                "WHERE partition_id=%s AND status='quarantined' AND manifest->>'data_generation'=%s "
                "AND (NOT (manifest ? 'local_wait') OR retry_due_at<=now()) "
                "AND (NOT (manifest ? 'remote_wait_unresolved') OR %s::text IS NOT NULL AND batch_id=%s) "
                "AND (%s::text IS NULL OR batch_id=%s) "
                "ORDER BY COALESCE(retry_due_at,updated_at),batch_id LIMIT 1",
                (partition_id,run_generation,target_batch_id,target_batch_id,target_batch_id,target_batch_id))).fetchone()
            if target_batch_id is not None and (not held or state["active_batch_id"]):
                return
            if not state["active_batch_id"] and state["wake_due_at"] is None and not held:
                await self.retries.arm_on(conn, partition_id)
                return
            if not held and not state["active_batch_id"] and state["wake_due_at"] and state["wake_due_at"] > now:
                await self._schedule_on(conn, partition_id, state["wake_due_at"])
                return
            lease = await (await conn.execute(
                "UPDATE memory.consolidation_state SET lease_owner=%s,lease_until=now()+%s*interval '1 second',updated_at=now() "
                "WHERE partition_id=%s RETURNING lease_until", (owner, LEASE_SECONDS, partition_id))).fetchone()
        batch_id = state["active_batch_id"]
        suppress_rearm = False
        leave_lease = False
        unresolved_marked = False
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._keep_lease(partition_id, owner, run_generation,
            asyncio.current_task(), lease_lost), name="memory-consolidation-lease")

        async def handle_failure(target, error):
            nonlocal suppress_rearm, leave_lease, unresolved_marked
            try:
                await self._failure(partition_id, target, error, owner=owner, generation=run_generation)
            except Exception as failure:
                if classify_database_failure(failure) is not None:
                    suppress_rearm = True
                if isinstance(failure, RecoveryWait):
                    if failure.reason == "recovery_claim_lost":
                        suppress_rearm = leave_lease = True
                    elif not recovery_wait_retryable(failure):
                        suppress_rearm = True
                        unresolved_marked = await self.waits.mark_unresolved(partition_id, target, failure, owner=owner, generation=run_generation)
                raise

        try:
            context = context_from_bound(state["authority_bound"])
            # One bounded original observation per wake. An unavailable original
            # must not prevent this same wake from processing unrelated input.
            if held:
                try:
                    resumed = await self.waits.reactivate(held["batch_id"], owner=owner)
                    if resumed != "blocked":
                        result = await self._execute(held["batch_id"], context, owner=owner)
                        if result is not None or resumed == "active":
                            return result
                except (AppError, TimeoutError, OSError, ClientError, ValueError, TypeError) as exc:
                    if isinstance(exc, RecoveryWait) and exc.reason != "recovery_claim_lost" and not recovery_wait_retryable(exc):
                        suppress_rearm = True
                        unresolved_marked = await self.waits.mark_unresolved(partition_id,held["batch_id"],exc,owner=owner,generation=run_generation)
                        raise
                    if isinstance(exc, AppError) and exc.code == "memory_remote_wait_unresolved":
                        suppress_rearm = True
                        raise
                    if ((isinstance(exc, RecoveryWait) and exc.reason == "recovery_claim_lost")
                            or classify_database_failure(exc) is not None):
                        suppress_rearm = True
                        leave_lease = isinstance(exc, RecoveryWait) and exc.reason == "recovery_claim_lost"
                        raise
                    await handle_failure(held["batch_id"], exc)
                if target_batch_id is not None:
                    return
            if not batch_id and state["wake_due_at"] is None:
                return
            if not batch_id and state["wake_due_at"] and state["wake_due_at"] > datetime.now(UTC):
                return
            if not batch_id:
                batch_id = await self._prepare(state, context, owner,
                    budget=self.preparation_budget(lease["lease_until"]))
            if batch_id:
                await self._execute(batch_id, context, owner=owner)
        except asyncio.CancelledError:
            if not lease_lost.is_set():
                raise
            worker = asyncio.current_task()
            worker.uncancel()  # Consume only the lease heartbeat's own cancellation.
            if worker.cancelling():
                raise
            # The next owner now owns all batch/episode mutations. Cancellation
            # still reaches the original ModelClient, preserving its receipt.
        except (AppError, TimeoutError, OSError, ClientError, ValueError, TypeError) as exc:
            if isinstance(exc, RecoveryWait) and exc.reason != "recovery_claim_lost" and not recovery_wait_retryable(exc):
                suppress_rearm = True
                if not unresolved_marked:
                    await self.waits.mark_unresolved(partition_id,batch_id,exc,owner=owner,generation=run_generation)
                raise
            if isinstance(exc, AppError) and exc.code == "memory_remote_wait_unresolved":
                suppress_rearm = True
                raise
            if ((isinstance(exc, RecoveryWait) and exc.reason == "recovery_claim_lost")
                    or classify_database_failure(exc) is not None):
                suppress_rearm = True
                leave_lease = isinstance(exc, RecoveryWait) and exc.reason == "recovery_claim_lost"
                raise
            await handle_failure(batch_id, exc)
        except Exception as exc:
            if classify_database_failure(exc) is not None:
                suppress_rearm = True
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            # A lost policy claim fences all domain writes by this claimant.
            # Its lease expires naturally; the periodic owner scan repairs a wake.
            if not leave_lease:
                async with self.database.transaction() as conn:
                    try:
                        await self.jobs.assert_generation_on(conn, run_generation)
                    except AppError as error:
                        if error.code != "job_generation_expired":
                            raise
                    else:
                        released = await (await conn.execute(
                            "UPDATE memory.consolidation_state SET lease_owner=NULL,lease_until=NULL "
                            "WHERE partition_id=%s AND lease_owner=%s RETURNING active_batch_id,wake_due_at",
                            (partition_id, owner))).fetchone()
                        if released and released["active_batch_id"] is None and not suppress_rearm:
                            # A concurrent wake may have run while this owner still held
                            # the lease. Re-arm remaining input after releasing only ours.
                            await self.retries.arm_on(conn, partition_id)
                        elif released and not suppress_rearm:
                            # The completion notice is a wake signal, not the model
                            # receipt. A bounded fallback also covers lost notices.
                            active = await (await conn.execute("SELECT status FROM memory.consolidation_batches "
                                "WHERE batch_id=%s AND manifest->>'data_generation'=%s",
                                (released["active_batch_id"],run_generation))).fetchone()
                            if active:
                                delay = 30 if active["status"] == "waiting_model" else 0
                                await self._schedule_on(conn,partition_id,datetime.now(UTC)+timedelta(seconds=delay),suffix="active")

    async def _renew_lease(self, partition_id, owner, data_generation):
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn, data_generation)
            row = await (await conn.execute("UPDATE memory.consolidation_state "
                "SET lease_until=now()+%s*interval '1 second' "
                "WHERE partition_id=%s AND lease_owner=%s AND lease_until>now() RETURNING partition_id",
                (LEASE_SECONDS, partition_id, owner))).fetchone()
        return row is not None

    async def _keep_lease(self, partition_id, owner, data_generation, worker, lost):
        try:
            while True:
                await asyncio.sleep(min(LEASE_RENEW_SECONDS, LEASE_SECONDS / 3))
                if not await self._renew_lease(partition_id, owner, data_generation):
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            # An unconfirmed lease cannot authorize a late apply.
            pass
        lost.set()
        worker.cancel()

    @staticmethod
    def preparation_budget(lease_until):
        # Retain the preparation allowance at the assembly boundary. Background
        # candidate phases do not treat it as a business/queue expiry, and the
        # owner heartbeat covers both preparation and admitted model execution.
        return RequestBudget(lease_until - timedelta(seconds=MODEL_REQUEST_SECONDS + COMMIT_RESERVE_SECONDS))

    def _request(self, context, frozen, step_id, *, context_round=0, requests=()):
        messages = model_messages(frozen, self.policy, context_round=context_round, requests=requests)
        payload = json.loads(messages[0].parts[0].text)
        images = [part.media for message in messages for part in message.parts if part.kind == "image" and part.media]
        invalid_sizes = sum(type(media.size_bytes) is not int or media.size_bytes < 0 for media in images)
        media_bytes = sum(media.size_bytes for media in images if type(media.size_bytes) is int and media.size_bytes >= 0)
        if invalid_sizes or media_bytes > MAX_INLINE_MEDIA_BYTES or len(images) > MAX_INLINE_MEDIA_PARTS:
            raise AppError("memory_consolidation_input_budget", details={
                "media_bytes":media_bytes,"media_bytes_limit":MAX_INLINE_MEDIA_BYTES,
                "media_parts":len(images),"media_parts_limit":MAX_INLINE_MEDIA_PARTS,"media_size_invalid":invalid_sizes,
                "new_characters":sum(len(item["text"]) for item in payload["materials"] if item["role"]=="new")})
        instruction = consolidation_instruction(frozen)
        attempt_id=str(uuid5(NAMESPACE_URL,step_id))
        estimate = self.counter.measure(instruction, messages, ())[0]
        repair_estimate=repair_input_estimate(self.counter,instruction,messages,attempt_id)
        if max(estimate,repair_estimate) > self.policy.max_input_tokens:
            # Measure the same schema, identity mappings and full metadata with
            # text removed. Character count alone cannot budget UTF-8 evidence.
            fixed=deepcopy(payload)
            for material in fixed["materials"]:
                material["text"]=""
            # Image estimates remain in the fixed cost. A textual caption never
            # substitutes for the original image in an otherwise fitting input.
            fixed_messages=(ModelMessage("user",(ModelPart("text",json.dumps(wire(fixed),ensure_ascii=False,separators=(",",":"))),
                *messages[0].parts[1:])),*messages[1:])
            fixed_tokens=max(self.counter.measure(instruction,fixed_messages,())[0],
                repair_input_estimate(self.counter,instruction,fixed_messages,attempt_id))
            raise AppError("memory_consolidation_input_budget", details={
                "estimated":estimate if estimate>self.policy.max_input_tokens else repair_estimate,
                "request_estimated":estimate,"repair_required_tokens":repair_estimate,
                "input_budget": self.policy.max_input_tokens, "visible_output": self.policy.max_output_tokens,
                "reasoning_reserve":self.policy.reasoning_reserve_tokens,"fixed_tokens":fixed_tokens,
                "new_characters":sum(len(item["text"]) for item in payload["materials"] if item["role"]=="new")})
        return ModelRequest(step_id, attempt_id, "memory.extract", ServiceClass.ELASTIC,
            context, RequestBudget(datetime.now(UTC)+timedelta(seconds=MODEL_REQUEST_SECONDS)), messages,
            required_capabilities=material_capabilities(frozen),
            execution_timeout_seconds=MODEL_REQUEST_SECONDS,
            system=instruction, max_output_tokens=self.policy.max_output_tokens+self.policy.reasoning_reserve_tokens, estimated_input_tokens=estimate,
            source_refs=self.materials.dependencies(frozen))

    async def _prepare(self, state, context, owner, *, budget):
        # A scene may contain several principals. Select the oldest input and
        # its own frozen authorization, never the latest sender's authority.
        async with self.database.transaction(read_only=True) as conn:
            data_generation = await self.jobs.current_generation_on(conn)
            first = await (await conn.execute("SELECT * FROM memory.episodes WHERE consolidation_partition_id=%s "
                "AND active AND extraction_state='pending' ORDER BY sequence LIMIT 1", (state["partition_id"],))).fetchone()
            if first:
                bound = first.get("consolidation_authority") or state["authority_bound"]
                original = context_from_bound(bound)
                try:
                    current = await self.identity.context_on(conn, original.principal_id, actor_id=original.actor_id,
                        person_id=original.person_id, conversation_id=original.conversation_id,
                        origin_kind=original.origin_kind)
                    permissions = (current.permissions if "*" in original.permissions else original.permissions
                        if "*" in current.permissions else original.permissions & current.permissions)
                    context = replace(current,
                        scope_ids=tuple(scope for scope in original.scope_ids if scope in current.scope_ids),
                        audience=tuple(scope for scope in original.audience if scope in current.audience),
                        permissions=frozenset(permissions),
                        delivery_allowed=original.delivery_allowed and current.delivery_allowed)
                    await self.identity.check_context(context, connection=conn)
                    batch_bound = {**context_record(context), "scene_id": bound.get("scene_id"),
                        "write_scope_ids": bound.get("write_scope_ids", []),
                        "scene_authority": wire(await self.identity.scene_authority_on(conn, context))}
                except AppError as error:
                    error.details = {**(error.details or {}), "episode_id": first["episode_id"],
                        "episode_revision": first["revision"], "episode_sequence": first["sequence"]}
                    raise
        if not first:
            await self._idle(state["partition_id"], owner, state["dirty_generation"])
            return None
        rows = await self.materials.pending(context, state["partition_id"], authority=bound)
        if not rows or rows[0]["episode_id"] != first["episode_id"]:
            raise AppError("source_unavailable",status=403,details={"episode_id": first["episode_id"],
                "episode_revision": first["revision"], "episode_sequence": first["sequence"]})
        async with self.database.transaction(read_only=True) as conn:
            part = await (await conn.execute(
                "SELECT p.* FROM memory.consolidation_reorganization_parts p "
                "JOIN memory.consolidation_reorganizations r USING(root_batch_id) "
                "WHERE r.partition_id=%s AND p.state='pending' AND p.episodes @> %s::jsonb "
                "ORDER BY p.created_at,p.ordinal LIMIT 1",
                (state["partition_id"],Jsonb([{"episode_id":first["episode_id"]}])))).fetchone()
            if part:
                part_ids = [episode["episode_id"] for episode in part["episodes"]]
                rows = await (await conn.execute(
                    "SELECT * FROM memory.episodes WHERE actor_id=%s AND principal_id=%s "
                    "AND consolidation_partition_id=%s AND active AND extraction_state='pending' "
                    "AND consolidation_authority=%s::jsonb AND scope_ids<@%s::text[] "
                    "AND episode_id=ANY(%s::text[]) ORDER BY sequence",
                    (context.actor_id, context.principal_id, state["partition_id"],Jsonb(bound),
                     list(context.scope_ids),part_ids))).fetchall()
                if (len(rows) != len(part_ids) or any(
                        row["episode_id"] != episode["episode_id"] or row["revision"] != episode["revision"]
                        or row["sequence"] != episode["sequence"] or wire(row["extraction_cursor"]) != episode["before"]
                        for row, episode in zip(rows, part["episodes"], strict=True))):
                    raise AppError("memory_reorganization_input_changed", status=409)
        batch_id = "memory_batch_"+uuid4().hex
        text_ceiling = min(self.policy.max_source_characters, self.policy.max_input_tokens//2)
        reads=PreparationReads(self.sources)
        prefetched = None

        def reuse_candidates(reduced, original):
            reduced["records"] = deepcopy(original["records"])
            for subject, details in original["subjects"].items():
                reduced["subjects"].setdefault(subject, details)
                reduced["targets"].setdefault(subject, {"new_material_ids": []})
            reduced["candidate_subject_ids"] = list(original.get("candidate_subject_ids", ()))
            for key, value in original.get("selection_coverage", {}).items():
                if key.startswith("record_"):
                    reduced.setdefault("selection_coverage", {})[key] = deepcopy(value)
            refresh_read_set(reduced)

        while True:
            frozen = None
            closure_frozen = None
            try:
                reads.metrics.count("assembly_attempts")
                with reads.metrics.phase("assembly_ms"):
                    frozen=await self.materials.build(context,rows,batch_id,text_ceiling=text_ceiling,
                        reads=reads,budget=budget,include_records=False)
                if not frozen:
                    await self._idle(state["partition_id"], owner, state["dirty_generation"])
                    return None
                if prefetched:
                    reuse_candidates(frozen, prefetched)
                    closure_frozen = deepcopy(frozen)
                if part and ([item["episode_id"] for item in frozen["episodes"]] != part_ids
                             or any(not item["complete"] for item in frozen["episodes"])):
                    raise AppError("memory_reorganization_input_not_closed", status=409)
                step_id=batch_id+":step:0"
                request=self._budgeted_request(context,frozen,step_id,metrics=reads.metrics,
                    allow_required_context=False,reserve_candidates=not bool(prefetched)) if has_new_material(frozen) else None
                break
            except AppError as exc:
                if exc.code not in {"memory_consolidation_input_budget","memory_identity_candidates_too_large"}:
                    raise
                if exc.code != "memory_consolidation_input_budget" or not frozen:
                    raise
                if part:
                    await self._record_closed_component(state,context,owner,batch_id,frozen,exc,
                        data_generation=data_generation,authority_bound=batch_bound,source_budget=text_ceiling,part=part)
                    return None
                if not prefetched:
                    await self.materials.attach_records(context,frozen,reads=reads,budget=budget)
                    prefetched = deepcopy(frozen)
                    closure_frozen = prefetched
                split = closed_episode_split(closure_frozen)
                if not split:
                    await self._record_closed_component(state,context,owner,batch_id,closure_frozen,exc,
                        data_generation=data_generation,authority_bound=batch_bound,source_budget=text_ceiling)
                    return None
                first_ids = {episode["episode_id"] for episode in split[0]}
                rows = [row for row in rows if row["episode_id"] in first_ids]
        if request:
            # Only this final new-material/source range can incur optional remote
            # work. Candidate size never starts another material-build trial.
            if not prefetched:
                await self.materials.attach_records(context,frozen,reads=reads,budget=budget)
            # Estimate the combined target/operation shape after the one
            # candidate lookup. Rebuild only the selected material prefix and
            # reuse the already-read candidates if an actually closed cut fits.
            estimated_output = 48 * len(frozen["targets"]) + 96 * sum(
                item["role"] == "new" for item in frozen["materials"].values())
            split = closed_episode_split(frozen) if not part and estimated_output > self.policy.max_output_tokens else None
            if split:
                first_ids = {episode["episode_id"] for episode in split[0]}
                first_rows = [row for row in rows if row["episode_id"] in first_ids]
                reduced = await self.materials.build(context,first_rows,batch_id,text_ceiling=text_ceiling,
                    reads=reads,budget=budget,include_records=False)
                if (reduced and {episode["episode_id"] for episode in reduced["episodes"]} == first_ids
                        and all(episode["complete"] for episode in reduced["episodes"])):
                    reuse_candidates(reduced, frozen)
                    frozen = reduced
            try:
                request=self._budgeted_request(context,frozen,step_id,metrics=reads.metrics,allow_required_context=False)
            except AppError as exc:
                if exc.code != "memory_consolidation_input_budget" or closed_episode_split(frozen):
                    raise
                await self._record_closed_component(state,context,owner,batch_id,frozen,exc,
                    data_generation=data_generation,authority_bound=batch_bound,source_budget=text_ceiling,part=part)
                return None
            with reads.metrics.phase("route_ms"):
                request=replace(request,data_generation=data_generation)
                request=await native_request(self.steps.models,request)
        dependencies = self.materials.dependencies(frozen)
        manifest = {"frozen": frozen_mapping(frozen), "episodes": frozen["episodes"], "source_budget":text_ceiling,
            "budget":self._budget_record(),
            "preparation":{**reads.metrics.snapshot(),"final_episodes":len(frozen["episodes"]),
                "final_materials":len(frozen["materials"]),"final_new_materials":sum(
                    item["role"]=="new" for item in frozen["materials"].values()),
                "estimated_output_tokens":estimated_output if request else 0,
                "final_images":sum(part.kind=="image" for message in request.messages for part in message.parts) if request else 0},
            "authority_bound": batch_bound, "generation": state["dirty_generation"], "data_generation": data_generation,
            "steps": {step_id: frozen_mapping(frozen)} if request else {}, "context_round": 0}
        if part:
            manifest["reorganization"] = {"root_batch_id": part["root_batch_id"],
                "parent_batch_id": part["parent_batch_id"], "ordinal": part["ordinal"]}
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn, data_generation)
            if not await self.sources.validate_persistence(conn, context, dependencies):
                raise AppError("source_unavailable", status=403)
            current = await (await conn.execute(
                "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (state["partition_id"],))).fetchone()
            if current["lease_owner"] != owner or current["active_batch_id"]:
                return current["active_batch_id"]
            await self.identity.check_context(context, connection=conn)
            for episode in frozen["episodes"]:
                original = await (await conn.execute(
                    "SELECT revision,sequence,extraction_cursor,content_hash,active FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (episode["episode_id"],))).fetchone()
                if (not original or not original["active"] or original["revision"] != episode["revision"]
                        or original["sequence"] != episode["sequence"] or original["extraction_cursor"] != episode["before"]
                        or episode.get("content_digest") is not None and original["content_hash"] != episode["content_digest"]):
                    raise AppError("memory_consolidation_episode_changed", status=409)
            recovery = None
            if request and part:
                root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                    (part["root_batch_id"],))).fetchone()
                if not root or root["manifest"].get("data_generation") != data_generation:
                    raise AppError("job_generation_expired",status=409)
                recovery = root["manifest"].get("recovery")
                if recovery and self.memory.recovery_policy is not None:
                    prior = await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts "
                        "WHERE recovery_lineage_id=%s AND recovery_policy_state='succeeded' "
                        "ORDER BY created_at DESC,step_id DESC LIMIT 1",(recovery["lineage_id"],))).fetchone()
                    if not prior:
                        raise AppError("memory_recovery_prior_unavailable",status=409)
                    manifest["recovery_pending"] = {"kind":"continuation",
                        "prior_attempt_id":prior["model_attempt_id"], "reason":"capacity_repartition"}
            elif request and self.memory.recovery_policy is not None:
                manifest["recovery_pending"] = {"kind":"original"}
            if recovery:
                manifest["recovery"] = recovery
            if request:
                await self.steps.prepare_on(conn, step_id, request)
            await conn.execute(
                "INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,read_set,dependency_refs,context_fingerprint,step_ids) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (batch_id, state["partition_id"], SCHEMA_VERSION, "prepared" if request else "ready", Jsonb(wire(manifest)),
                 Jsonb(wire(frozen["read_set"])), Jsonb(wire(dependencies)), body_hash(context_record(context)), [step_id] if request else []))
            if part:
                reserved = await (await conn.execute("UPDATE memory.consolidation_reorganization_parts "
                    "SET state='active',child_batch_id=%s WHERE parent_batch_id=%s AND ordinal=%s AND state='pending' "
                    "RETURNING parent_batch_id", (batch_id,part["parent_batch_id"],part["ordinal"]))).fetchone()
                if not reserved:
                    raise AppError("memory_reorganization_part_changed", status=409)
            if request:
                await self.retries.track_on(conn, batch_id, step_id, kind="original")
                if not manifest.get("recovery_pending"):
                    await self._track_policy_on(conn,batch_id,step_id,recovery)
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=%s,last_error=NULL,budget_failure=NULL "
                "WHERE partition_id=%s", (batch_id,state["partition_id"]))
            for episode in frozen["episodes"]:
                await conn.execute(
                    "UPDATE memory.episodes SET extraction_state='running',extraction_started_at=now(),extraction_sources=%s "
                    "WHERE episode_id=%s AND sequence=%s", (Jsonb(episode["source_refs"]), episode["episode_id"],episode["sequence"]))
        return batch_id

    async def _record_closed_component(self, state, context, owner, batch_id, frozen, error, *,
                                       data_generation, authority_bound, source_budget, part=None):
        """Retain a source-backed batch when its closed input cannot fit at all."""
        fixed = frozen_mapping(frozen)
        dependencies = self.materials.dependencies(frozen)
        failure = {"reason":"closed_component_exceeds_budget", "stage":"preparation",
            "original_error_code":error.code, "dependency_closure":fixed.get("dependency_closure"),
            "configuration":self._input_budget_record(), "required_tokens":(error.details or {}).get("estimated")}
        manifest = {"frozen":fixed,"episodes":fixed["episodes"],"source_budget":source_budget,
            "budget":self._budget_record(),"authority_bound":authority_bound,"generation":state["dirty_generation"],
            "data_generation":data_generation,"steps":{},"context_round":0,"closed_component_failure":failure}
        if part:
            manifest["reorganization"]={"root_batch_id":part["root_batch_id"],
                "parent_batch_id":part["parent_batch_id"],"ordinal":part["ordinal"]}
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn,data_generation)
            if not await self.sources.validate_persistence(conn,context,dependencies):
                raise AppError("source_unavailable",status=403)
            current = await self._lock_owned_partition(conn,state["partition_id"],owner)
            if current["active_batch_id"]:
                raise AppError("memory_consolidation_attempt_changed",status=409)
            await self.identity.check_context(context,connection=conn)
            for episode in fixed["episodes"]:
                row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (episode["episode_id"],))).fetchone()
                if (not row or not row["active"] or row["revision"] != episode["revision"]
                        or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]):
                    raise AppError("memory_consolidation_episode_changed",status=409)
            if part:
                root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                    (part["root_batch_id"],))).fetchone()
                if not root or root["manifest"].get("data_generation") != data_generation:
                    raise AppError("job_generation_expired",status=409)
                if root["manifest"].get("recovery"):
                    manifest["recovery"] = root["manifest"]["recovery"]
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,"
                "manifest,read_set,dependency_refs,context_fingerprint,step_ids,error_code) "
                "VALUES(%s,%s,%s,'failed',%s,%s,%s,%s,ARRAY[]::text[],'closed_component_exceeds_budget')",
                (batch_id,state["partition_id"],SCHEMA_VERSION,Jsonb(wire(manifest)),Jsonb(wire(fixed["read_set"])),
                 Jsonb(wire(dependencies)),body_hash(context_record(context))))
            if part:
                changed = await (await conn.execute("UPDATE memory.consolidation_reorganization_parts "
                    "SET state='failed',child_batch_id=%s WHERE parent_batch_id=%s AND ordinal=%s AND state='pending' "
                    "RETURNING parent_batch_id",(batch_id,part["parent_batch_id"],part["ordinal"]))).fetchone()
                if not changed:
                    raise AppError("memory_reorganization_part_changed",status=409)
            await self.memory.record_batch_statistics_on(conn,batch_id,"failed")
            for episode in fixed["episodes"]:
                await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed',"
                    "extraction_started_at=NULL WHERE episode_id=%s",(episode["episode_id"],))
            pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                "AND active AND extraction_state='pending' LIMIT 1",(state["partition_id"],))).fetchone()
            due = datetime.now(UTC) if pending else None
            await conn.execute("UPDATE memory.consolidation_state SET last_error='closed_component_exceeds_budget',"
                "budget_failure=%s,wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                (Jsonb(failure),due,due,state["partition_id"]))
            if due:
                await self._schedule_on(conn,state["partition_id"],due,suffix="closed-component")

    def _budget_record(self):
        return {"input_tokens":self.policy.max_input_tokens,"output_tokens":self.policy.max_output_tokens,
                "reasoning_tokens":self.policy.reasoning_reserve_tokens,
                "generation_policy":"memory-native-low-json-v1","request_seconds":MODEL_REQUEST_SECONDS,
                "model_protocol":"memory.proposal.v2",
                "timing_policy":"admitted-execution-v1",
                "repair_input_policy":"bounded-diagnostic-headroom-v1"}

    def _input_budget_record(self):
        # These settings determine the smallest complete input we can prepare.
        # Scheduling changes alone do not make a rejected input fit its budget.
        return {**self._budget_record(),"input_layout":"consolidation-fitted-materials-v3",
            **{name:getattr(self.policy,name) for name in
            ("max_episodes","max_subjects","max_materials","max_old_records",
             "context_messages","max_source_characters","time_zone")}}

    def _candidate_diagnostic_reserve(self,request):
        payload=json.loads(request.messages[0].parts[0].text)
        extra=candidate_coverage_reserve(self.policy.max_old_records)
        payload["coverage"].update(extra)
        worst=(ModelMessage("user",(ModelPart("text",text=json.dumps(payload,ensure_ascii=False,separators=(",",":"))),
            *request.messages[0].parts[1:])),*request.messages[1:])
        base=max(request.estimated_input_tokens,
            repair_input_estimate(self.counter,request.system,request.messages,request.attempt_id))
        measured=max(self.counter.measure(request.system,worst,())[0],
            repair_input_estimate(self.counter,request.system,worst,request.attempt_id))-base
        # Codes are finite ASCII identifiers. This byte upper bound also covers
        # tokenizers that compress the synthetic repeated identifier unusually
        # well, plus the JSON-string escaping and token boundaries around it.
        bounded=len(json.dumps(json.dumps(extra,separators=(",",":"))).encode("utf-8"))+32
        return max(0,measured,bounded)

    def _budgeted_request(self,context,frozen,step_id,*,metrics=None,allow_required_context=True,reserve_candidates=False):
        """Keep whole old records when possible; explicit stubs can be expanded once."""
        candidates=list(reversed(frozen["records"]))
        while True:
            try:
                if metrics is None:
                    request=self._request(context,frozen,step_id)
                    reserve=self._candidate_diagnostic_reserve(request) if reserve_candidates else 0
                else:
                    metrics.count("trial_count")
                    with metrics.phase("fit_ms"):
                        request=self._request(context,frozen,step_id)
                        reserve=self._candidate_diagnostic_reserve(request) if reserve_candidates else 0
                required=max(request.estimated_input_tokens,
                    repair_input_estimate(self.counter,request.system,request.messages,request.attempt_id))+reserve
                if required>self.policy.max_input_tokens:
                    raise AppError("memory_consolidation_input_budget",details={"estimated":required,
                        "candidate_diagnostic_reserve":reserve,"new_characters":sum(
                            len(item["text"]) for item in frozen["materials"].values() if item["role"]=="new")})
                return request
            except AppError as exc:
                if exc.code != "memory_consolidation_input_budget":
                    raise
                if self._omit_context_message(frozen):
                    continue
                if candidates:
                    key=candidates.pop(0)
                    record=frozen["records"][key]
                    frozen["records"][key]={name:record[name] for name in
                        ("kind","id","revision","head_revision","current_status","subject_id","source_refs","scope_ids") if name in record}
                    frozen["records"][key]["content_omitted"]=True
                    frozen.setdefault("selection_coverage",{}).setdefault("record_content_omitted",[]).append(key)
                    continue
                if frozen["records"]:
                    self._omit_record(frozen)
                    continue
                if allow_required_context and self._omit_context_message(frozen,allow_required=True):
                    continue
                raise

    @staticmethod
    def _omit_record(frozen):
        """Candidate overhead cannot evict previously fitted new or quoted originals."""
        key=next(reversed(frozen["records"]))
        del frozen["records"][key]
        coverage=frozen.setdefault("selection_coverage",{})
        coverage.setdefault("record_omitted",[]).append(key)
        if key in coverage.get("record_content_omitted",[]):
            coverage["record_content_omitted"].remove(key)
        for record in frozen["records"].values():
            links=record.get("relations",())
            kept=[link for link in links if key not in (link.get("from_ref"),link.get("to_ref"))]
            if len(kept)<len(links):
                record["relations"]=kept
                coverage["record_relations_omitted"]=coverage.get("record_relations_omitted",0)+len(links)-len(kept)
        remaining={item.get("subject_id") for item in frozen["records"].values()}
        for subject in tuple(frozen.get("candidate_subject_ids",())):
            if subject not in remaining:
                frozen["subjects"].pop(subject,None)
                frozen["targets"].pop(subject,None)
                frozen["candidate_subject_ids"].remove(subject)
        refresh_read_set(frozen)

    @staticmethod
    def _omit_context_message(frozen, *, allow_required=False):
        """Drop an optional historical message whole, preserving all new input."""
        optional = [(key, item) for key, item in frozen["materials"].items()
                    if item["role"] != "new" and (allow_required or not item.get("context_required"))]
        if not optional:
            return False
        def group(key, item):
            if item.get("context_message_id") or item.get("message_id"):
                return ("message", item.get("context_message_id") or item["message_id"])
            refs = item.get("source_refs", ())
            if refs:
                first = refs[0]
                return ("event", (first.get("locator") or {}).get("event_id") or (first["source_id"], first["revision"]))
            return ("material", key)
        chosen = group(*optional[0])
        keys = [key for key, item in frozen["materials"].items() if group(key, item) == chosen]
        if any(frozen["materials"][key]["role"] == "new" or
                frozen["materials"][key].get("context_required") and not allow_required for key in keys):
            return False
        required = any(frozen["materials"][key].get("context_required") for key in keys)
        for key in keys:
            del frozen["materials"][key]
        frozen.setdefault("selection_coverage", {}).setdefault("context_omitted", []).append({
            "material_ids": keys, "reason": "input_budget", "referenced_context": required})
        return True

    async def _idle(self, partition_id, owner, generation):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE memory.consolidation_state SET first_pending_at=NULL,wake_due_at=NULL,last_error=NULL,budget_failure=NULL "
                "WHERE partition_id=%s AND lease_owner=%s AND active_batch_id IS NULL AND dirty_generation=%s",
                (partition_id, owner, generation))

    async def _execute(self, batch_id, context, *, owner=None):
        # Both additional paths have durable, independent limits: one original
        # context supplement and one repair of known output. Transient physical
        # attempts are independent and use the durable retry policy.
        while True:
            async with self.database.transaction() as conn:
                batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s", (batch_id,))).fetchone()
                if not batch or batch["status"] not in _OBSERVABLE:
                    return batch["commit_refs"] if batch else None
                await self._assert_batch_generation_on(conn, batch)
                if owner:
                    renewed=await (await conn.execute("UPDATE memory.consolidation_state "
                        "SET lease_until=now()+%s*interval '1 second' WHERE partition_id=%s AND lease_owner=%s "
                        "RETURNING partition_id",(LEASE_SECONDS,batch["partition_id"],owner))).fetchone()
                    if not renewed:
                        raise AppError("memory_consolidation_lease_changed",status=409)
                await conn.execute("UPDATE memory.consolidation_batches SET status='waiting_model',updated_at=now() "
                    "WHERE batch_id=%s AND status='prepared' AND NOT (manifest ? 'remote_wait_unresolved')", (batch_id,))
            manifest = batch["manifest"]
            if manifest.get("remote_wait_unresolved"):
                step_id = manifest["remote_wait_unresolved"].get("step_id")
                if not step_id:
                    raise AppError("memory_remote_wait_unresolved",status=503)
                _, original = await self.steps.recorded(step_id)
                outcome = await observe_original(self.steps.models,original)
                # A still-unconfirmed local reservation needs separate policy
                # reconciliation, even if the original receipt becomes visible.
                if outcome.receipt is None or manifest.get("recovery_pending"):
                    raise AppError("memory_remote_wait_unresolved",status=503)
                await self.waits.resolve_observed(batch,outcome.receipt,owner=owner)
                continue
            if manifest.get("recovery_pending"):
                await self._admit_prepared(batch, owner=owner)
                continue
            # New events may refresh the scene's authority while this older
            # batch waits. Each attempt keeps its own original upper bound.
            context = context_from_bound(manifest["authority_bound"])
            await self.identity.check_context(context)
            async with self.database.transaction(read_only=True) as conn:
                compilation = await (await conn.execute(
                    "SELECT * FROM memory.consolidation_compilations WHERE batch_id=%s AND selected",
                    (batch_id,))).fetchone()
            if compilation:
                frozen = (manifest["steps"][compilation["step_id"]] if compilation["step_id"]
                          else manifest["frozen"])
                return await self._apply(batch, context, frozen, compilation["proposal"],
                    owner=owner, step_id=compilation["step_id"])
            if not batch["step_ids"]:
                proposal = consolidation_example(manifest["frozen"])
                await self._freeze_compilation(batch, context, manifest["frozen"], proposal, [], owner=owner)
                return await self._apply(batch, context, manifest["frozen"], proposal,owner=owner)
            chosen = await self.retries.choose(batch, context, owner=owner)
            if chosen is None:
                return None
            step_id, observe_only = chosen
            if step_id not in manifest["steps"]:
                async with self.database.transaction(read_only=True) as conn:
                    batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s", (batch_id,))).fetchone()
                manifest = batch["manifest"]
            observed = await self.steps.submit_or_observe(step_id, batch["partition_id"],
                data_generation=manifest["data_generation"], observe_only=observe_only, owner=owner,
                validate_on=self._validate_submission_on)
            if observed is None:
                if batch["status"] == "quarantined":
                    await self.retries._postpone(batch,code="model_outcome_unconfirmed",owner=owner)
                return None
            result, recorded = observed
            await self._policy_outcome(batch_id,step_id,status="succeeded",
                observation_ref="models:"+recorded.attempt_id)
            await self.retries.succeeded(batch_id, step_id)
            frozen = manifest["steps"][step_id]
            context_attempted = False
            try:
                proposal, alias_map = self._parse(frozen, result, with_alias_map=True)
            except AppError as exc:
                await self.retries.reject(batch_id, step_id, exc.code)
                requests = (exc.details or {}).get("requests", ())
                if exc.code == "memory_consolidation_record_context_required" and requests:
                    context_attempted = True
                    if not manifest["context_round"] and await self._supplement(batch,context,frozen,recorded,requests,owner=owner):
                        continue
                    # A known dependency did not fit or was unavailable. Keep
                    # it explicitly unresolved instead of deleting its reads.
                    proposal = self._unresolved_context(frozen, requests)
                    alias_map = []
                elif await self._repair(batch,context,frozen,recorded,result,exc,owner=owner):
                    continue
                else:
                    raise
            if (proposal["needs_context"] and not context_attempted and not manifest["context_round"]
                    and await self._supplement(batch,context,frozen,recorded,proposal["needs_context"],owner=owner)):
                await self.retries.reject(batch_id, step_id, "memory_consolidation_context_requested")
                continue
            if proposal["needs_context"] and not context_attempted:
                # A necessary lookup was not available or has already been
                # attempted. Never turn that omission into a complete result.
                proposal = self._unresolved_context(frozen, proposal["needs_context"])
                alias_map = []
            # A quarantined result remains outside the active unique predicate,
            # including while its unchanged frozen proposal is being checked.
            try:
                await self._freeze_compilation(batch, context, frozen, proposal, alias_map,
                    owner=owner, step_id=step_id)
                return await self._apply(batch, context, frozen, proposal,owner=owner,step_id=step_id)
            except RecoveryWait:
                raise
            except AppError as exc:
                await self.retries.reject(batch_id, step_id, exc.code)
                if await self._repair(batch,context,frozen,recorded,result,exc,owner=owner):
                    continue
                raise

    async def _repair(self,batch,context,frozen,recorded,result,error,*,owner=None):
        if (not repairable(result,recorded,error.code)
                or len(batch["manifest"].get("output_repairs",())) >= REPAIR_LIMIT):
            return False
        step_id=batch["batch_id"]+":repair:1"
        try:
            request=repair_request(recorded,step_id,result,error,self.counter,self.policy.max_input_tokens)
        except AppError as exc:
            if exc.code == "memory_consolidation_repair_input_budget":
                return False
            raise
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            if not await self.sources.validate_persistence(conn,context,codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            await self.identity.check_context(context,connection=conn)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current=await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["status"] not in _OBSERVABLE or len(current["manifest"].get("output_repairs",())) >= REPAIR_LIMIT:
                return False
            if not await self._consume_recovery_on(conn, current, "repair"):
                return False
            if current.get("selected_step_id") not in {None, recorded.intent_id}:
                raise AppError("memory_consolidation_attempt_changed", status=409)
            if current.get("selected_step_id"):
                # A semantic apply rejection keeps its old compilation for
                # audit, but the correction must be allowed to select its own.
                await conn.execute("UPDATE memory.consolidation_compilations SET selected=false "
                    "WHERE batch_id=%s AND selected AND step_id=%s", (batch["batch_id"], recorded.intent_id))
            recovery = current["manifest"].get("recovery")
            manifest=deepcopy(current["manifest"])
            if recovery and self.memory.recovery_policy is not None:
                manifest["recovery_pending"] = {"kind":"continuation", "step_id":step_id,
                    "prior_attempt_id":recorded.attempt_id, "reason":"schema_correction"}
            manifest["steps"][step_id]=frozen_mapping(frozen)
            manifest["output_repairs"]=[{"step_id":step_id,"original_step_id":recorded.intent_id,
                "original_attempt_id":recorded.attempt_id,"error_code":error.code,
                "rejected_proposal_digest":current["proposal_digest"],
                "diagnostic":repair_diagnostic(error)}]
            await self.steps.prepare_on(conn,step_id,request)
            await self.retries.track_on(conn,batch["batch_id"],step_id,kind="repair",previous_step_id=recorded.intent_id)
            if not manifest.get("recovery_pending"):
                await self._track_policy_on(conn,batch["batch_id"],step_id,recovery)
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,step_ids=array_append(step_ids,%s),"
                "status=CASE WHEN status='quarantined' THEN status ELSE 'prepared' END,selected_step_id=NULL,proposal_digest=NULL,"
                "retry_due_at=NULL,error_code=NULL,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest),step_id,batch["batch_id"]))
        return True

    @staticmethod
    async def _consume_recovery_on(conn, batch, kind):
        root = batch["manifest"].get("reorganization", {}).get("root_batch_id")
        if not root:
            return True
        column = {"repair": "repair_remaining", "context": "context_remaining",
                  "retry": "retry_remaining"}[kind]
        row = await (await conn.execute(
            f"UPDATE memory.consolidation_reorganizations SET {column}={column}-1 "
            f"WHERE root_batch_id=%s AND {column}>0 RETURNING root_batch_id", (root,))).fetchone()
        return row is not None

    @staticmethod
    def _parse(frozen, result, *, with_alias_map=False):
        native = result.native_response or {}
        reasons = {native.get("finish_reason"),native.get("stop_reason"),native.get("status")}
        completion=getattr(result,"completion",None)
        if completion:
            reasons.update(completion.stop_reasons)
        if isinstance(native.get("incomplete_details"),dict):
            reasons.add(native["incomplete_details"].get("reason"))
        for choice in native.get("choices",()):
            if isinstance(choice,dict):
                reasons.add(choice.get("finish_reason"))
        normalized={value.casefold() for value in reasons if isinstance(value,str)}
        if normalized & {"length","max_tokens","max_output_tokens"}:
            raise AppError("memory_consolidation_output_truncated", status=502)
        if (completion and completion.state == "incomplete") or "incomplete" in normalized:
            raise AppError("memory_consolidation_output_incomplete",status=502,
                details={"stop_reasons":sorted(normalized)})
        try:
            proposal = decode_model_json("".join(part.text or "" for part in result.parts if part.kind == "text"))
        except (TypeError, ValueError) as exc:
            raise AppError("memory_consolidation_output_invalid", status=502,details={
                key:getattr(exc,field) for key,field in (("line","lineno"),("column","colno"),("position","pos"))
                if hasattr(exc,field)}) from exc
        if frozen.get("model_protocol") == MODEL_PROTOCOL:
            compiled, aliases = compile_proposal(frozen, proposal)
        else:
            compiled, aliases = parse_model_proposal(frozen, proposal), []
        return (compiled, aliases) if with_alias_map else compiled

    async def _freeze_compilation(self, batch, context, frozen, proposal, alias_map, *, owner=None, step_id=None):
        """Persist the only selected commit input before entering the write transaction."""
        digest = body_hash(proposal)
        frozen_digest = body_hash(frozen)
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            await self._assert_attempt_applicable_on(conn, batch, step_id)
            if not await self.sources.validate_persistence(conn, context, codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable", status=403)
            await self._lock_owned_partition(conn, batch["partition_id"], owner)
            current = await (await conn.execute(
                "SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch["batch_id"],))).fetchone()
            if not current or current["status"] not in _OBSERVABLE:
                raise AppError("memory_consolidation_batch_terminal", status=409)
            await self.identity.check_context(context, connection=conn)
            saved_frozen = current["manifest"]["steps"].get(step_id) if step_id else current["manifest"]["frozen"]
            if saved_frozen != frozen or current["dependency_refs"] != batch["dependency_refs"]:
                raise AppError("memory_consolidation_attempt_changed", status=409)
            selected = await (await conn.execute(
                "SELECT * FROM memory.consolidation_compilations WHERE batch_id=%s AND selected FOR UPDATE",
                (batch["batch_id"],))).fetchone()
            if selected:
                if (selected["step_id"] != step_id or selected["proposal_digest"] != digest
                        or selected["frozen_input_digest"] != frozen_digest):
                    raise AppError("memory_consolidation_proposal_changed", status=409)
                return
            if current.get("selected_step_id") not in {None, step_id}:
                raise AppError("memory_consolidation_attempt_changed", status=409)
            number = await (await conn.execute(
                "SELECT COALESCE(max(attempt),0)+1 AS next FROM memory.consolidation_compilations WHERE batch_id=%s",
                (batch["batch_id"],))).fetchone()
            await conn.execute(
                "INSERT INTO memory.consolidation_compilations(batch_id,attempt,step_id,compiler_version,schema_version,"
                "proposal,alias_map,source_snapshot,frozen_input_digest,proposal_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (batch["batch_id"], number["next"], step_id, COMPILER_VERSION, SCHEMA_VERSION,
                 Jsonb(wire(proposal)), Jsonb(wire(alias_map)), Jsonb(current["dependency_refs"]), frozen_digest, digest))
            await conn.execute("UPDATE memory.consolidation_batches SET status=CASE WHEN status='quarantined' "
                "THEN status ELSE 'ready' END,selected_step_id=%s,proposal_digest=%s,updated_at=now() WHERE batch_id=%s",
                (step_id,digest,batch["batch_id"]))

    async def recompile_recorded(self, context, batch_id, *, expected_digest):
        """Explicit compiler repair from the original result, without a new model call."""
        async with self.database.transaction(read_only=True) as conn:
            await self.identity.check_context(context, connection=conn)
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                (batch_id,))).fetchone()
            selected = await (await conn.execute("SELECT * FROM memory.consolidation_compilations "
                "WHERE batch_id=%s AND selected", (batch_id,))).fetchone()
        if not batch or batch["context_fingerprint"] != body_hash(context_record(context)):
            raise AppError("memory_consolidation_batch_unavailable", status=404)
        allowed_failure = batch["status"] == "failed" and batch["error_code"] in {
            "memory_consolidation_duplicate_create", "memory_consolidation_unknown_endpoint"}
        if batch["status"] not in _OBSERVABLE and not allowed_failure:
            raise AppError("memory_consolidation_batch_terminal", status=409)
        old_digest = selected["proposal_digest"] if selected else None
        if old_digest != expected_digest:
            raise AppError("memory_consolidation_proposal_changed", status=409)
        step_id = selected["step_id"] if selected else batch["step_ids"][-1] if batch["step_ids"] else None
        if not step_id:
            raise AppError("memory_consolidation_result_unavailable", status=409)
        _, request = await self.steps.recorded(step_id)
        outcome = await observe_original(self.steps.models, request)
        if outcome.result is None or outcome.status != "succeeded":
            raise outcome.exception(request.attempt_id)
        frozen = batch["manifest"]["steps"][step_id]
        proposal, aliases = self._parse(frozen, outcome.result, with_alias_map=True)
        if proposal["needs_context"]:
            raise AppError("memory_consolidation_context_required", status=409)
        digest = body_hash(proposal)
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            if not await self.sources.validate_persistence(conn, context, codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable", status=403)
            state = await self._lock_owned_partition(conn, batch["partition_id"], None)
            if state.get("lease_until") and state["lease_until"] > datetime.now(UTC):
                raise AppError("memory_consolidation_lease_changed", status=409)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch_id,))).fetchone()
            previous = await (await conn.execute("SELECT * FROM memory.consolidation_compilations "
                "WHERE batch_id=%s AND selected FOR UPDATE", (batch_id,))).fetchone()
            if ((previous["proposal_digest"] if previous else None) != expected_digest
                    or current["dependency_refs"] != batch["dependency_refs"]
                    or current["manifest"]["steps"].get(step_id) != frozen
                    or current["status"] not in _OBSERVABLE | ({"failed"} if allowed_failure else set())):
                raise AppError("memory_consolidation_proposal_changed", status=409)
            if state.get("active_batch_id") not in {None, batch_id}:
                raise AppError("memory_consolidation_attempt_changed", status=409)
            await self.identity.check_context(context, connection=conn)
            for episode in current["manifest"]["episodes"]:
                row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (episode["episode_id"],))).fetchone()
                if (not row or not row["active"] or row["revision"] != episode["revision"]
                        or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]):
                    raise AppError("memory_consolidation_episode_changed", status=409)
            await self.memory.claims.revisions.validate_read_set_on(conn, context, frozen)
            await conn.execute("UPDATE memory.consolidation_compilations SET selected=false WHERE batch_id=%s AND selected",
                (batch_id,))
            number = await (await conn.execute("SELECT COALESCE(max(attempt),0)+1 AS next "
                "FROM memory.consolidation_compilations WHERE batch_id=%s", (batch_id,))).fetchone()
            await conn.execute("INSERT INTO memory.consolidation_compilations(batch_id,attempt,step_id,compiler_version,"
                "schema_version,proposal,alias_map,source_snapshot,frozen_input_digest,proposal_digest) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (batch_id, number["next"], step_id,
                COMPILER_VERSION, SCHEMA_VERSION, Jsonb(wire(proposal)), Jsonb(wire(aliases)),
                Jsonb(current["dependency_refs"]), body_hash(frozen), digest))
            await conn.execute("UPDATE memory.consolidation_batches SET status='ready',selected_step_id=%s,"
                "proposal_digest=%s,error_code=NULL,updated_at=now() WHERE batch_id=%s", (step_id,digest,batch_id))
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=%s,wake_due_at=now() "
                "WHERE partition_id=%s", (batch_id,batch["partition_id"]))
            await self._schedule_on(conn,batch["partition_id"],datetime.now(UTC),suffix="recompile")
        return {"batch_id": batch_id, "attempt": number["next"], "step_id": step_id,
                "proposal_digest": digest, "compiler_version": COMPILER_VERSION}

    @staticmethod
    def _unresolved_context(frozen, requests):
        proposal = consolidation_example(frozen)
        proposal["needs_context"] = list(requests[:4])
        for target in proposal["target_results"]:
            if not target["processed_material_ids"]:
                continue
            target["status"] = "unresolved"
            target["reason"] = "所需旧记录正文无法在本批预算内完整补查"
            proposal["unresolved"].append({"issue_id":"context_"+str(len(proposal["unresolved"])+1),
                "subject_id":target["subject_id"], "material_ids":target["processed_material_ids"],
                "reason":"所需旧记录正文无法在本批预算内完整补查，未应用依赖该正文的候选修改。",
                "needed_refs":[item["ref"] for item in requests if item.get("ref")][:16]})
        return proposal

    async def _supplement(self, batch, context, frozen, recorded, requests, *, owner=None):
        # Reuse the original exact payload. New aliases append; no old alias is renumbered.
        payload = json.loads(recorded.messages[0].parts[0].text)
        expanded = deepcopy(frozen)
        for item in payload["materials"]:
            # Frozen media stays authoritative. Restoring the exact text does
            # not reconstruct or drop image parts and never renumbers aliases.
            expanded["materials"][item["id"]]["text"] = item["text"]
        for item in payload["existing_records"]:
            expanded["records"][item["ref"]].update({key:value for key,value in item.items() if key != "ref"})
        added = False
        outcomes = []
        for request in requests:
            kind, ref = request["kind"], request["ref"]
            outcome = {**request, "outcome":"unavailable"}
            if kind == "material":
                material = frozen["materials"][ref]
                original_ref = codec.refs(material["source_refs"])[0]
                parent = SourceRef(**material.get("original_ref",wire(original_ref)))
                start = (original_ref.locator or {}).get("end", 0)
                read = await self.sources.read(context, parent, {"position": start, "max_characters": 2000})
                if self._append_material(expanded, read, role="context", extra={
                        key:material[key] for key in ("occurred_at","context_message_id") if key in material}):
                    added = True
                    outcome["outcome"] = "provided"
            elif kind == "record":
                record=expanded["records"][ref]
                if record.get("content_omitted"):
                    expanded["records"][ref]=await self.materials.expand_record(context,record)
                    omitted=expanded.get("selection_coverage",{}).get("record_content_omitted",[])
                    if ref in omitted:
                        omitted.remove(ref)
                    added=True
                    outcome["outcome"]="provided"
                else:
                    outcome["outcome"] = "already_provided"
            elif kind == "conversation_range" and self.materials.conversation:
                values = await self.materials.conversation(context, cutoff=frozen["context"]["message_cutoff"],
                    start=request["start"], end=request["end"], limit=self.policy.context_messages or 1)
                for message in values["items"]:
                    for source in codec.refs(message["source_refs"]):
                        read = await self.sources.read(context, source, {"max_characters":1200})
                        if self._append_material(expanded, read, role="context", extra={
                                "context_message_id":message.get("message_id"),
                                "occurred_at":wire(message.get("occurred_at")),
                                "conversation_id":context.conversation_id}):
                            added = True
                            outcome["outcome"] = "provided"
            elif kind == "identity":
                async with self.database.transaction(read_only=True) as conn:
                    people = await self.identity.resolve_memory_subjects_on(conn,context,(),aliases=(request["query"],),include_group=False)
                for person in people:
                    subject_id = person["subject_id"]
                    if subject_id not in expanded["subjects"]:
                        expanded["subjects"][subject_id] = {"subject_id":subject_id,"name":person["display_name"],
                            "kind":person["kind"],"aliases":list(person["aliases"]),"revision":person["revision"]}
                        expanded["targets"][subject_id] = {"new_material_ids":[]}
                        added = True
                        outcome["outcome"] = "provided"
            outcomes.append(outcome)
        if not added:
            return False
        expanded["records"]=expand_record_endpoints(expanded["records"])
        expanded["read_set"]=[{"kind":record["kind"],"id":record["id"],"revision":record["revision"],
                              "head_revision":record.get("head_revision") or record["revision"]}
            for record in expanded["records"].values()]
        authors={item["speaker_id"] for item in expanded["materials"].values() if item["speaker_id"]} - set(expanded["subjects"])
        if authors:
            async with self.database.transaction(read_only=True) as conn:
                subjects=await self.identity.resolve_memory_subjects_on(conn,context,authors)
            for subject in subjects:
                subject_id=subject["subject_id"]
                expanded["subjects"][subject_id]={"subject_id":subject_id,"name":subject["display_name"],
                    "kind":subject["kind"],"aliases":list(subject["aliases"]),"revision":subject["revision"]}
                expanded["targets"].setdefault(subject_id,{"new_material_ids":[]})
        freeze_dependency_closure(expanded)
        step_id = batch["batch_id"]+":step:1"
        try:
            request = self._request(context,expanded,step_id,context_round=1,requests=outcomes)
            request = replace(request,data_generation=batch["manifest"]["data_generation"])
            request = await native_request(self.steps.models,request)
        except AppError as exc:
            if exc.code == "memory_consolidation_input_budget":
                return False
            raise
        dependencies = self.materials.dependencies(expanded)
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            if not await self.sources.validate_persistence(conn,context,dependencies):
                raise AppError("source_unavailable",status=403)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["manifest"]["context_round"]:
                return True
            if not await self._consume_recovery_on(conn, current, "context"):
                return False
            recovery = current["manifest"].get("recovery")
            manifest = deepcopy(current["manifest"])
            if recovery and self.memory.recovery_policy is not None:
                manifest["recovery_pending"] = {"kind":"continuation", "step_id":step_id,
                    "prior_attempt_id":recorded.attempt_id, "reason":"need_context"}
            manifest["steps"][step_id] = frozen_mapping(expanded)
            manifest["context_round"] = 1
            await self.steps.prepare_on(conn,step_id,request)
            await self.retries.track_on(conn,batch["batch_id"],step_id,kind="context",previous_step_id=recorded.intent_id)
            if not manifest.get("recovery_pending"):
                await self._track_policy_on(conn,batch["batch_id"],step_id,recovery)
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,step_ids=array_append(step_ids,%s),dependency_refs=%s,read_set=%s,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest),step_id,Jsonb(wire(dependencies)),Jsonb(wire(expanded["read_set"])),batch["batch_id"]))
        return True

    @staticmethod
    def _append_material(frozen, read, *, role, extra=None):
        values={key:value for key,value in (extra or {}).items() if value is not None}
        if not read["speaker_id"] and read["origin_kind"]=="actor":
            values["speaker_id"]="actor:"+frozen["context"]["actor_id"]
        # Human image authorship defaults to the source's verified uploader in
        # append_context_material; a None override would erase that identity.
        return append_context_material(frozen["materials"],read,extra=values)

    async def _lock_owned_partition(self,conn,partition_id,owner):
        state=await (await conn.execute("SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
            (partition_id,))).fetchone()
        if owner is not None and (not state or state["lease_owner"] != owner):
            raise AppError("memory_consolidation_lease_changed",status=409)
        return state

    async def _apply(self, batch, context, frozen, proposal, *, owner=None, step_id=None):
        digest = body_hash(proposal)
        async with self.database.transaction() as conn:
            await self._assert_batch_generation_on(conn, batch)
            await self._assert_attempt_applicable_on(conn, batch, step_id)
            # This order matches Sources withdrawal: sources -> partition -> batch -> stable domain objects.
            if not await self.sources.validate_persistence(conn,context,codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["status"] == "applied":
                return current["commit_refs"]
            if current["status"] not in _OBSERVABLE:
                raise AppError("memory_consolidation_batch_terminal",status=409)
            compilation = await (await conn.execute("SELECT * FROM memory.consolidation_compilations "
                "WHERE batch_id=%s AND selected FOR UPDATE", (batch["batch_id"],))).fetchone()
            if compilation and (compilation["step_id"] != step_id or compilation["proposal_digest"] != digest
                    or compilation["frozen_input_digest"] != body_hash(frozen)
                    or compilation["source_snapshot"] != current["dependency_refs"]):
                raise AppError("memory_consolidation_proposal_changed",status=409)
            if step_id is not None and not compilation:
                raise AppError("memory_consolidation_compilation_missing", status=409)
            if step_id is not None:
                if (step_id not in current["step_ids"] or current["manifest"]["steps"].get(step_id) != frozen
                        or current.get("selected_step_id") not in {None,step_id}):
                    raise AppError("memory_consolidation_attempt_changed",status=409)
                selected = await (await conn.execute("UPDATE memory.consolidation_batches SET selected_step_id=%s "
                    "WHERE batch_id=%s AND (selected_step_id IS NULL OR selected_step_id=%s) RETURNING batch_id",
                    (step_id,batch["batch_id"],step_id))).fetchone()
                if not selected:
                    raise AppError("memory_consolidation_attempt_changed",status=409)
            if current["proposal_digest"] is not None and current["proposal_digest"] != digest:
                raise AppError("memory_consolidation_proposal_changed",status=409)
            if current["context_fingerprint"] != body_hash(context_record(context)):
                raise AppError("memory_consolidation_authority_changed",status=409)
            await self.identity.check_context(context,connection=conn)
            for episode in current["manifest"]["episodes"]:
                row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(episode["episode_id"],))).fetchone()
                if not row or not row["active"] or row["revision"] != episode["revision"] or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]:
                    raise AppError("memory_consolidation_episode_changed",status=409)
            result = await self.memory.claims.revisions.apply_operations_on(conn,context,batch_id=batch["batch_id"],
                frozen=frozen,operations=proposal["operations"])
            if result.get("derived_invalidations"):
                from tianshu.sources.dependencies import unique_refs
                new_sources=unique_refs(*(codec.refs(item["source_refs"]) for item in frozen["materials"].values() if item["role"]=="new"))
                await self.memory.domain_changed_on(conn,context,result["derived_invalidations"],source_refs=new_sources,
                    affected_subject_ids=result.get("affected_subject_ids",()))
            unresolved = {item["subject_id"] for item in proposal["unresolved"]}
            coverage = []
            for episode in current["manifest"]["episodes"]:
                missing = set(episode["missing"])
                if any((item["status"] == "unresolved" or item["subject_id"] in unresolved)
                        and set(item["processed_material_ids"]) & set(episode["material_ids"])
                        for item in proposal["target_results"]) or None in unresolved or proposal["needs_context"]:
                    missing.add("semantic_unresolved")
                selection = frozen.get("selection_coverage", {})
                if selection.get("required_context_omitted") or any(item.get("referenced_context")
                        for item in selection.get("context_omitted",())):
                    missing.add("referenced_context_incomplete")
                state = "pending" if not episode["complete"] else "completed_with_missing" if missing else "completed"
                await conn.execute("UPDATE memory.episodes SET extraction_cursor=%s,extraction_state=%s,extraction_missing=%s,extraction_started_at=NULL WHERE episode_id=%s AND sequence=%s",
                    (Jsonb(episode["after"]),state,sorted(missing),episode["episode_id"],episode["sequence"]))
                coverage.append({"episode_id":episode["episode_id"],"revision":episode["revision"],"sequence":episode["sequence"],
                    "cursor":episode["after"],"state":state,"missing":sorted(missing)})
            result = {**result,"coverage":coverage,"target_results":proposal["target_results"],"unresolved":proposal["unresolved"],
                "context_unfulfilled":proposal["needs_context"],
                "candidate_selection":frozen.get("selection_coverage", {}),"batch_id":batch["batch_id"]}
            await conn.execute("UPDATE memory.consolidation_batches SET status='applied',proposal_digest=%s,commit_refs=%s,committed_at=now(),updated_at=now(),"
                "retry_due_at=NULL,retry_requested=false,error_code=NULL WHERE batch_id=%s",
                (digest,Jsonb(wire(result)),batch["batch_id"]))
            await self.memory.record_batch_statistics_on(conn,batch["batch_id"],"applied")
            await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='applied' "
                "WHERE child_batch_id=%s AND state='active'", (batch["batch_id"],))
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error=NULL,budget_failure=NULL,updated_at=now() "
                "WHERE partition_id=%s AND active_batch_id=%s",(batch["partition_id"],batch["batch_id"]))
            pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                "AND active AND extraction_state='pending' LIMIT 1",(batch["partition_id"],))).fetchone()
            if pending:
                due = datetime.now(UTC)
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=COALESCE(first_pending_at,now()) WHERE partition_id=%s",(due,batch["partition_id"]))
                await self._schedule_on(conn,batch["partition_id"],due)
            else:
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,first_pending_at=NULL WHERE partition_id=%s",(batch["partition_id"],))
        return result

    async def _failure(self, partition_id, batch_id, exc, *, owner=None, generation=None):
        code = failure_code(exc)
        if isinstance(exc, RecoveryWait):
            if exc.reason == "recovery_claim_lost" or not recovery_wait_retryable(exc):
                raise exc
            try:
                await self.waits.defer(partition_id, batch_id, exc, owner=owner, generation=generation)
            except AppError as error:
                if error.code not in {"source_unavailable", "memory_consolidation_episode_changed"}:
                    raise
                await self._failure(partition_id, batch_id, error, owner=owner, generation=generation)
            return
        if code in {"memory_consolidation_lease_changed", "job_generation_expired", "data_generation_expired"}:
            # A successor may already be observing/applying this same batch.
            # The expired writer has no authority to settle or reset its work.
            return
        details = getattr(exc,"details",None) or {}
        if batch_id and details.get("status") in {"unknown","failed","cancelled"}:
            async with self.database.transaction(read_only=True) as conn:
                attempt = await (await conn.execute("SELECT step_id FROM memory.consolidation_attempts "
                    "WHERE batch_id=%s ORDER BY number DESC LIMIT 1", (batch_id,))).fetchone()
            if attempt:
                await self._policy_outcome(batch_id,attempt["step_id"],status=details["status"],
                    code=details.get("receipt_error_code") or code,
                    observation_ref="models:"+str(details.get("attempt_id") or "unknown"),
                    retry_after=details.get("retry_after"))
        if batch_id and (transient_model_failure(code) or details.get("status") in {"queued","running","succeeded"}):
            if await self.retries.defer(batch_id,exc,owner=owner):
                return
        revoked = code in {"source_unavailable","access_changed","delivery_access_changed","actor_forbidden","conversation_forbidden"}
        stale = code in {"revision_conflict","memory_consolidation_read_changed","memory_consolidation_episode_changed",
            "memory_read_set_changed","memory_read_revision_conflict","claim_revision_conflict","matter_revision_conflict",
            "memory_record_unavailable","model_route_binding_changed","model_route_revision_conflict"}
        # All retryable physical outcomes were delegated above. Unclassified
        # errors stay terminal rather than creating another replay policy here.
        terminal = batch_id is not None or not (transient_model_failure(code) or code in PREPARATION_DEPENDENCY_WAIT)
        shrink = False
        async with self.database.transaction() as conn:
            if batch_id:
                batch_snapshot = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches "
                    "WHERE batch_id=%s",(batch_id,))).fetchone()
                if not batch_snapshot:
                    return
                try:
                    await self._assert_batch_generation_on(conn,batch_snapshot)
                except AppError as error:
                    if error.code == "job_generation_expired":
                        return
                    raise
            ownership = await (await conn.execute("SELECT lease_owner FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",(partition_id,))).fetchone()
            if owner is not None and (not ownership or ownership["lease_owner"] != owner):
                return
            if batch_id:
                batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch_id,))).fetchone()
                if batch and batch["status"] in _OBSERVABLE:
                    if revoked or stale or code == "explicit_cancel":
                        await self.cancel_recovery_on(conn,batch["manifest"])
                    split = closed_episode_split(batch_frozen(batch)) if code == "memory_consolidation_output_truncated" else None
                    status = "revoked" if revoked else "stale" if stale else "superseded" if split else "failed"
                    manifest=deepcopy(batch["manifest"])
                    recorded_code = code
                    if code == "memory_consolidation_output_truncated" and not split:
                        recorded_code = "closed_component_exceeds_budget"
                        manifest["closed_component_failure"] = {"original_error_code":code,
                            "reason":recorded_code,"dependency_closure":batch_frozen(batch).get("dependency_closure"),
                            "stage":"model_output"}
                    if split:
                        inherited = manifest.get("reorganization", {})
                        root = inherited.get("root_batch_id") or batch_id
                        if not inherited:
                            counts = await (await conn.execute("SELECT count(*) FILTER (WHERE kind='retry') AS retries "
                                "FROM memory.consolidation_attempts WHERE batch_id=%s", (batch_id,))).fetchone()
                            await conn.execute("INSERT INTO memory.consolidation_reorganizations(root_batch_id,partition_id,"
                                "context_remaining,repair_remaining,retry_remaining) VALUES(%s,%s,%s,%s,%s)",
                                (root,partition_id,max(0,1-int(bool(manifest.get("context_round")))),
                                 max(0,1-len(manifest.get("output_repairs",()))),max(0,1-counts["retries"])))
                        manifest["superseded_by_reorganization"] = {"root_batch_id":root,"parts":len(split),"reason":code}
                        await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='superseded' "
                            "WHERE child_batch_id=%s AND state='active'", (batch_id,))
                        for ordinal, episodes in enumerate(split, 1):
                            await conn.execute("INSERT INTO memory.consolidation_reorganization_parts "
                                "(parent_batch_id,root_batch_id,ordinal,episodes) VALUES(%s,%s,%s,%s)",
                                (batch_id,root,ordinal,Jsonb(wire(episodes))))
                    else:
                        await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='failed' "
                            "WHERE child_batch_id=%s AND state='active'", (batch_id,))
                    await conn.execute("UPDATE memory.consolidation_batches SET status=%s,error_code=%s,manifest=%s,retry_due_at=NULL,retry_requested=false,updated_at=now() WHERE batch_id=%s",(status,recorded_code,Jsonb(manifest),batch_id))
                    code = recorded_code
                    if status == "failed":
                        await self.memory.record_batch_statistics_on(conn,batch_id,"failed")
                    if terminal:
                        shrink = bool(split)
                        for episode in batch["manifest"]["episodes"]:
                            # A newer batch may legitimately revise the old
                            # read set while this computation waits. Rebuild
                            # from the still-unconsumed original episodes.
                            state = "pending" if stale or shrink else "consolidation_failed"
                            await conn.execute("UPDATE memory.episodes SET extraction_state=%s,extraction_started_at=NULL "
                                "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active "
                                "AND extraction_cursor IS NOT DISTINCT FROM %s",
                                (state,episode["episode_id"],episode["revision"],episode["sequence"],Jsonb(episode["before"])))
                        await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL "
                            "WHERE partition_id=%s AND active_batch_id=%s",(partition_id,batch_id))
            budget_failure=None
            if not batch_id and code == "memory_consolidation_input_budget":
                budget_failure={"configuration":self._input_budget_record(),
                    "required_tokens":details.get("estimated"),"configured_tokens":self.policy.max_input_tokens}
            await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,budget_failure=%s,updated_at=now() "
                "WHERE partition_id=%s",(code,Jsonb(budget_failure) if budget_failure else None,partition_id))
            if not batch_id and terminal:
                part = await (await conn.execute("SELECT p.* FROM memory.consolidation_reorganization_parts p "
                    "JOIN memory.consolidation_reorganizations r USING(root_batch_id) WHERE r.partition_id=%s "
                    "AND p.state='pending' ORDER BY p.created_at,p.ordinal LIMIT 1 FOR UPDATE OF p", (partition_id,))).fetchone()
                if part:
                    await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='failed' "
                        "WHERE parent_batch_id=%s AND ordinal=%s", (part["parent_batch_id"],part["ordinal"]))
                    for episode in part["episodes"]:
                        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed',extraction_started_at=NULL "
                            "WHERE episode_id=%s AND revision=%s AND sequence=%s AND extraction_state='pending'",
                            (episode["episode_id"],episode["revision"],episode["sequence"]))
                    remaining = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                        "AND active AND extraction_state='pending' LIMIT 1", (partition_id,))).fetchone()
                    due = datetime.now(UTC) if remaining else None
                    await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                        (due,due,partition_id))
                    if due:
                        await self._schedule_on(conn,partition_id,due,suffix="reorganization-failed")
                elif details.get("episode_id"):
                    # Only the original that actually failed is isolated; a
                    # source error is not permission to cancel its neighbors.
                    await conn.execute("UPDATE memory.episodes SET extraction_state=%s,extraction_started_at=NULL "
                        "WHERE episode_id=%s AND revision=%s AND sequence=%s AND extraction_state='pending'",
                        ("consolidation_revoked" if revoked else "consolidation_failed", details["episode_id"],
                         details.get("episode_revision"), details.get("episode_sequence")))
                    remaining = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                        "AND active AND extraction_state='pending' LIMIT 1", (partition_id,))).fetchone()
                    due = datetime.now(UTC) if remaining else None
                    await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                        (due,due,partition_id))
                    if due:
                        await self._schedule_on(conn,partition_id,due,suffix="input-failed")
                else:
                    await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,first_pending_at=NULL WHERE partition_id=%s",(partition_id,))
            elif not batch_id and (transient_model_failure(code) or code in PREPARATION_DEPENDENCY_WAIT):
                due=datetime.now(UTC)+timedelta(seconds=30)
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s WHERE partition_id=%s",(due,partition_id))
                await self._schedule_on(conn,partition_id,due,suffix="prepare-retry")
            if batch_id and terminal:
                pending=await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                    "AND active AND extraction_state='pending' LIMIT 1",(partition_id,))).fetchone()
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                    (datetime.now(UTC) if pending else None,datetime.now(UTC) if pending else None,partition_id))
            else:
                pending=False
            if shrink or pending:
                due=datetime.now(UTC)
                await self._schedule_on(conn,partition_id,due,suffix="stale:"+(batch_id or "prepare"))
