"""Durable, stage-preserving waits for the existing consolidation worker."""

from copy import deepcopy
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from . import codec
from .authority import request_identity
from .recovery_wait import wait_marker


ACTIVE = {"prepared", "waiting_model", "ready"}


def batch_snapshot(batch):
    return body_hash({key: batch.get(key) for key in ("batch_id", "partition_id", "status", "error_code", "manifest",
        "dependency_refs", "read_set", "context_fingerprint", "step_ids", "selected_step_id", "proposal_digest",
        "commit_refs", "retry_due_at")})


class ConsolidationWait:
    def __init__(self, consolidation):
        self.consolidation = consolidation
        self.database = consolidation.database

    async def mark_unresolved(self, partition_id, batch_id, wait, *, owner, generation):
        """Retain negative rollback evidence without settling the original work."""
        async with self.database.transaction() as conn:
            await self.consolidation.jobs.assert_generation_on(conn,generation)
            state = await (await conn.execute("SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (partition_id,))).fetchone()
            if not state or owner is not None and state["lease_owner"] != owner:
                return False
            if not batch_id:
                await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,"
                    "wake_due_at=NULL,updated_at=now() WHERE partition_id=%s", (wait_marker(wait),partition_id))
                return True
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch_id,))).fetchone()
            if (not batch or batch["partition_id"] != partition_id or batch["status"] not in ACTIVE | {"quarantined"}
                    or batch["manifest"].get("data_generation") != generation
                    or batch["status"] in ACTIVE and state["active_batch_id"] != batch_id):
                return False
            manifest = deepcopy(batch["manifest"])
            if not manifest.get("remote_wait_unresolved"):
                manifest["remote_wait_unresolved"] = {
                    "reason":wait.reason, "retry_at":wire(wait.retry_at), "dependency":wait.dependency,
                    "remote_rollback_confirmed":getattr(wait,"remote_rollback_confirmed",None),
                    "transaction_rollback_confirmed":getattr(wait,"transaction_rollback_confirmed",None),
                    "step_id":batch["step_ids"][-1] if batch["step_ids"] else None,
                }
                await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,updated_at=now() WHERE batch_id=%s",
                    (Jsonb(manifest),batch_id))
                if state["active_batch_id"] == batch_id:
                    await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,updated_at=now() WHERE partition_id=%s",
                        (partition_id,))
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
            manifest["last_unresolved_wait"] = {**manifest.pop("remote_wait_unresolved"),
                "observed_status":receipt.status, "observed_at":wire(datetime.now(UTC))}
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
