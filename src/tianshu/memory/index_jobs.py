"""Durable, source-checked vector computations on the existing Memory index lane."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
from uuid import NAMESPACE_URL, uuid4, uuid5

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.types.json import Jsonb

from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.models.codec import request_from_wire
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.sources.dependencies import unique_refs
from tianshu.types import RequestBudget, ServiceClass
from . import codec
from .candidate_sources import check_rows_on
from .domain_utils import lexical
from .embedding import normalized_input
from .evidence import build_evidence_unit
from .indexing import index_text
from .index_recovery import IndexRecovery, MAX_ATTEMPTS


TABLES = {"claim": ("claim_revisions", "claim_id"), "hypothesis": ("claim_revisions", "claim_id"),
          "episode": ("episodes", "episode_id"), "matter": ("matters", "matter_id")}
ACTIVE = ("pending", "running", "observing", "retry_wait")
LEASE_SECONDS = 90
OBSERVE_SECONDS = 30
SCAN_LIMIT = 24
LOG = logging.getLogger(__name__)


class IndexJobs:
    def __init__(self, workers):
        self.workers = workers
        self.memory, self.database = workers.memory, workers.database
        self.recovery = IndexRecovery(self)

    def bind_observability(self, *, statistics=None, recovery_policy=None, guard_on=None):
        self.recovery.bind(statistics=statistics, recovery_policy=recovery_policy, guard_on=guard_on)

    async def recovery_inspect(self, recovery, target):
        return await self.recovery.inspect(recovery, target)

    async def context_for_recovery(self, kind, object_id, revision):
        return await self.recovery.context_for_recovery(kind, object_id, revision)

    async def recovery_invoke(self, recovery, target, facts, attempt_id):
        return await self.recovery.invoke(recovery, target, facts, attempt_id)

    async def recovery_observe(self, recovery, target, facts, attempt_id):
        return await self.recovery.observe(recovery, target, facts, attempt_id)

    async def recovery_candidates(self, limit=32):
        return await self.recovery.candidates(limit=limit)

    async def backfill_statistics(self, generation, *, limit=64):
        return await self.recovery.backfill(generation, limit=limit)

    async def _load_on(self, conn, kind, object_id, revision, *, lock=False):
        if kind not in TABLES:
            raise AppError("memory_index_kind_invalid")
        table, key = TABLES[kind]
        if kind in {"claim", "hypothesis"}:
            query = ("SELECT r.*,c.actor_id,c.principal_id FROM memory.claims c JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s AND r.revision=%s "
                "AND r.state='active' AND r.current_status='current'")
            if lock:
                query += " FOR UPDATE OF c,r"
        else:
            query = f"SELECT * FROM memory.{table} WHERE {key}=%s AND revision=%s AND active"
            if lock:
                query += " FOR UPDATE"
        row = await (await conn.execute(query, (object_id, revision))).fetchone()
        if not row:
            return None
        context = await self.memory.identity.context_on(conn, row["principal_id"], actor_id=row["actor_id"])
        selected = ((await self.memory.claims.selected_groups(conn, context, (row,)))[0]
            if kind in {"claim", "hypothesis", "matter"} else codec.refs(row["source_refs"]))
        if not selected:
            raise AppError("source_unavailable", status=403)
        supplements = codec.refs(row.get("supplement_refs")) if kind == "episode" else ()
        refs = unique_refs(selected, supplements)
        proof = {"_supports": [refs], "_derivations": (), "_explicit": (), "_relation_proofs": None}
        if (await check_rows_on(self.memory.sources, conn, context, (proof,)))[0] is not True:
            raise AppError("source_unavailable", status=403)
        value = (codec.claim(row, selected) if kind in {"claim", "hypothesis"} else
            codec.matter(row, selected_refs=selected) if kind == "matter" else getattr(codec, kind)(row))
        unit = build_evidence_unit(value)
        if supplements:
            unit = replace(unit, source_refs=unique_refs(unit.source_refs, supplements))
        proof["_supports"] = [unit.source_refs]
        if (await check_rows_on(self.memory.sources, conn, context, (proof,)))[0] is not True:
            raise AppError("source_unavailable", status=403)
        return row, context, unit.source_refs, index_text(unit)

    async def _ensure_on(self, conn, kind, object_id, revision, profile, fingerprint, generation, content_hash, *, error=None):
        await self.memory.jobs.assert_generation_on(conn, generation)
        if profile is not None:
            await conn.execute("SELECT memory.ensure_embedding_space(%s)", (fingerprint,))
        index_id = "memory_index_" + body_hash((generation, kind, object_id, revision, fingerprint, content_hash))[:48]
        await conn.execute("INSERT INTO memory.index_jobs(index_id,generation,kind,object_id,revision,profile_key,content_hash,profile,state,next_due,last_error) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NULL ELSE now() END,%s) ON CONFLICT DO NOTHING",
            (index_id, generation, kind, object_id, revision, fingerprint, content_hash, Jsonb(profile) if profile else None,
             "failed" if error else "pending", bool(error), error))
        job = await (await conn.execute("SELECT *,lease_until>now() AS leased FROM memory.index_jobs "
            "WHERE index_id=%s FOR UPDATE", (index_id,))).fetchone()
        if job["state"] == "succeeded" and profile is not None:
            present = await (await conn.execute("SELECT 1 FROM memory.search_vectors WHERE profile_key=%s AND generation=%s "
                "AND kind=%s AND object_id=%s AND revision=%s AND content_hash=%s",
                (fingerprint, generation, kind, object_id, revision, content_hash))).fetchone()
            if not present:
                # Rehydrate from the same successful Models receipt. No new
                # physical attempt or recovery budget is invented.
                job = await (await conn.execute("UPDATE memory.index_jobs SET state='observing',next_due=now(),"
                    "last_error='memory_index_projection_missing',updated_at=now() WHERE index_id=%s "
                    "AND state='succeeded' RETURNING *,false AS leased", (index_id,))).fetchone() or job
        return job

    async def _schedule_on(self, conn, job):
        if job["state"] not in ACTIVE or job["next_due"] is None:
            return
        await self.memory.jobs.enqueue(conn, JobSpec("memory.index_retry", "memory-index-wake:" + str(uuid4()),
            {"index_id": job["index_id"], "generation": job["generation"]}, ServiceClass.ELASTIC,
            scheduled_at=job["next_due"], data_generation=job["generation"]),
            queueing_lock="memory-index:" + job["index_id"])

    async def _material(self, kind, object_id, revision, generation):
        # Keyword visibility never depends on a supplier succeeding. Both queue
        # deliveries and consistency recovery use this same canonical document.
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, generation)
            loaded = await self._load_on(conn, kind, object_id, revision)
            if loaded is None:
                return None
            _, context, refs, content = loaded
            if not await self.memory.sources.validate_persistence(conn, context, refs):
                raise AppError("source_unavailable", status=403)
            fresh = await self._load_on(conn, kind, object_id, revision, lock=True)
            if fresh is None or fresh[2:] != loaded[2:]:
                return None
            table, key = TABLES[kind]
            await conn.execute(f"UPDATE memory.{table} SET lexical=%s WHERE {key}=%s AND revision=%s",
                               (lexical(content), object_id, revision))
            return fresh

    async def index_object(self, kind, object_id, revision, generation):
        try:
            loaded = await self._material(kind, object_id, revision, generation)
            if loaded is None:
                return
        except AppError as error:
            if error.status in {403, 404}:
                return
            raise
        if kind in {"claim", "hypothesis"}:
            kind = "hypothesis" if loaded[0]["memory_kind"] == "hypothesis" else "claim"
        content_hash = body_hash(normalized_input(loaded[3]))
        try:
            profile, fingerprint = await self.workers.embeddings.profile()
        except AppError as error:
            async with self.database.transaction() as conn:
                await self._ensure_on(conn, kind, object_id, revision, None, "unavailable", generation, content_hash, error=error.code)
            return
        async with self.database.transaction() as conn:
            job = await self._ensure_on(conn, kind, object_id, revision, profile, fingerprint, generation, content_hash)
        return await self.run(job["index_id"], generation=generation)

    async def _attempt_on(self, conn, job, number):
        await self.memory.jobs.assert_generation_on(conn, job["generation"])
        intent = f"memory-index:{job['generation']}:{job['kind']}:{job['object_id']}:{job['revision']}:{job['content_hash']}"
        if number > 1:
            intent += f":retry:{number}"
        step_id = intent + ":" + job["profile_key"]
        attempt_id = str(uuid5(NAMESPACE_URL, step_id))
        await conn.execute("INSERT INTO memory.index_attempts(index_id,number,intent_id,step_id,model_attempt_id) "
            "VALUES(%s,%s,%s,%s,%s)", (job["index_id"], number, intent, step_id, attempt_id))
        await conn.execute("UPDATE memory.index_jobs SET attempt_count=%s WHERE index_id=%s", (number, job["index_id"]))
        return {"number": number, "intent_id": intent, "step_id": step_id, "model_attempt_id": attempt_id,
                "status": "prepared", "error_code": None, "event_revision": 1,
                "recovery_reserved": False, "recovery_probe_token": None}

    async def run(self, index_id, completed_attempt_id=None, generation=None):
        if not isinstance(generation, str) or not generation:
            raise AppError("memory_index_generation_required", status=409)
        owner = str(uuid4())
        async with self.database.transaction() as conn:
            job = await (await conn.execute("SELECT *,lease_until>now() AS leased,next_due>now() AS deferred "
                "FROM memory.index_jobs WHERE index_id=%s FOR UPDATE", (index_id,))).fetchone()
            await self.memory.jobs.assert_generation_on(conn, generation)
            if not job or job["state"] not in ACTIVE:
                return
            if job["generation"] != generation:
                raise AppError("memory_index_generation_changed", status=409)
            if job["leased"]:
                return
            attempt = await (await conn.execute("SELECT * FROM memory.index_attempts WHERE index_id=%s AND number=%s",
                (index_id, job["attempt_count"]))).fetchone()
            notified = attempt and completed_attempt_id == attempt["model_attempt_id"]
            if job["deferred"] and not notified:
                await self._schedule_on(conn, job)
                return
            await conn.execute("UPDATE memory.index_jobs SET state='running',lease_owner=%s,"
                "lease_until=now()+%s*interval '1 second',updated_at=now() WHERE index_id=%s", (owner, LEASE_SECONDS, index_id))
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._keep_lease(index_id, owner, asyncio.current_task(), lost, generation))
        try:
            await self._execute(job, attempt, owner, allow_retry=job["state"] == "retry_wait")
        except asyncio.CancelledError:
            if not lost.is_set():
                await self._finish(index_id, owner, "observing", "index_worker_interrupted", delay=OBSERVE_SECONDS)
                raise
            worker = asyncio.current_task()
            worker.uncancel()
            if worker.cancelling():
                raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            async with self.database.transaction() as conn:
                await self.memory.jobs.assert_generation_on(conn, generation)
                released = await (await conn.execute("UPDATE memory.index_jobs SET lease_owner=NULL,lease_until=NULL "
                    "WHERE index_id=%s AND lease_owner=%s RETURNING *", (index_id, owner))).fetchone()
                if released:
                    await self._schedule_on(conn, released)

    async def _keep_lease(self, index_id, owner, worker, lost, generation):
        try:
            while True:
                await asyncio.sleep(LEASE_SECONDS / 3)
                async with self.database.transaction() as conn:
                    await self.memory.jobs.assert_generation_on(conn, generation)
                    renewed = await (await conn.execute("UPDATE memory.index_jobs SET lease_until=now()+%s*interval '1 second' "
                        "WHERE index_id=%s AND lease_owner=%s AND lease_until>now() RETURNING index_id",
                        (LEASE_SECONDS, index_id, owner))).fetchone()
                if not renewed:
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        lost.set()
        worker.cancel()

    async def _finish(self, index_id, owner, state, error=None, *, delay=None):
        async with self.database.transaction() as conn:
            row = await (await conn.execute("SELECT generation FROM memory.index_jobs WHERE index_id=%s AND lease_owner=%s",
                                             (index_id, owner))).fetchone()
            if row is None:
                return
            await self.memory.jobs.assert_generation_on(conn, row["generation"])
            await conn.execute("UPDATE memory.index_jobs SET state=%s,last_error=%s,"
                "next_due=now()+%s::double precision*interval '1 second',updated_at=now() "
                "WHERE index_id=%s AND lease_owner=%s AND lease_until>now()", (state, error, delay, index_id, owner))

    async def _execute(self, job, attempt, owner, *, allow_retry):
        try:
            loaded = await self._material(job["kind"], job["object_id"], job["revision"], job["generation"])
            if loaded is None:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_revision_obsolete")
            row, context, refs, content = loaded
            if body_hash(normalized_input(content)) != job["content_hash"]:
                # The same episode revision can lose a supplement in place.
                # Its original successful attempt remains immutable; the
                # consistency scan will create a job for the new material.
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_material_changed")
            job["actor_id"] = context.actor_id
            current_profile = await self.workers.embeddings.profile()
            if current_profile[1] != job["profile_key"]:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_profile_changed")
            if job.get("profile") != current_profile[0]:
                async with self.database.transaction() as conn:
                    await self.memory.jobs.assert_generation_on(conn, job["generation"])
                    frozen = (await (await conn.execute("SELECT 1 FROM memory.model_steps WHERE step_id=%s",
                        (attempt["step_id"],))).fetchone()) if attempt else None
                    if frozen is None:
                        owned = await (await conn.execute("UPDATE memory.index_jobs SET profile=%s WHERE index_id=%s "
                            "AND lease_owner=%s AND lease_until>now() AND state='running' RETURNING index_id",
                            (Jsonb(current_profile[0]), job["index_id"], owner))).fetchone()
                        if not owned:
                            return
                        job["profile"] = current_profile[0]
            if attempt is None and await self._reuse(job, owner, context, refs, content):
                return
            if attempt is None:
                async with self.database.transaction() as conn:
                    await self.memory.jobs.assert_generation_on(conn, job["generation"])
                    owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                        "AND lease_owner=%s AND lease_until>now() AND attempt_count=0 FOR UPDATE",
                        (job["index_id"], owner))).fetchone()
                    if not owned:
                        return
                    attempt = await self._attempt_on(conn, job, 1)
            result = await self._submit_or_observe(job, attempt, owner, context, refs, content)
            if result is None:
                return
            try:
                async with self.database.transaction() as conn:
                    await self.memory.jobs.assert_generation_on(conn, job["generation"])
                    owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                        "AND lease_owner=%s AND lease_until>now() AND state='running' AND attempt_count=%s FOR UPDATE",
                        (job["index_id"], owner, attempt["number"]))).fetchone()
                    if not owned:
                        return
                    if attempt.get("status") != "succeeded":
                        await self.recovery.success_on(conn, attempt)
                        settled = await (await conn.execute("UPDATE memory.index_attempts SET status='succeeded',error_code=NULL,"
                            "event_revision=event_revision+1,updated_at=now() WHERE index_id=%s AND number=%s RETURNING *",
                            (job["index_id"], attempt["number"]))).fetchone()
                        attempt.update(settled)
            except Exception:
                LOG.exception("memory index physical attempt settlement failed")
                return await self._finish(job["index_id"], owner, "observing", "memory_index_settlement_unavailable",
                                          delay=OBSERVE_SECONDS)
            try:
                values, fingerprint = self.workers.embeddings.decode(result, job["profile"], job["profile_key"])
            except AppError as error:
                return await self._finish(job["index_id"], owner, "failed", error.code)
        except Exception as error:
            return await self._failure(job, attempt, owner, error, allow_retry=allow_retry)
        # Control reads precede the write transaction. The fingerprint checked
        # here is the same configured profile used to select this logical job.
        try:
            if (await self.workers.embeddings.profile())[1] != fingerprint:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_profile_changed")
            async with self.database.transaction() as conn:
                await self.memory.jobs.assert_generation_on(conn, job["generation"])
                if not await self.memory.sources.validate_persistence(conn, context, refs):
                    return await self._finish_on(conn, job, owner, "revoked", "source_unavailable")
                fresh = await self._load_on(conn, job["kind"], job["object_id"], job["revision"], lock=True)
                if fresh is None or fresh[3] != content or fresh[2] != refs:
                    return await self._finish_on(conn, job, owner, "obsolete", "memory_index_revision_obsolete")
                owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                    "AND lease_owner=%s AND lease_until>now() AND state='running' AND attempt_count=%s FOR UPDATE",
                    (job["index_id"], owner, attempt["number"]))).fetchone()
                if not owned:
                    return
                await register_vector_async(conn)
                await self.memory.jobs.assert_generation_on(conn, job["generation"])
                await conn.execute("INSERT INTO memory.search_vectors(profile_key,generation,kind,object_id,revision,content_hash,embedding) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(profile_key,generation,kind,object_id,revision) DO UPDATE "
                    "SET content_hash=EXCLUDED.content_hash,embedding=EXCLUDED.embedding,created_at=now()",
                    (fingerprint, job["generation"], job["kind"], job["object_id"], job["revision"],
                     body_hash(normalized_input(content)), Vector(values)))
                await self._finish_on(conn, job, owner, "succeeded")
                await self.recovery.record_attempt_on(conn, {**job, "state": "succeeded"}, attempt, actor_id=context.actor_id)
        except AppError as error:
            await self._finish(job["index_id"], owner, "revoked" if error.status in {403, 404} else "failed", error.code)

    async def _submit_or_observe(self, job, attempt, owner, context, refs, content):
        """Persist the original request, submit once by attempt identity, then yield."""
        embeddings, models, steps = self.workers.embeddings, self.workers.embeddings.models, self.workers.embeddings.steps
        request = embeddings.request(context, content, RequestBudget(datetime.now(UTC) + timedelta(seconds=120)),
            intent=attempt["intent_id"], profile=job["profile"], fingerprint=job["profile_key"], source_refs=refs,
            data_generation=job["generation"])
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, job["generation"])
            owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                "AND lease_owner=%s AND lease_until>now() AND state='running' AND attempt_count=%s FOR UPDATE",
                (job["index_id"], owner, attempt["number"]))).fetchone()
            if not owned:
                return None
            frozen = await steps.prepare_on(conn, attempt["step_id"], request)
            original = request_from_wire(frozen["request"])
            if original.attempt_id != attempt["model_attempt_id"]:
                raise AppError("memory_model_step_identity_conflict", status=409)
            if not await self.recovery.reserve_on(conn, job, attempt, original, content):
                await conn.execute("UPDATE memory.index_jobs SET state='retry_wait',next_due=now()+interval '30 seconds',"
                    "last_error='memory_index_recovery_wait',updated_at=now() WHERE index_id=%s AND lease_owner=%s",
                    (job["index_id"], owner))
                return None
        try:
            receipt = await models.status(original.attempt_id)
        except Exception as error:
            if isinstance(error, AppError) and error.status in {401, 403}:
                raise
            await self._finish(job["index_id"], owner, "observing", "model_status_unavailable", delay=OBSERVE_SECONDS)
            return None
        if receipt is None:
            async with self.database.transaction() as conn:
                await self.memory.jobs.assert_generation_on(conn, job["generation"])
                if not await self.memory.sources.validate_persistence(conn, context, refs):
                    raise AppError("source_unavailable", status=403)
                owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                    "AND lease_owner=%s AND lease_until>now() AND state='running' AND attempt_count=%s FOR UPDATE",
                    (job["index_id"], owner, attempt["number"]))).fetchone()
                if not owned:
                    return None
                await conn.execute("UPDATE memory.model_steps SET state='dispatching',updated_at=now() "
                                   "WHERE step_id=%s AND state NOT IN ('source_revoked','succeeded','failed','cancelled')",
                                   (attempt["step_id"],))
            completion = JobSpec("memory.index_retry", "memory-index-result:" + original.attempt_id,
                {"index_id": job["index_id"], "completed_attempt_id": original.attempt_id,
                 "generation": job["generation"]}, ServiceClass.ELASTIC, data_generation=job["generation"])
            try:
                receipt = await models.submit(original, completion=completion)
            except Exception as error:
                if isinstance(error, AppError):
                    if error.status < 500 and error.status not in {408, 429}:
                        raise
                    if "not_configured" in error.code or "configuration" in error.code:
                        raise
                # Submission may already have reached Models. Preserve this
                # exact attempt; a later status read or idempotent submit settles it.
                await self._finish(job["index_id"], owner, "observing", "model_submit_unconfirmed", delay=OBSERVE_SECONDS)
                return None
        if receipt.attempt_id != original.attempt_id or receipt.intent_id != original.intent_id:
            raise AppError("memory_model_result_identity_conflict", status=409)
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, job["generation"])
            await conn.execute("UPDATE memory.model_steps SET state=%s,error_code=%s,updated_at=now() "
                "WHERE step_id=%s AND state<>'source_revoked'", (receipt.status, receipt.error_code, attempt["step_id"]))
            if receipt.status in {"queued", "running"} and attempt.get("status") != receipt.status:
                changed = await (await conn.execute("UPDATE memory.index_attempts SET status=%s,error_code=NULL,"
                    "event_revision=event_revision+1,updated_at=now() WHERE index_id=%s AND number=%s RETURNING *",
                    (receipt.status, job["index_id"], attempt["number"]))).fetchone()
                attempt.update(changed)
        if receipt.status in {"queued", "running"}:
            await self._finish(job["index_id"], owner, "observing", None, delay=OBSERVE_SECONDS)
            return None
        if receipt.status != "succeeded":
            explicit = getattr(receipt, "cancellation_reason", None) == "explicit_cancel"
            raise AppError("explicit_cancel" if explicit else receipt.error_code or "model_outcome_" + receipt.status,
                status=503, details={"status": receipt.status, "receipt_error_code": receipt.error_code,
                    "cancellation_reason": getattr(receipt, "cancellation_reason", None)})
        try:
            result = await models.result(original.attempt_id, context=context)
        except Exception as error:
            if isinstance(error, AppError) and error.status in {401, 403}:
                raise
            await self._finish(job["index_id"], owner, "observing", "model_result_unavailable", delay=OBSERVE_SECONDS)
            return None
        if result is None:
            await self._finish(job["index_id"], owner, "observing", "model_result_unavailable", delay=OBSERVE_SECONDS)
            return None
        if result.receipt.attempt_id != original.attempt_id or result.receipt.intent_id != original.intent_id:
            raise AppError("memory_model_result_identity_conflict", status=409)
        if result.receipt.status != "succeeded":
            await self._finish(job["index_id"], owner, "observing", "model_result_not_settled", delay=OBSERVE_SECONDS)
            return None
        return result

    async def _reuse(self, job, owner, context, refs, content):
        """Reuse only an identical document for this object and embedding space."""
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, job["generation"])
            if not await self.memory.sources.validate_persistence(conn, context, refs):
                await self._finish_on(conn, job, owner, "revoked", "source_unavailable")
                return True
            fresh = await self._load_on(conn, job["kind"], job["object_id"], job["revision"], lock=True)
            if fresh is None or fresh[2] != refs or fresh[3] != content:
                await self._finish_on(conn, job, owner, "obsolete", "memory_index_revision_obsolete")
                return True
            owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                "AND lease_owner=%s AND lease_until>now() AND state='running' FOR UPDATE",
                (job["index_id"], owner))).fetchone()
            if not owned:
                return True
            digest = body_hash(normalized_input(content))
            copied = await (await conn.execute("INSERT INTO memory.search_vectors"
                "(profile_key,generation,kind,object_id,revision,content_hash,embedding) "
                "SELECT profile_key,generation,kind,object_id,%s,content_hash,embedding FROM memory.search_vectors "
                "WHERE profile_key=%s AND generation=%s AND kind=%s AND object_id=%s AND content_hash=%s "
                "ORDER BY revision DESC LIMIT 1 ON CONFLICT(profile_key,generation,kind,object_id,revision) DO UPDATE "
                "SET content_hash=EXCLUDED.content_hash,embedding=EXCLUDED.embedding,created_at=now() RETURNING object_id",
                (job["revision"], job["profile_key"], job["generation"], job["kind"], job["object_id"], digest))).fetchone()
            if copied:
                await self._finish_on(conn, job, owner, "succeeded")
                return True
        return False

    @staticmethod
    async def _finish_on(conn, job, owner, state, error=None):
        await conn.execute("UPDATE memory.index_jobs SET state=%s,last_error=%s,next_due=NULL,updated_at=now() "
            "WHERE index_id=%s AND lease_owner=%s AND lease_until>now()", (state, error, job["index_id"], owner))

    async def _failure(self, job, attempt, owner, error, *, allow_retry):
        code = getattr(error, "code", type(error).__name__)
        if getattr(error, "status", None) in {403, 404} or code in {"context_changed", "access_changed"}:
            return await self._finish(job["index_id"], owner, "revoked", code)
        if attempt is None:
            retry = transient_model_failure(code)
            return await self._finish(job["index_id"], owner, "retry_wait" if retry else "failed", code,
                                      delay=retry_delay_seconds(1) if retry else None)
        details = getattr(error, "details", None) or {}
        status = details.get("status")
        if attempt.get("status") == "prepared" and status is None:
            try:
                observed = await self.workers.embeddings.models.status(attempt["model_attempt_id"])
            except Exception:
                return await self._finish(job["index_id"], owner, "observing", code, delay=OBSERVE_SECONDS)
            if observed is None:
                # Nothing in Models proves a physical call occurred.
                return await self._finish(job["index_id"], owner, "failed", code)
            status = observed.status
            if status == "succeeded":
                return await self._finish(job["index_id"], owner, "observing", code, delay=OBSERVE_SECONDS)
            details = {**details, "status": status, "receipt_error_code": observed.error_code,
                       "cancellation_reason": getattr(observed, "cancellation_reason", None)}
        explicit_cancel = code == "explicit_cancel" or details.get("cancellation_reason") == "explicit_cancel"
        receipt_code = details.get("receipt_error_code", code)
        if transient_model_failure(code) and not explicit_cancel:
            try:
                receipt = await self.workers.embeddings.models.status(attempt["model_attempt_id"])
            except Exception:
                # A control read failure is not proof the original stopped.
                return await self._finish(job["index_id"], owner, "observing", code, delay=OBSERVE_SECONDS)
            status = receipt.status if receipt else status or "unknown"
            code = (receipt.error_code if receipt else None) or code
            explicit_cancel = bool(receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel")
            receipt_code = receipt.error_code if receipt else code
        status = status or "failed"
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, job["generation"])
            owned = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE index_id=%s AND lease_owner=%s "
                "AND lease_until>now() AND attempt_count=%s FOR UPDATE",
                (job["index_id"], owner, attempt["number"]))).fetchone()
            if not owned:
                return
            prior_status = attempt.get("status")
            if prior_status != status or attempt.get("error_code") != (receipt_code if explicit_cancel else code):
                if status in {"failed", "cancelled", "unknown"}:
                    await self.recovery.fail_on(conn, attempt, code, unknown=status == "unknown", state=status,
                                                retry_after=retry_delay_seconds(attempt["number"]) if status == "failed" else None)
                updated = await (await conn.execute("UPDATE memory.index_attempts SET status=%s,error_code=%s,"
                    "event_revision=event_revision+1,updated_at=now() WHERE index_id=%s AND number=%s RETURNING *",
                    (status, receipt_code if explicit_cancel else code, job["index_id"], attempt["number"]))).fetchone()
                attempt.update(updated)
                await self.recovery.record_attempt_on(conn, job, attempt, actor_id=job.get("actor_id"))
            successor = None
            if explicit_cancel:
                state, delay = "cancelled", None
            elif status in {"queued", "running", "unknown"}:
                state, delay = "observing", OBSERVE_SECONDS
            elif transient_model_failure(code, cancelled=status == "cancelled" and code in {None, "cancelled", "model_cancelled"}):
                if attempt["number"] >= MAX_ATTEMPTS:
                    state, delay = "failed", None
                elif allow_retry:
                    successor = await self._attempt_on(conn, job, attempt["number"] + 1)
                    state, delay = "retry_wait", retry_delay_seconds(attempt["number"])
                else:
                    state, delay = "retry_wait", retry_delay_seconds(attempt["number"])
            else:
                state, delay = "cancelled" if status == "cancelled" else "failed", None
                successor = None
            if successor is None:
                await conn.execute("UPDATE memory.index_jobs SET state=%s,next_due=now()+%s::double precision*interval '1 second',"
                    "last_error=%s,updated_at=now() WHERE index_id=%s AND lease_owner=%s",
                    (state, delay, "explicit_cancel" if explicit_cancel else code, job["index_id"], owner))
                return
        # Selection is committed before dispatch; a late original cannot win a
        # write after this new attempt has been selected.
        await self._execute(job, successor, owner, allow_retry=False)

    async def recover(self, generation):
        try:
            profile, fingerprint = await self.workers.embeddings.profile()
        except AppError:
            # Still repair keyword documents once. No embedding is dispatched
            # until a real configured profile exists; permanent errors settle
            # this placeholder, and a newly configured profile has a new key.
            profile, fingerprint = None, "unavailable"
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, generation)
            # Create the partition before this transaction reads its parent;
            # concurrent profile creators cannot deadlock upgrading read locks.
            if profile is not None:
                await conn.execute("SELECT memory.ensure_embedding_space(%s)", (fingerprint,))
            for kind in ("claim", "episode", "matter"):
                cursor = await (await conn.execute("SELECT * FROM memory.index_scan WHERE kind=%s FOR UPDATE SKIP LOCKED", (kind,))).fetchone()
                if cursor is None:
                    continue
                if cursor["profile_key"] != fingerprint or cursor["generation"] != generation:
                    cursor = {**cursor, "after_id": "", "after_revision": 0}
                    await conn.execute("UPDATE memory.index_scan SET profile_key=%s,generation=%s,after_id='',after_revision=0 WHERE kind=%s",
                                       (fingerprint, generation, kind))
                table, key = TABLES[kind]
                if kind == "claim":
                    query = ("SELECT r.claim_id AS id,r.revision,CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END AS kind "
                        ",c.current_revision=r.revision AS eligible "
                        "FROM memory.claim_revisions r JOIN memory.claims c ON c.claim_id=r.claim_id "
                        "WHERE r.revision=c.current_revision AND r.state='active' AND r.current_status='current' AND (r.lexical='' OR NOT EXISTS("
                        "SELECT 1 FROM memory.search_vectors v WHERE v.profile_key=%s AND v.generation=%s AND v.object_id=r.claim_id "
                        "AND v.revision=r.revision AND v.kind=CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END)) "
                        "AND (r.claim_id,r.revision)>(%s,%s) ORDER BY r.claim_id,r.revision LIMIT %s")
                else:
                    query = (f"SELECT r.{key} AS id,r.revision FROM memory.{table} r WHERE r.active AND (r.lexical='' OR NOT EXISTS("
                        f"SELECT 1 FROM memory.search_vectors v WHERE v.profile_key=%s AND v.generation=%s AND v.kind='{kind}' "
                        f"AND v.object_id=r.{key} AND v.revision=r.revision)) "
                        f"AND (r.{key},r.revision)>(%s,%s) ORDER BY r.{key},r.revision LIMIT %s")
                rows = await (await conn.execute(query, (fingerprint, generation, cursor["after_id"], cursor["after_revision"], SCAN_LIMIT))).fetchall()
                for row in rows:
                    if not row.get("eligible", True):
                        continue
                    object_kind = row.get("kind", kind)
                    try:
                        loaded = await self._load_on(conn, object_kind, row["id"], row["revision"])
                    except AppError as error:
                        if error.status in {401, 403, 404}:
                            continue
                        raise
                    if loaded is None:
                        continue
                    digest = body_hash(normalized_input(loaded[3]))
                    job = await self._ensure_on(conn, object_kind, row["id"], row["revision"], profile, fingerprint, generation, digest)
                    if not job["leased"]:
                        await self._schedule_on(conn, job)
                last = rows[-1] if len(rows) == SCAN_LIMIT else {"id": "", "revision": 0}
                await conn.execute("UPDATE memory.index_scan SET after_id=%s,after_revision=%s WHERE kind=%s",
                    (last["id"], last["revision"], kind))
            due = await (await conn.execute("SELECT * FROM memory.index_jobs "
                "WHERE generation=%s AND state IN ('pending','running','observing','retry_wait') AND next_due<=now() "
                "AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_due,index_id LIMIT 64 FOR UPDATE SKIP LOCKED", (generation,))).fetchall()
            for job in due:
                await self._schedule_on(conn, job)
        if self.recovery.statistics is not None:
            try:
                await self.recovery.backfill(generation)
            except Exception:
                LOG.exception("memory index statistics backfill failed")
