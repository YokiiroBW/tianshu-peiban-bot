"""Shared physical-download budgets over the existing media job and source ledgers."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef

from ..contracts import Fault, digest
from ..execution_context import CURRENT_ACTION, current_generation
from ..serialization import context_from


OWNER = "capabilities.media.download"
TRANSIENT = frozenset({
    "download_failed", "music_download_incomplete", "comic_source_dns_failed", "comic_source_server_error",
    "comic_source_image_failed", "comic_source_connection_refused",
})


class DownloadDeferred(Fault):
    def __init__(self, seconds=30):
        super().__init__("media_download_deferred", 503)
        self.retry_after = max(1, seconds)


@asynccontextmanager
async def physical_download(media, job, owner, *, family, material, route, request, partition_size=1):
    """Fence one actual transfer; cached reads and publication polling do not spend it.

    Each independently cached page or video part is a source material.
    Its retries and route changes share one five-attempt lineage. The policy
    stores all attempts; the job ledger remains authoritative for ownership,
    cancellation, original source references and downloaded bytes.
    """
    capabilities = getattr(media, "capabilities", None)
    if capabilities is None:
        # The media library can also be embedded independently in isolated
        # callers. Product composition always supplies its capabilities owner.
        yield
        return
    policy = getattr(capabilities, "recovery_policy", None)
    if policy is None:
        raise Fault("media_recovery_policy_unavailable", 503)
    generation = current_generation()
    if generation is not None and generation != job.get("data_generation"):
        raise Fault("job_generation_expired", 409)
    action = CURRENT_ACTION.get()
    context = action.context if action else context_from(job["context"])
    source_refs = action.source_refs if action else tuple(
        SourceRef(**value) for value in job.get("source_refs", ()))
    await capabilities.authority.check(context, source_refs)
    material_id = "media-download:" + digest([job["job_id"], family, material])
    version = digest([job["job_id"], family, material])
    subject = context.actor_id or "principal:" + context.principal_id
    dependency = "media-download:" + family + ":" + route
    request_digest = digest([family, material, route, request])
    attempt_id = "media-download:" + digest([material_id, job["generation"], route, request_digest])
    try:
        async with capabilities.runtime.database.transaction() as connection:
            # Use the same source -> domain lock order as withdrawal and writes.
            if not await capabilities.sources.validate_persistence(connection, context, source_refs):
                raise Fault("source_revoked", 410)
            saved = await (await connection.execute(
                "SELECT owner,generation,body FROM capability_media.jobs WHERE id=%s FOR UPDATE",
                (job["job_id"],))).fetchone()
            body = json.loads(saved["body"]) if saved else {}
            if (not saved or saved["owner"] != owner or saved["generation"] != job["generation"]
                    or body.get("cancel_requested")):
                raise Fault("lease_conflict", 409)
            dependencies = body.get("recovery_dependencies", {})
            previous_dependency = dependencies.get(material_id, dependency)
            lineage = await policy.register_on(
                connection, domain="media", subject_id=subject, owner=OWNER,
                material_id=material_id, material_digest=version, dependency=previous_dependency,
                current_version=version, max_attempts=5, data_generation=job["data_generation"])
            if previous_dependency != dependency:
                lineage = await policy.update_dependency_on(
                    connection, lineage["lineage_id"], owner=OWNER, expected_version=version,
                    expected_dependency=previous_dependency, new_dependency=dependency)
            if dependencies.get(material_id) != dependency:
                body["recovery_dependencies"] = {**dependencies, material_id: dependency}
                await connection.execute("UPDATE capability_media.jobs SET body=%s WHERE id=%s",
                                         (json.dumps(body, ensure_ascii=False), job["job_id"]))
            token = None
            original = "none"
            if lineage["attempts"]:
                if lineage["attempts"] >= lineage["max_attempts"]:
                    raise Fault("media_download_budget_exhausted", 409)
                if lineage["due_at"] is None:
                    last = body.get("recovery_outcomes", {}).get(material_id, {})
                    if (family == "book" and last.get("status") == "succeeded"
                            and (partition_size > 1 or last.get("partition_size", 1) > 1)
                            and last.get("request_digest") != request_digest):
                        claim = await policy.claim_continuation_on(
                            connection, lineage["lineage_id"], owner=OWNER, expected_version=version,
                            prior_attempt_id=last["attempt_id"], reason="capacity_repartition")
                        token, original = claim["claim_token"], "succeeded"
                    else:
                        if lineage.get("failure_category") in {
                            "missing_input", "invalid_output", "capacity", "state_changed", "source_invalid", "other"
                        }:
                            raise Fault("media_download_previous_failed", 409)
                        # A reserved, unknown or succeeded original is never issued again.
                        raise Fault("media_download_original_unresolved", 409)
                else:
                    claim = await policy.claim_lineage_due_on(connection, lineage["lineage_id"], owner=OWNER)
                    if claim is None:
                        due = lineage.get("due_at")
                        seconds = max(1, (due - datetime.now(UTC)).total_seconds()) if due else 30
                        raise DownloadDeferred(seconds)
                    token, original = claim["claim_token"], "failed"
            reservation = await policy.reserve_attempt_on(
                connection, lineage["lineage_id"], owner=OWNER, expected_version=version,
                attempt_id=attempt_id, request_digest=request_digest, route=route,
                original_state=original, claim_token=token)
            if reservation.status != "reserved":
                raise Fault("media_download_original_unresolved", 409)
    except AppError as error:
        if isinstance(error, Fault):
            raise
        if error.code in {"recovery_dependency_cooling", "recovery_dependency_probe_in_progress",
                          "recovery_claim_in_progress", "recovery_claim_lost"}:
            raise DownloadDeferred() from error
        if error.code == "recovery_original_not_settled":
            raise Fault("media_download_original_unresolved", 409) from error
        raise Fault(error.code, error.status) from error

    async def finish(error=None):
        from tianshu.runtime.recovery_policy import FailureCategory, classify_failure

        async with capabilities.runtime.database.transaction() as connection:
            await capabilities.runtime.jobs.assert_generation_on(connection, job["data_generation"])
            if error is None:
                await policy.succeed_attempt_on(connection, attempt_id, probe_token=reservation.probe_token)
                if family == "book":
                    row = await (await connection.execute(
                        "SELECT owner,generation,body FROM capability_media.jobs WHERE id=%s FOR UPDATE",
                        (job["job_id"],))).fetchone()
                    if not row or row["owner"] != owner or row["generation"] != job["generation"]:
                        raise Fault("lease_conflict", 409)
                    body = json.loads(row["body"])
                    outcomes = body.get("recovery_outcomes", {})
                    outcomes[material_id] = {"attempt_id": attempt_id, "status": "succeeded",
                        "request_digest": request_digest, "partition_size": partition_size}
                    body["recovery_outcomes"] = outcomes
                    await connection.execute("UPDATE capability_media.jobs SET body=%s WHERE id=%s",
                                             (json.dumps(body, ensure_ascii=False), job["job_id"]))
            else:
                code = getattr(error, "code", type(error).__name__)
                cancelled = False
                if isinstance(error, asyncio.CancelledError):
                    row = await (await connection.execute(
                        "SELECT body FROM capability_media.jobs WHERE id=%s", (job["job_id"],))).fetchone()
                    cancelled = bool(row and json.loads(row["body"]).get("cancel_requested"))
                if cancelled:
                    code = "cancelled"
                unknown = (isinstance(error, asyncio.CancelledError) and not cancelled
                           or getattr(error, "effect", None) == "unknown"
                           or not isinstance(error, (AppError, TimeoutError, OSError, asyncio.CancelledError)))
                category = (FailureCategory.STATE_CHANGED if cancelled else
                            classify_failure(code, external_outcome_unknown=unknown))
                if not unknown and (code in TRANSIENT or isinstance(error, (TimeoutError, OSError))):
                    category = FailureCategory.TRANSPORT
                await policy.fail_attempt_on(connection, attempt_id, code=code, category=category,
                                             retry_after=getattr(error, "retry_after", None),
                                             probe_token=reservation.probe_token)

    try:
        yield
    except BaseException as error:
        await asyncio.shield(finish(error))
        raise
    else:
        await finish()
