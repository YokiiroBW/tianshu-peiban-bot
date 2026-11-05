"""Durable, stage-preserving waits for the existing consolidation worker."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from . import codec
from .authority import context_record, request_identity
from .extraction_contract import SCHEMA_VERSION
from .materials import frozen_mapping
from .recovery_wait import read_observation, read_observation_marker, wait_marker


ACTIVE = {"prepared", "waiting_model", "ready"}


def batch_snapshot(batch):
    return body_hash({key: batch.get(key) for key in ("batch_id", "partition_id", "status", "error_code", "manifest",
        "dependency_refs", "read_set", "context_fingerprint", "step_ids", "selected_step_id", "proposal_digest",
        "commit_refs", "retry_due_at")})


class ConsolidationWait:
    def __init__(self, consolidation):
        self.consolidation = consolidation
        self.database = consolidation.database

    async def _wake_pending_on(self, conn, partition_id, *, suffix):
        pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
            "AND active AND extraction_state='pending' LIMIT 1", (partition_id,))).fetchone()
        due = datetime.now(UTC) if pending else None
        await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s,updated_at=now() "
            "WHERE partition_id=%s", (due,due,partition_id))
        if due:
            await self.consolidation._schedule_on(conn,partition_id,due,suffix=suffix)

    async def mark_unresolved(self, partition_id, batch_id, wait, *, owner, generation):
        """Fence only the original work; safely isolated batches yield their slot."""
        async with self.database.transaction() as conn:
            await self.consolidation.jobs.assert_generation_on(conn,generation)
            preparation = getattr(wait,"memory_preparation",None) or {}
            local_safe = getattr(wait,"transaction_rollback_confirmed",None) is not False
            frozen = preparation.get("frozen")
            dependencies, source_valid = (), None
            if not batch_id and local_safe and frozen and preparation.get("context"):
                dependencies = self.consolidation.materials.dependencies(frozen)
                if preparation.get("stage") == "route_profile":
                    # Only the route stage retains a text-bearing draft. Lock
                    # all its actual sources before persisting that request.
                    source_valid = await self.consolidation.sources.validate_persistence(
                        conn,preparation["context"],dependencies)
            state = await (await conn.execute("SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (partition_id,))).fetchone()
            if not state or owner is not None and state["lease_owner"] != owner:
                return False
            if not batch_id:
                return await self._mark_preparation_on(conn,state,wait,generation=generation,
                    dependencies=dependencies,source_valid=source_valid)
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch_id,))).fetchone()
            if (not batch or batch["partition_id"] != partition_id or batch["status"] not in ACTIVE | {"quarantined"}
                    or batch["manifest"].get("data_generation") != generation
                    or batch["status"] in ACTIVE and state["active_batch_id"] != batch_id):
                return False
            manifest = deepcopy(batch["manifest"])
            local_rollback = getattr(wait,"transaction_rollback_confirmed",None)
            remote_rollback = getattr(wait,"remote_rollback_confirmed",None)
            safe_local = local_rollback is not False
            can_isolate = remote_rollback is False and safe_local
            marker = manifest.get("remote_wait_unresolved")
            if not marker:
                marker = {"reason":wait.reason, "retry_at":wire(wait.retry_at), "dependency":wait.dependency,
                    "remote_rollback_confirmed":remote_rollback, "transaction_rollback_confirmed":local_rollback,
                    "step_id":batch["step_ids"][-1] if batch["step_ids"] else None,
                    "stage":getattr(wait,"memory_wait_stage",None), "resume_status":batch["status"], "isolation":"local_transaction_unknown" if not safe_local else "remote"}
                manifest["remote_wait_unresolved"] = marker
            elif getattr(wait,"memory_wait_stage",None) == "route_profile":
                marker["reobservation_count"] = int(marker.get("reobservation_count",0)) + 1
                marker["last_reobservation"] = {"reason":wait.reason,"retry_at":wire(wait.retry_at),
                    "remote_rollback_confirmed":remote_rollback,"transaction_rollback_confirmed":local_rollback}
            if can_isolate and batch["status"] in ACTIVE:
                # Do not touch changed/revoked episodes; the frozen versions
                # remain in the batch for later source/read-set/CAS checks.
                for episode in sorted(manifest.get("episodes",()),key=lambda item:item["episode_id"]):
                    before = Jsonb(episode["before"]) if episode.get("before") is not None else None
                    await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_unknown',"
                        "extraction_started_at=NULL WHERE episode_id=%s AND revision=%s AND sequence=%s AND active "
                        "AND extraction_cursor IS NOT DISTINCT FROM %s AND extraction_state='running'",
                        (episode["episode_id"],episode["revision"],episode["sequence"],before))
                await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',manifest=%s,"
                    "retry_due_at=NULL,updated_at=now() WHERE batch_id=%s", (Jsonb(manifest),batch_id))
                await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error=NULL "
                    "WHERE partition_id=%s AND active_batch_id=%s", (partition_id,batch_id))
                await self._wake_pending_on(conn,partition_id,suffix="after-unresolved-batch")
            else:
                await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,updated_at=now() WHERE batch_id=%s",
                    (Jsonb(manifest),batch_id))
                if state["active_batch_id"] == batch_id:
                    await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,updated_at=now() WHERE partition_id=%s",
                        (partition_id,))
            if (can_isolate and marker.get("stage") == "route_profile" and manifest.get("profile_pending")):
                due = max(wait.retry_at,datetime.now(UTC)+timedelta(seconds=5))
                await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=%s WHERE batch_id=%s",(due,batch_id))
                await self.consolidation._schedule_on(conn,partition_id,due,suffix="profile-reobserve",target_batch_id=batch_id)
            return True

    async def _mark_preparation_on(self, conn, state, wait, *, generation, dependencies, source_valid):
        """Reobserve proven reads or hold only the input whose effect is uncertain."""
        preparation = getattr(wait,"memory_preparation",None) or {}
        stage = preparation.get("stage", "unknown")
        local_safe = getattr(wait,"transaction_rollback_confirmed",None) is not False
        remote_unknown = getattr(wait,"remote_rollback_confirmed",None) is False
        if local_safe and remote_unknown and stage in {"selection", "material_read"}:
            due = max(wait.retry_at,datetime.now(UTC)+timedelta(seconds=5))
            marker = read_observation_marker(wait,read_observation(state.get("last_error")))
            await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,wake_due_at=%s,updated_at=now() "
                "WHERE partition_id=%s", (marker,due,state["partition_id"]))
            await self.consolidation._schedule_on(conn,state["partition_id"],due,suffix="preparation-read-observation")
            return True
        frozen = preparation.get("frozen")
        batch_id = preparation.get("batch_id")
        context = preparation.get("context")
        bound = preparation.get("authority_bound")
        if local_safe and remote_unknown and batch_id and frozen and context and bound:
            return await self._hold_preparation_on(conn,state,wait,preparation,dependencies,source_valid)
        # A failed local commit cannot be treated as a rollback. Inspect the
        # intended batch, selected compilation and original cursors first, but
        # keep the active pointer and evidence until a conclusive reconciliation.
        current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
            (batch_id,))).fetchone() if batch_id else None
        selected = await (await conn.execute("SELECT 1 FROM memory.consolidation_compilations WHERE batch_id=%s AND selected",
            (batch_id,))).fetchone() if current else None
        episodes = (frozen or {}).get("episodes",())
        cursor_matches = True if episodes else None
        for item in episodes:
            row = await (await conn.execute("SELECT revision,sequence,extraction_cursor FROM memory.episodes WHERE episode_id=%s",
                (item["episode_id"],))).fetchone()
            cursor_matches = cursor_matches and bool(row and row["revision"] == item["revision"]
                and row["sequence"] == item["sequence"] and row["extraction_cursor"] == item["before"])
        evidence = {"preparation_stage":stage,"batch_id":batch_id,"batch_status":current["status"] if current else None,
            "selected_compilation":bool(selected),"original_cursors_match":cursor_matches}
        await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,wake_due_at=NULL,updated_at=now() "
            "WHERE partition_id=%s", (wait_marker(wait,extra=evidence),state["partition_id"]))
        return True

    async def _hold_preparation_on(self, conn, state, wait, preparation, dependencies, source_valid):
        batch_id, frozen, context = preparation["batch_id"],preparation["frozen"],preparation["context"]
        stage, part = preparation["stage"],preparation.get("part")
        existing = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
            (batch_id,))).fetchone()
        if existing or state["active_batch_id"]:
            # A concurrent/uncertain local commit must be reconciled, not
            # overwritten with an artificial hold.
            await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,wake_due_at=NULL WHERE partition_id=%s",
                (wait_marker(wait,extra={"preparation_stage":stage,"batch_id":batch_id,
                    "batch_status":existing["status"] if existing else None}),state["partition_id"]))
            return True
        can_reobserve = stage == "route_profile" and source_valid and preparation.get("request") is not None
        recovery = None
        prior = None
        if can_reobserve and part:
            root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                (part["root_batch_id"],))).fetchone()
            if not root or root["manifest"].get("data_generation") != preparation["data_generation"]:
                can_reobserve = False
            else:
                recovery = root["manifest"].get("recovery")
                if not recovery and self.consolidation.memory.recovery_policy is not None:
                    can_reobserve = False
                if recovery and self.consolidation.memory.recovery_policy is not None:
                    prior = await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts "
                        "WHERE recovery_lineage_id=%s AND recovery_policy_state='succeeded' "
                        "ORDER BY created_at DESC,step_id DESC LIMIT 1",(recovery["lineage_id"],))).fetchone()
                    if not prior:
                        can_reobserve = False
        marker = {"reason":wait.reason,"retry_at":wire(wait.retry_at),"dependency":wait.dependency,
            "remote_rollback_confirmed":getattr(wait,"remote_rollback_confirmed",None),
            "transaction_rollback_confirmed":getattr(wait,"transaction_rollback_confirmed",None),
            "stage":"route_profile" if can_reobserve else "preparation", "preparation_stage":stage,
            "step_id":batch_id+":step:0" if can_reobserve else None,"resume_status":"preparation"}
        manifest = {"episodes":deepcopy(frozen["episodes"]),"authority_bound":preparation["authority_bound"],
            "data_generation":preparation["data_generation"],"generation":state["dirty_generation"],
            "context_round":0,"steps":{},"remote_wait_unresolved":marker,
            "preparation_hold":{"stage":stage,"closure_complete":stage == "route_profile", "source_valid":source_valid}}
        previous = read_observation(state.get("last_error"))
        if previous:
            manifest["preparation_observation"] = previous
        step_ids = []
        if part:
            manifest["reorganization"] = {"root_batch_id":part["root_batch_id"],
                "parent_batch_id":part["parent_batch_id"],"ordinal":part["ordinal"]}
        if can_reobserve:
            step_id = marker["step_id"]
            manifest.update(frozen=frozen_mapping(frozen),source_budget=preparation.get("source_budget"),profile_pending=True)
            manifest["steps"][step_id] = frozen_mapping(frozen)
            if recovery:
                manifest["recovery"] = recovery
                if prior:
                    manifest["recovery_pending"] = {"kind":"continuation","prior_attempt_id":prior["model_attempt_id"],
                        "reason":"capacity_repartition"}
            elif not part and self.consolidation.memory.recovery_policy is not None:
                manifest["recovery_pending"] = {"kind":"original"}
            await self.consolidation.steps.prepare_on(conn,step_id,preparation["request"])
            step_ids = [step_id]
        await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,"
            "read_set,dependency_refs,context_fingerprint,step_ids,error_code) VALUES(%s,%s,%s,'quarantined',%s,%s,%s,%s,%s,%s)",
            (batch_id,state["partition_id"],SCHEMA_VERSION,Jsonb(wire(manifest)),Jsonb(wire(frozen.get("read_set",[]))),
             Jsonb(wire(dependencies)),body_hash(context_record(context)),step_ids,"memory_preparation_unresolved"))
        if part:
            reserved = await (await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='active',"
                "child_batch_id=%s WHERE parent_batch_id=%s AND ordinal=%s AND state='pending' RETURNING parent_batch_id",
                (batch_id,part["parent_batch_id"],part["ordinal"]))).fetchone()
            if not reserved:
                raise AppError("memory_reorganization_part_changed",status=409)
        if can_reobserve:
            await self.consolidation.retries.track_on(conn,batch_id,step_ids[0],kind="original")
        for episode in sorted(frozen["episodes"],key=lambda item:item["episode_id"]):
            before = Jsonb(episode["before"]) if episode.get("before") is not None else None
            await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_unknown',extraction_started_at=NULL "
                "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active AND extraction_state='pending' "
                "AND extraction_cursor IS NOT DISTINCT FROM %s",(episode["episode_id"],episode["revision"],episode["sequence"],before))
        await conn.execute("UPDATE memory.consolidation_state SET last_error=NULL WHERE partition_id=%s",(state["partition_id"],))
        await self._wake_pending_on(conn,state["partition_id"],suffix="after-unresolved-preparation")
        if can_reobserve:
            due = max(wait.retry_at,datetime.now(UTC)+timedelta(seconds=5))
            await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=%s WHERE batch_id=%s",(due,batch_id))
            await self.consolidation._schedule_on(conn,state["partition_id"],due,
                suffix="profile-reobserve",target_batch_id=batch_id)
        return True

    async def resolve_observed(self, snapshot, receipt, *, owner):
        """Only a validated original receipt can remove the no-admission marker."""
        from .consolidation import context_from_bound
        context = context_from_bound(snapshot["manifest"]["authority_bound"])
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn,snapshot)
            if not await self.consolidation.sources.validate_persistence(conn,context,codec.refs(snapshot["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            state = await self.consolidation._lock_owned_partition(conn,snapshot["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (snapshot["batch_id"],))).fetchone()
            if (not current or batch_snapshot(current) != batch_snapshot(snapshot)
                    or current["status"] in ACTIVE and state["active_batch_id"] != current["batch_id"]):
                raise AppError("memory_consolidation_batch_changed",status=409)
            manifest = deepcopy(current["manifest"])
            marker = manifest.get("remote_wait_unresolved") or {}
            step_id = marker.get("step_id")
            if not step_id or not current["step_ids"] or current["step_ids"][-1] != step_id:
                raise AppError("memory_remote_wait_unresolved",status=503)
            step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s FOR UPDATE",
                (step_id,))).fetchone()
            if (not step or step["state"] == "source_revoked" or step["request"].get("attempt_id") != receipt.attempt_id
                    or step["request"].get("intent_id") != receipt.intent_id
                    or receipt.data_generation != manifest.get("data_generation")):
                raise AppError("memory_consolidation_attempt_changed",status=409)
            await self.consolidation._validate_submission_on(conn,current,context,step_id)
            manifest["last_unresolved_wait"] = {**manifest.pop("remote_wait_unresolved"),
                "observed_status":receipt.status, "observed_at":wire(datetime.now(UTC))}
            # Even after a successful control read, this original batch may
            # only consume the original attempt, never allocate a successor.
            manifest["original_observation_only"] = True
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest),current["batch_id"]))
            await conn.execute("UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() WHERE step_id=%s",
                (receipt.status,receipt.error_code,step_id))
            await conn.execute("UPDATE memory.consolidation_attempts SET status=%s,error_code=%s,updated_at=now() WHERE step_id=%s",
                (receipt.status,receipt.error_code,step_id))

    async def defer(self, partition_id, batch_id, wait, *, owner, generation=None):
        """Commit the stage, pointer release and wake together; never settle a model attempt."""
        if wait.reason == "recovery_claim_lost":
            # A stale policy claimant cannot write an owner transition. A fresh
            # ordinary recovery scan must acquire its own claim first.
            return False
        snapshot = None
        if batch_id:
            async with self.database.transaction(read_only=True) as conn:
                snapshot = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                    (batch_id,))).fetchone()
            if not snapshot:
                return False
        due = wait.retry_at
        async with self.database.transaction() as conn:
            if generation is not None:
                await self.consolidation.jobs.assert_generation_on(conn,generation)
            if snapshot:
                from .consolidation import context_from_bound
                await self.consolidation._assert_batch_generation_on(conn, snapshot)
                context = context_from_bound(snapshot["manifest"]["authority_bound"])
                if not await self.consolidation.sources.validate_persistence(conn, context, codec.refs(snapshot["dependency_refs"])):
                    raise AppError("source_unavailable", status=403)
                await self.consolidation.identity.check_context(context, connection=conn)
            state = await (await conn.execute("SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (partition_id,))).fetchone()
            if not state or owner is not None and state["lease_owner"] != owner:
                return False
            if not snapshot:
                await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,wake_due_at=%s,updated_at=now() "
                    "WHERE partition_id=%s", (wait.code, due, partition_id))
                await self.consolidation._schedule_on(conn, partition_id, due, suffix="prepare-wait")
                return True
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch_id,))).fetchone()
            if not current or batch_snapshot(current) != batch_snapshot(snapshot):
                return False
            if current["status"] not in ACTIVE | {"quarantined"}:
                return False
            if current["status"] in ACTIVE and state["active_batch_id"] != batch_id:
                return False
            manifest = deepcopy(current["manifest"])
            previous = manifest.get("local_wait") or {}
            resume_status = previous.get("resume_status", current["status"])
            if current["status"] == "quarantined" and current.get("retry_due_at"):
                due = max(due, current["retry_due_at"])
            manifest["local_wait"] = {"reason":wait.reason, "code":wait.code, "retry_at":wire(due),
                "dependency":wait.dependency, "resume_status":resume_status}
            await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',manifest=%s,"
                "error_code=%s,retry_due_at=%s,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest), wait.code, due, batch_id))
            if resume_status in ACTIVE:
                for episode in manifest["episodes"]:
                    before = Jsonb(episode["before"]) if episode.get("before") is not None else None
                    updated = await (await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_waiting',"
                        "extraction_started_at=NULL WHERE episode_id=%s AND revision=%s AND sequence=%s AND active "
                        "AND extraction_cursor IS NOT DISTINCT FROM %s AND extraction_state='running' RETURNING episode_id",
                        (episode["episode_id"],episode["revision"],episode["sequence"],before))).fetchone()
                    if not updated:
                        raise AppError("memory_consolidation_episode_changed", status=409)
                await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error=%s,updated_at=now() "
                    "WHERE partition_id=%s AND active_batch_id=%s", (wait.code,partition_id,batch_id))
            await self.consolidation._schedule_on(conn,partition_id,due,suffix="local-wait",target_batch_id=batch_id)
            pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                "AND active AND extraction_state='pending' LIMIT 1", (partition_id,))).fetchone()
            if pending and state["active_batch_id"] in {None,batch_id}:
                now = datetime.now(UTC)
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,"
                    "first_pending_at=COALESCE(first_pending_at,%s) WHERE partition_id=%s", (now,now,partition_id))
                await self.consolidation._schedule_on(conn,partition_id,now,suffix="after-local-wait")
            elif not pending and state["active_batch_id"] in {None,batch_id}:
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,first_pending_at=NULL "
                    "WHERE partition_id=%s", (partition_id,))
        return True

    async def reopen_failed_on(self, conn, batch, context, request_id):
        """Reopen only a previously misclassified dependency wait, with its original ledger."""
        if (batch["status"] != "failed" or batch.get("error_code") not in {
                "recovery_dependency_cooling", "recovery_dependency_probe_in_progress", "recovery_claim_in_progress"}):
            return False
        manifest = deepcopy(batch["manifest"])
        recovery = manifest.get("recovery")
        if not recovery or not batch["step_ids"] or not manifest.get("frozen"):
            raise AppError("memory_original_batch_unavailable",status=409)
        step_id = batch["step_ids"][-1]
        attempt = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s "
            "AND step_id=%s", (batch["batch_id"],step_id))).fetchone()
        step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s",
            (step_id,))).fetchone()
        lineage = await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (recovery["lineage_id"],))).fetchone()
        ledger = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s",
            (attempt["model_attempt_id"],))).fetchone() if attempt else None
        root_id = manifest.get("reorganization", {}).get("root_batch_id") or batch["batch_id"]
        root = await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
            (root_id,))).fetchone()
        if (not attempt or not step or not lineage or not ledger or not root
                or body_hash(root["manifest"].get("frozen")) != lineage["material_digest"]
                or attempt.get("recovery_lineage_id") != recovery["lineage_id"]
                or ledger["lineage_id"] != recovery["lineage_id"] or ledger["version"] != recovery["version"]
                or ledger["request_digest"] != body_hash(request_identity(step["request"]))
                or ledger["attempt_number"] != lineage["attempts"]
                or ledger["status"] not in {"reserved", "unknown", "failed", "succeeded"}
                or attempt.get("recovery_policy_state") != ledger["status"]
                or step["request"].get("attempt_id") != attempt["model_attempt_id"]
                or step["request"].get("data_generation") != manifest.get("data_generation")
                or lineage["owner"] != "memory" or lineage["owner"] != recovery["owner"]
                or lineage["domain"] != "memory" or lineage["subject_id"] != context.actor_id
                or lineage["current_version"] != recovery["version"]
                or lineage["data_generation"] != manifest.get("data_generation") or lineage["paused"]
                or lineage["cancelled"] or lineage.get("source_withdrawn")):
            raise AppError("memory_original_batch_changed",status=409)
        for episode in sorted(manifest["episodes"],key=lambda item:item["episode_id"]):
            row = await (await conn.execute("SELECT revision,sequence,extraction_cursor,content_hash,active,extraction_state "
                "FROM memory.episodes WHERE episode_id=%s FOR UPDATE", (episode["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"] != episode["revision"]
                    or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]
                    or row["extraction_state"] != "consolidation_failed"
                    or episode.get("content_digest") is not None and row["content_hash"] != episode["content_digest"]):
                raise AppError("memory_consolidation_episode_changed",status=409)
        previous = {"status":batch["status"], "error_code":batch["error_code"], "request_id":request_id}
        manifest["local_wait"] = {"reason":batch["error_code"], "code":batch["error_code"],
            "retry_at":wire(datetime.now(UTC)), "dependency":lineage["dependency"],
            "resume_status":"ready" if batch.get("selected_step_id") else "waiting_model",
            "recovered_from":previous}
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined',manifest=%s,"
            "retry_due_at=now(),retry_requested=false,updated_at=now() WHERE batch_id=%s",
            (Jsonb(manifest),batch["batch_id"]))
        for episode in manifest["episodes"]:
            await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_waiting',"
                "extraction_started_at=NULL WHERE episode_id=%s AND revision=%s AND sequence=%s",
                (episode["episode_id"],episode["revision"],episode["sequence"]))
        await self.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
            suffix="restore-original:"+request_id,target_batch_id=batch["batch_id"])
        return True

    async def reactivate(self, batch_id, *, owner):
        """Reattach a due batch only if its authority and frozen episodes still match."""
        async with self.database.transaction(read_only=True) as conn:
            snapshot = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                (batch_id,))).fetchone()
        if not snapshot or not snapshot["manifest"].get("local_wait"):
            return None
        from .consolidation import context_from_bound
        context = context_from_bound(snapshot["manifest"]["authority_bound"])
        async with self.database.transaction() as conn:
            await self.consolidation._assert_batch_generation_on(conn,snapshot)
            if not await self.consolidation.sources.validate_persistence(conn,context,codec.refs(snapshot["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            await self.consolidation.identity.check_context(context,connection=conn)
            state = await self.consolidation._lock_owned_partition(conn,snapshot["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch_id,))).fetchone()
            if not current or batch_snapshot(current) != batch_snapshot(snapshot):
                return "blocked"
            wait = current["manifest"].get("local_wait")
            if not wait or current["status"] != "quarantined" or current.get("retry_due_at") and current["retry_due_at"] > datetime.now(UTC):
                return "blocked"
            resume_status = wait.get("resume_status")
            if resume_status not in ACTIVE | {"quarantined"}:
                raise AppError("memory_consolidation_wait_invalid",status=409)
            if resume_status in ACTIVE and state["active_batch_id"] not in {None,batch_id}:
                return "blocked"
            for episode in sorted(current["manifest"]["episodes"], key=lambda item:item["episode_id"]):
                row = await (await conn.execute("SELECT revision,sequence,extraction_cursor,content_hash,active,extraction_state "
                    "FROM memory.episodes WHERE episode_id=%s FOR UPDATE", (episode["episode_id"],))).fetchone()
                if (not row or not row["active"] or row["revision"] != episode["revision"] or
                        row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"] or
                        resume_status in ACTIVE and row["extraction_state"] != "consolidation_waiting" or
                        episode.get("content_digest") is not None and row["content_hash"] != episode["content_digest"]):
                    raise AppError("memory_consolidation_episode_changed",status=409)
            manifest = deepcopy(current["manifest"])
            manifest["last_local_wait"] = manifest.pop("local_wait")
            manifest["local_wait_count"] = int(manifest.get("local_wait_count", 0)) + 1
            await conn.execute("UPDATE memory.consolidation_batches SET status=%s,manifest=%s,error_code=NULL,"
                "retry_due_at=CASE WHEN %s='quarantined' THEN retry_due_at ELSE NULL END,updated_at=now() WHERE batch_id=%s",
                (resume_status,Jsonb(manifest),resume_status,batch_id))
            if resume_status in ACTIVE:
                await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=%s,last_error=NULL "
                    "WHERE partition_id=%s AND (active_batch_id IS NULL OR active_batch_id=%s)",
                    (batch_id,snapshot["partition_id"],batch_id))
                for episode in manifest["episodes"]:
                    await conn.execute("UPDATE memory.episodes SET extraction_state='running',extraction_started_at=now() "
                        "WHERE episode_id=%s AND revision=%s AND sequence=%s AND extraction_state='consolidation_waiting'",
                        (episode["episode_id"],episode["revision"],episode["sequence"]))
                return "active"
            return "quarantined"
