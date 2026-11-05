"""Budget physical reads by the original Action and material, across resumes."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import aiohttp

from tianshu.runtime.recovery_policy import FailureCategory, RecoveryWait, recovery_wait_retryable
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.runtime.serialization import wire

from ..contracts import Fault, digest
from ..execution_context import CURRENT_EXECUTION, current_generation
from ..withdrawal import validate_sources


OWNER = "capabilities.content.read"
MAX_ATTEMPTS = 5  # The existing media physical-download budget includes the first attempt.


def identity(action, attempt, method, url):
    parsed = urlsplit(action.arguments["url"])
    frozen = wire(action)
    frozen["context"]["permissions"] = sorted(frozen["context"]["permissions"])
    version = digest(frozen)
    return {
        "material_id": "content-read:" + digest([action.action_id]),
        "version": version,
        "dependency": "content-read:" + digest([parsed.scheme, parsed.netloc]),
        "attempt_id": "content-read:" + digest([attempt.attempt_id]),
        "request_digest": digest([version, method, url]),
    }


async def reserve_on(content, conn, action, attempt, method, url, *, generation):
    policy = content.recovery_policy
    if policy is None:
        raise Fault("read_recovery_policy_unavailable", 503)
    item = identity(action, attempt, method, url)
    lineage = await policy.register_on(
        conn, domain="capability_read", subject_id=action.actor_id or "principal:" + action.context.principal_id,
        owner=OWNER, material_id=item["material_id"], material_digest=item["version"],
        dependency=item["dependency"], current_version=item["version"], max_attempts=MAX_ATTEMPTS,
        data_generation=generation)
    execution = CURRENT_EXECUTION.get()
    if execution is not None:
        execution["content_read_budget"] = {"attempt_number": lineage["attempts"], "max_attempts": MAX_ATTEMPTS}
    existing = await (await conn.execute(
        "SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s", (item["attempt_id"],))).fetchone()
    claim_token, original_state = None, "none"
    if existing:
        # reserve_attempt_on checks the full original request identity.
        original_state = "failed" if lineage["attempts"] > 1 else "none"
    elif lineage["attempts"]:
        claimed = await policy.claim_lineage_due_on(conn, lineage["lineage_id"], owner=OWNER)
        if claimed is None:
            wait = await policy.inspect_wait_on(conn, lineage["lineage_id"], owner=OWNER,
                                                expected_version=item["version"])
            if wait is not None:
                raise wait
            raise Fault("read_recovery_not_eligible", 409)
        claim_token, original_state = claimed["claim_token"], "failed"
    reservation = await policy.reserve_attempt_on(
        conn, lineage["lineage_id"], owner=OWNER, expected_version=item["version"],
        attempt_id=item["attempt_id"], request_digest=item["request_digest"], route=method,
        original_state=original_state, claim_token=claim_token)
    if reservation.status != "reserved":
        raise Fault("read_original_unresolved", 409)
    return reservation


@asynccontextmanager
async def physical_read(content, action, attempt, method, url):
    execution = CURRENT_EXECUTION.get()
    if execution is None:
        yield
        return
    async with content.config.database.transaction() as conn:
        if not await validate_sources(conn, action.context, action.source_refs, content.sources):
            raise Fault("source_revoked", 410)
        row = await (await conn.execute(
            "SELECT attempt,receipt,execution_owner,EXISTS(SELECT 1 FROM capabilities.execution_owners o "
            "WHERE o.id=capabilities.actions.execution_owner AND o.expires_at>now() AND o.stopped_at IS NULL) AS owner_live "
            "FROM capabilities.actions WHERE id=%s FOR UPDATE",
            (action.action_id,))).fetchone()
        if (not row or row["attempt"]["attempt_id"] != attempt.attempt_id
                or row["receipt"]["execution"] != "running"
                or not row["owner_live"]
                or row["execution_owner"] != execution["owner"].owners.id):
            raise Fault("attempt_changed", 409)
        reservation = await reserve_on(content, conn, action, attempt, method, url,
                                       generation=current_generation())
    try:
        yield
    except BaseException as error:
        if ((execution.get("content_read_phase") or {}).get("phase") == "commit"
                and execution.get("content_read_checkpoint")):
            async def finish_read():
                async with content.config.database.transaction() as conn:
                    await content.recovery_policy.succeed_attempt_on(conn, reservation.attempt_id,
                                                                      probe_token=reservation.probe_token)
            try:
                await finish_cleanup(finish_read())
            except Exception:
                execution["content_read_effect_unknown"] = True
                raise
            execution["content_read_budget"] = {"retry_at": None, "attempt_number": reservation.number,
                                                  "max_attempts": MAX_ATTEMPTS}
            raise
        unknown = (isinstance(error, asyncio.CancelledError) or getattr(error, "effect", None) == "unknown"
                   or isinstance(error, RecoveryWait) and not recovery_wait_retryable(error))
        if unknown:
            execution["content_read_effect_unknown"] = True
        if unknown:
            category = FailureCategory.UNKNOWN_EFFECT
        elif ((execution.get("content_read_phase") or {}).get("phase") == "acquisition"
              and (isinstance(error, (TimeoutError, OSError, aiohttp.ClientError))
                   or getattr(error, "code", None) in {"content_source_unavailable", "browser_source_unavailable"})):
            category = FailureCategory.TRANSPORT
        else:
            category = FailureCategory.OTHER
        async def settle_failure():
            async with content.config.database.transaction() as conn:
                return await content.recovery_policy.fail_attempt_on(
                    conn, reservation.attempt_id, code=getattr(error, "code", type(error).__name__),
                    category=category, retry_after=getattr(error, "retry_after", None),
                    probe_token=reservation.probe_token)
        try:
            due = await finish_cleanup(settle_failure())
            execution["content_read_recovery_allowed"] = due is not None
            execution["content_read_budget"] = {"retry_at": due.isoformat() if due else None,
                "attempt_number": reservation.number, "max_attempts": MAX_ATTEMPTS}
        except Exception:
            execution["content_read_effect_unknown"] = True
            raise
        raise
    else:
        try:
            async with content.config.database.transaction() as conn:
                await content.recovery_policy.succeed_attempt_on(conn, reservation.attempt_id,
                                                                  probe_token=reservation.probe_token)
        except Exception:
            execution["content_read_effect_unknown"] = True
            raise
