"""Observed index attempts and an Operations port for one exact index target."""

from datetime import UTC, datetime

from tianshu.models.retry import transient_model_failure
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.types import ServiceClass, TrustedContext
from .candidate_sources import check_rows_on
from .embedding import normalized_input


MAX_ATTEMPTS = 3
PRODUCER = "memory.index.recovery"
ACTION_NAME = "memory.index.rebuild"
ERROR_FAMILIES = ("memory_index_failed", "memory_index_missing", "memory_vector_candidate_budget")


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class IndexRecovery:
    def __init__(self, jobs):
        self.jobs, self.memory, self.database = jobs, jobs.memory, jobs.database
        self.statistics = self.policy = self.guard_on = None

    def bind(self, *, statistics=None, recovery_policy=None, guard_on=None):
        if statistics is not None:
            self.statistics = statistics
        if recovery_policy is not None:
            self.policy = recovery_policy
        if guard_on is not None:
            self.guard_on = guard_on

    def owner(self, job):
        return "memory.index:" + job["index_id"]

    def material_id(self, job):
        return "memory-index:" + body_hash((job["kind"], job["object_id"], job["revision"],
                                             job["profile_key"], job["content_hash"]))

    async def reserve_on(self, conn, job, attempt, request, content):
        if self.policy is None or attempt.get("recovery_reserved"):
            return True
        from .authority import request_identity
        owner = self.owner(job)
        quota_group = (job.get("profile") or {}).get("quota_group")
        if not isinstance(quota_group, str) or not quota_group:
            raise AppError("memory_index_quota_group_unavailable", status=503)
        existing_lineage, previous_dependency = job.get("recovery_lineage_id"), job.get("recovery_dependency")
        if existing_lineage and previous_dependency and previous_dependency != quota_group:
            await self.policy.update_dependency_on(conn, existing_lineage, owner=owner,
                expected_version=str(job["revision"]), expected_dependency=previous_dependency, new_dependency=quota_group)
        lineage = await self.policy.register_on(conn, domain="memory.index", subject_id=job["object_id"],
            owner=owner, material_id=self.material_id(job),
            material_digest=body_hash(normalized_input(content)), dependency=quota_group,
            current_version=str(job["revision"]), max_attempts=MAX_ATTEMPTS, data_generation=job["generation"])
        lineage_id = _field(lineage, "lineage_id")
        if not lineage_id:
            raise AppError("memory_index_recovery_lineage_unavailable", status=503)
        claim_token = None
        if attempt["number"] > 1:
            matching = await self.policy.claim_lineage_due_on(conn, lineage_id, owner=owner, lease_seconds=30)
            if matching is None:
                return False
            claim_token = _field(matching, "claim_token")
        reservation = await self.policy.reserve_attempt_on(conn, lineage_id, owner=owner,
            expected_version=str(job["revision"]), attempt_id=attempt["model_attempt_id"],
            request_digest=body_hash(request_identity(request)),
            route=(job.get("profile") or {}).get("route_id") or "embedding:" + job["profile_key"],
            original_state="none" if attempt["number"] == 1 else "failed", claim_token=claim_token)
        probe_token = _field(reservation, "probe_token")
        await conn.execute("UPDATE memory.index_jobs SET recovery_lineage_id=%s,recovery_dependency=%s WHERE index_id=%s",
                           (lineage_id, quota_group, job["index_id"]))
        job["recovery_lineage_id"], job["recovery_dependency"] = lineage_id, quota_group
        await conn.execute("UPDATE memory.index_attempts SET recovery_reserved=true,recovery_probe_token=%s "
                           "WHERE index_id=%s AND number=%s", (probe_token, job["index_id"], attempt["number"]))
        attempt["recovery_reserved"], attempt["recovery_probe_token"] = True, probe_token
        return True

    async def fail_on(self, conn, attempt, code, *, unknown=False, retry_after=None, state="failed"):
        if self.policy is None or not attempt.get("recovery_reserved"):
            return
        from tianshu.runtime.recovery_policy import FailureCategory, classify_failure
        cancelled = state == "cancelled" or code in {"explicit_cancel", "cancelled", "model_cancelled"}
        if cancelled:
            code, category, state = "cancelled", FailureCategory.STATE_CHANGED, "failed"
        else:
            category = classify_failure(code, external_outcome_unknown=unknown)
        if attempt.get("status") == "unknown" and not unknown:
            await self.policy.resolve_unknown_on(conn, attempt["model_attempt_id"],
                observation_ref="models.attempt:" + attempt["model_attempt_id"], state=state, code=code,
                category=category, retry_after=retry_after, probe_token=attempt.get("recovery_probe_token"))
        else:
            await self.policy.fail_attempt_on(conn, attempt["model_attempt_id"], code=code, category=category,
                retry_after=retry_after, probe_token=attempt.get("recovery_probe_token"))

    async def success_on(self, conn, attempt):
        if self.policy is not None and attempt.get("recovery_reserved"):
            if attempt.get("status") == "unknown":
                await self.policy.resolve_unknown_on(conn, attempt["model_attempt_id"],
                    observation_ref="models.attempt:" + attempt["model_attempt_id"], state="succeeded",
                    probe_token=attempt.get("recovery_probe_token"))
            else:
                await self.policy.succeed_attempt_on(conn, attempt["model_attempt_id"],
                                                     probe_token=attempt.get("recovery_probe_token"))

    async def record_attempt_on(self, conn, job, attempt, *, actor_id=None):
        if self.statistics is None:
            return False
        number, status, code = attempt["number"], attempt["status"], attempt.get("error_code")
        retryable = status == "failed" and transient_model_failure(code) and number < MAX_ATTEMPTS
        if status == "succeeded" and number > 1 and job.get("state") == "succeeded":
            state, verified, failed = "verified", 1, 0
        elif status == "failed" and number > 1:
            state, verified, failed = "failed", 0, 1
        elif retryable:
            state, verified, failed = "eligible", 0, 0
        else:
            return True
        event = {"family": "recovery", "event_id": "memory-index:" + attempt["model_attempt_id"],
            "revision": attempt["event_revision"], "occurred_at": attempt.get("updated_at") or datetime.now(UTC),
            "module": "memory", "actor_id": actor_id, "route_id": (job.get("profile") or {}).get("route_id"),
            "dimensions": {"state": state}, "metrics": {"recovery_eligible": int(retryable),
                "recovery_verified": verified, "recovery_failed": failed}}
        recorded = await self.statistics.record_safely_on(conn, event)
        if not recorded:
            try:
                async with conn.transaction():
                    await self.statistics.mark_producer_on(conn, PRODUCER, watermark="", complete=False,
                        detail={"source": "memory.index_attempts", "reason": "event_write_pending"})
            except Exception:
                # Observability must not roll back the domain transition.
                pass
        return recorded

    async def backfill(self, generation, *, limit=64):
        if self.statistics is None:
            return {"complete": False, "reason": "statistics_unavailable"}
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn, generation)
            cursor = await (await conn.execute("SELECT * FROM memory.index_stats_cursor WHERE singleton FOR UPDATE")).fetchone()
            if cursor["generation"] != generation:
                cursor = {**cursor, "after_index_id": "", "after_number": 0}
                await conn.execute("UPDATE memory.index_stats_cursor SET generation=%s,after_index_id='',after_number=0 "
                                   "WHERE singleton", (generation,))
            rows = await (await conn.execute(
                "SELECT a.*,j.kind,j.object_id,j.revision AS object_revision,j.profile,j.state AS job_state,"
                "COALESCE(c.actor_id,m.actor_id,e.actor_id) AS actor_id FROM memory.index_attempts a "
                "JOIN memory.index_jobs j USING(index_id) LEFT JOIN memory.claims c ON j.kind IN ('claim','hypothesis') AND c.claim_id=j.object_id "
                "LEFT JOIN memory.matters m ON j.kind='matter' AND m.matter_id=j.object_id "
                "LEFT JOIN memory.episodes e ON j.kind='episode' AND e.episode_id=j.object_id "
                "WHERE j.generation=%s AND (a.index_id,a.number)>(%s,%s) ORDER BY a.index_id,a.number LIMIT %s",
                (generation, cursor["after_index_id"], cursor["after_number"], limit))).fetchall()
            last, complete, processed = None, True, 0
            for row in rows:
                job = {"profile": row["profile"], "state": row["job_state"]}
                if not await self.record_attempt_on(conn, job, row, actor_id=row["actor_id"]):
                    complete = False
                    break
                last = row
                processed += 1
            finished = complete and len(rows) < limit
            after_id = "" if finished else (last["index_id"] if last else cursor["after_index_id"])
            after_number = 0 if finished else (last["number"] if last else cursor["after_number"])
            await conn.execute("UPDATE memory.index_stats_cursor SET after_index_id=%s,after_number=%s WHERE singleton",
                               (after_id, after_number))
            await self.statistics.mark_producer_on(conn, PRODUCER, watermark=f"{after_id}:{after_number}",
                complete=finished, detail={"source": "memory.index_attempts", "batch": len(rows)})
            return {"complete": finished, "processed": processed, "watermark": f"{after_id}:{after_number}"}

    async def context_for_recovery(self, kind, object_id, revision):
        if kind not in {"claim", "hypothesis", "matter", "episode"} or not isinstance(object_id, str) or type(revision) is not int or revision < 1:
            raise AppError("invalid_memory_index_recovery_target")
        async with self.database.transaction(read_only=True) as conn:
            loaded = await self.jobs._load_on(conn, kind, object_id, revision)
            if loaded is None or not isinstance(loaded[1], TrustedContext):
                raise AppError("memory_index_recovery_target_unavailable", status=404)
            return loaded[1]

    async def _facts_on(self, conn, context, target, profile_key, quota_group, *, lock=True):
        kind, object_id, revision = target.get("kind"), target.get("id"), target.get("revision")
        if kind not in {"claim", "hypothesis", "matter", "episode"} or not isinstance(object_id, str) or type(revision) is not int or revision < 1:
            raise AppError("invalid_memory_index_recovery_target")
        await self.memory.identity.check_context(context, connection=conn)
        executing = current_execution_generation()
        if executing is not None:
            await self.memory.jobs.assert_generation_on(conn, executing)
        generation = await self.memory.jobs.current_generation_on(conn)
        try:
            loaded = await self.jobs._load_on(conn, kind, object_id, revision, lock=lock)
        except AppError as error:
            if error.status in {401, 403, 404}:
                return {"eligible": False, "reason": "source_unavailable"}
            raise
        if loaded is None or loaded[0]["actor_id"] != context.actor_id:
            return {"eligible": False, "reason": "revision_changed"}
        row, _owner_context, refs, content = loaded
        if kind in {"claim", "hypothesis"} and (row["memory_kind"] == "hypothesis") != (kind == "hypothesis"):
            return {"eligible": False, "reason": "kind_changed"}
        scope_groups = row.get("scope_groups") or (row["scope_ids"],)
        if not any(set(group) <= set(context.scope_ids) for group in scope_groups):
            return {"eligible": False, "reason": "scope_unavailable"}
        proof = {"_supports": [refs], "_derivations": (), "_explicit": (), "_relation_proofs": None}
        if (await check_rows_on(self.memory.sources, conn, context, (proof,)))[0] is not True:
            return {"eligible": False, "reason": "source_unavailable"}
        content_hash = body_hash(normalized_input(content))
        vector = await (await conn.execute("SELECT 1 FROM memory.search_vectors WHERE profile_key=%s AND generation=%s AND kind=%s "
            "AND object_id=%s AND revision=%s AND content_hash=%s", (profile_key, generation, kind, object_id, revision,
            content_hash))).fetchone()
        job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE generation=%s AND kind=%s AND object_id=%s "
            "AND revision=%s AND profile_key=%s AND content_hash=%s" + (" FOR UPDATE" if lock else ""),
            (generation, kind, object_id, revision, profile_key, content_hash))).fetchone()
        attempt = (await (await conn.execute("SELECT status,error_code FROM memory.index_attempts WHERE index_id=%s "
            "AND number=%s", (job["index_id"], job["attempt_count"]))).fetchone()) if job and job["attempt_count"] else None
        original_state = attempt["status"] if attempt else "none"
        lineage_id = job.get("recovery_lineage_id") if job else None
        retry_failed = (job is not None and job["state"] in {"failed", "retry_wait"} and
            original_state == "failed" and transient_model_failure(attempt["error_code"]) and
            job["attempt_count"] < MAX_ATTEMPTS)
        restore_succeeded = job is not None and job["state"] == "succeeded" and original_state == "succeeded"
        eligible = not vector and self.policy is not None and lineage_id is not None and (retry_failed or restore_succeeded)
        return {"eligible": bool(eligible), "version": str(revision), "profile_key": profile_key, "generation": generation,
            "policy_domain": "memory.index", "owner": self.owner(job) if job else None,
            "subject_id": object_id, "lineage_id": lineage_id, "max_attempts": MAX_ATTEMPTS,
            "material_id": self.material_id(job) if job else None, "material_digest": content_hash,
            "dependency": quota_group, "original_state": original_state,
            "paused": False, "cancelled": bool(job and job["state"] in {"cancelled", "revoked"}),
            "reason": ("already_indexed" if vector else None if eligible else
                       "recovery_lineage_unavailable" if not lineage_id or self.policy is None else "index_not_recoverable"),
            "job_state": job["state"] if job else "none",
            "index_id": job["index_id"] if job else None}

    async def candidates(self, *, limit=32):
        """Read due candidates from the index ledger, never from inferred log IDs."""
        if type(limit) is not int or limit < 1:
            raise AppError("invalid_memory_recovery_candidate_limit")
        limit = min(limit, 128)
        if self.policy is None:
            return ()
        profile, fingerprint = await self.jobs.workers.embeddings.profile()
        quota_group = profile.get("quota_group")
        if not isinstance(quota_group, str) or not quota_group:
            return ()
        result = []
        async with self.database.transaction() as conn:
            executing = current_execution_generation()
            if executing is not None:
                await self.memory.jobs.assert_generation_on(conn, executing)
            generation = await self.memory.jobs.current_generation_on(conn)
            rows = await (await conn.execute(
                "SELECT j.* FROM memory.index_jobs j JOIN memory.index_attempts a "
                "ON a.index_id=j.index_id AND a.number=j.attempt_count "
                "WHERE j.generation=%s AND j.profile_key=%s AND j.recovery_lineage_id IS NOT NULL "
                "AND (j.lease_until IS NULL OR j.lease_until<now()) AND ("
                "(j.state IN ('failed','retry_wait') AND a.status='failed' AND j.attempt_count<%s) OR "
                "(j.state='succeeded' AND a.status='succeeded' AND NOT EXISTS(SELECT 1 FROM memory.search_vectors v "
                "WHERE v.profile_key=j.profile_key AND v.generation=j.generation AND v.kind=j.kind "
                "AND v.object_id=j.object_id AND v.revision=j.revision AND v.content_hash=j.content_hash))) "
                "ORDER BY COALESCE(j.next_due,j.updated_at),j.index_id LIMIT %s",
                (generation, fingerprint, MAX_ATTEMPTS, min(limit * 4, 512)))).fetchall()
            for row in rows:
                target = {"kind": row["kind"], "id": row["object_id"], "revision": row["revision"]}
                try:
                    loaded = await self.jobs._load_on(conn, row["kind"], row["object_id"], row["revision"])
                    if loaded is None:
                        continue
                    facts = await self._facts_on(conn, loaded[1], target, fingerprint, quota_group, lock=False)
                except AppError as error:
                    if error.status in {401, 403, 404}:
                        continue
                    raise
                if not facts.get("eligible") or facts.get("index_id") != row["index_id"]:
                    continue
                result.append({"eligible": True, "due_at": row["next_due"] or row["updated_at"],
                    "dependency": facts["dependency"], "error_family": ("memory_index_missing" if row["state"] == "succeeded"
                        else "memory_index_failed"), "version": facts["version"], "material_id": facts["material_id"],
                    "role": "bot-background", "target": target})
                if len(result) >= limit:
                    break
            await self.memory.jobs.assert_generation_on(conn, generation)
        return tuple(result)

    async def inspect(self, recovery, target):
        profile, fingerprint = await self.jobs.workers.embeddings.profile()
        quota_group = profile.get("quota_group")
        if not isinstance(quota_group, str) or not quota_group:
            return {"eligible": False, "reason": "memory_index_quota_group_unavailable"}
        try:
            context = await self.context_for_recovery(target.get("kind"), target.get("id"), target.get("revision"))
        except AppError as error:
            if error.status in {401, 403, 404}:
                return {"eligible": False, "reason": "source_or_revision_unavailable"}
            raise
        async with self.database.transaction() as conn:
            return await self._facts_on(conn, context, target, fingerprint, quota_group)

    async def invoke(self, recovery, target, facts, attempt_id):
        if self.guard_on is None:
            raise AppError("memory_index_recovery_guard_unavailable", status=503)
        profile, fingerprint = await self.jobs.workers.embeddings.profile()
        quota_group = profile.get("quota_group")
        if not isinstance(quota_group, str) or not quota_group:
            return {"state": "failed", "reason": "memory_index_quota_group_unavailable"}
        try:
            context = await self.context_for_recovery(target.get("kind"), target.get("id"), target.get("revision"))
        except AppError as error:
            if error.status in {401, 403, 404}:
                return {"state": "failed", "reason": "source_or_revision_unavailable"}
            raise
        async with self.database.transaction() as conn:
            await self.guard_on(conn, attempt_id, recovery["owner_token"])
            await conn.execute("SELECT memory.ensure_embedding_space(%s)", (fingerprint,))
            fresh = await self._facts_on(conn, context, target, fingerprint, quota_group)
            if (not fresh.get("eligible") or fresh.get("version") != facts.get("version") or
                    fresh.get("material_digest") != facts.get("material_digest") or
                    fresh.get("profile_key") != facts.get("profile_key") or
                    fresh.get("generation") != facts.get("generation") or
                    fresh.get("lineage_id") != facts.get("lineage_id") or fresh.get("owner") != facts.get("owner")):
                return {"state": "failed", "reason": fresh.get("reason") or "index_state_changed"}
            job = await self.jobs._ensure_on(conn, target["kind"], target["id"], target["revision"], profile, fingerprint,
                                             fresh["generation"], fresh["material_digest"])
            if job["state"] == "failed":
                await conn.execute("UPDATE memory.index_jobs SET state='retry_wait',next_due=now(),last_error=NULL,updated_at=now() "
                                   "WHERE index_id=%s AND state='failed'", (job["index_id"],))
            await self.memory.jobs.enqueue(conn, JobSpec("memory.index_retry", "memory-index-ops:" + attempt_id,
                {"index_id": job["index_id"], "generation": fresh["generation"]}, ServiceClass.ELASTIC,
                data_generation=fresh["generation"]))
        return {"state": "accepted", "index_id": job["index_id"]}

    async def observe(self, recovery, target, facts, attempt_id):
        profile, fingerprint = await self.jobs.workers.embeddings.profile()
        quota_group = profile.get("quota_group")
        if not isinstance(quota_group, str) or not quota_group:
            return {"state": "unknown", "reason": "memory_index_quota_group_unavailable"}
        try:
            context = await self.context_for_recovery(target.get("kind"), target.get("id"), target.get("revision"))
        except AppError as error:
            if error.status in {401, 403, 404}:
                return {"state": "failed", "reason": "source_or_revision_unavailable"}
            raise
        async with self.database.transaction() as conn:
            fresh = await self._facts_on(conn, context, target, fingerprint, quota_group)
            if (fresh.get("version") != facts.get("version") or fresh.get("profile_key") != facts.get("profile_key")
                    or fresh.get("generation") != facts.get("generation")):
                return {"state": "failed", "reason": "index_state_changed"}
            if fresh.get("reason") == "already_indexed":
                return {"state": "verified", "index_id": fresh.get("index_id")}
            if fresh.get("original_state") == "unknown":
                return {"state": "unknown", "index_id": fresh.get("index_id")}
            if fresh.get("job_state") in {"failed", "cancelled", "revoked", "obsolete"}:
                return {"state": "failed", "reason": fresh.get("reason")}
            return {"state": "pending", "index_id": fresh.get("index_id")}
