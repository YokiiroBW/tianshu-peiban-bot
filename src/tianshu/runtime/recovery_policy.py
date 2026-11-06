"""Shared recovery decisions and attempt budgets; domains own business state.

Every database method accepts the domain transaction. It never submits a model
request, repeats an external effect, or declares a domain result successful.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
import hashlib
import json
import math
import random
import re
from uuid import uuid4

from tianshu.models.types import ModelReceipt

from .errors import AppError
from .rbh_evidence import confirmed_not_sent

MAX_CONNECTION_ATTEMPTS = 16

CONTINUATION_REASONS = frozenset({"need_context", "schema_correction", "capacity_repartition"})


class RecoveryWait(AppError):
    """A nonterminal dependency/claim wait, before reserving a new attempt.

    A lost claim never authorizes its former owner to update domain state. The
    owner must reacquire its claim or pass its own domain CAS before changing
    a durable wake, even though this exception carries a useful observation.
    """

    def __init__(self, reason, retry_at, *, dependency=None):
        if not isinstance(retry_at, datetime) or retry_at.utcoffset() is None:
            raise ValueError("recovery_wait_timezone_required")
        retry_at = retry_at.astimezone(UTC)
        self.reason, self.retry_at, self.dependency = reason, retry_at, dependency
        # None denotes a locally created wait with no prior RPC boundary.
        self.remote_rollback_confirmed = None
        super().__init__(reason, status=409, details={
            "retry_at": retry_at.isoformat(), "dependency": dependency,
        })


def recovery_wait_retryable(wait):
    """Combine local and independent remote evidence without upgrading either."""
    if not isinstance(wait, RecoveryWait):
        return False
    local = getattr(wait, "transaction_rollback_confirmed", None)
    remote = wait.remote_rollback_confirmed
    return (local is None or local is True) and (remote is None or remote is True)


def _validate_wait_fields(reason, retry_at, dependency):
    if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", reason):
        raise ValueError("invalid_recovery_wait_envelope")
    if not isinstance(retry_at, datetime) or retry_at.utcoffset() is None:
        raise ValueError("invalid_recovery_wait_envelope")
    if dependency is not None and (not isinstance(dependency, str) or not 1 <= len(dependency) <= 128
                                   or any(ord(character) < 32 or ord(character) == 127 for character in dependency)):
        raise ValueError("invalid_recovery_wait_envelope")


def recovery_wait_to_wire(wait):
    """Serialize only an explicit wait and its confirmed rollback evidence.

    An unconfirmed local transaction or any earlier unconfirmed remote boundary
    stays false, even when a later consumer rolls back successfully.
    """
    if not isinstance(wait, RecoveryWait):
        raise TypeError("recovery_wait_required")
    _validate_wait_fields(wait.reason, wait.retry_at, wait.dependency)
    remote = wait.remote_rollback_confirmed
    confirmed = (getattr(wait, "transaction_rollback_confirmed", None) is True
                 and (remote is None or remote is True))
    return {"kind": "recovery_wait", "version": 1, "reason": wait.reason,
            "retry_at": wait.retry_at.astimezone(UTC).isoformat(), "dependency": wait.dependency,
            "rollback_confirmed": confirmed}


def recovery_wait_from_wire(document):
    """Strictly decode the versioned typed envelope; generic errors stay errors."""
    required = {"kind", "version", "reason", "retry_at", "dependency", "rollback_confirmed"}
    if (not isinstance(document, dict) or set(document) != required or document["kind"] != "recovery_wait"
            or type(document["version"]) is not int or document["version"] != 1
            or type(document["rollback_confirmed"]) is not bool
            or not isinstance(document["retry_at"], str) or len(document["retry_at"]) > 64):
        raise ValueError("invalid_recovery_wait_envelope")
    try:
        retry_at = datetime.fromisoformat(document["retry_at"])
        _validate_wait_fields(document["reason"], retry_at, document["dependency"])
        wait = RecoveryWait(document["reason"], retry_at, dependency=document["dependency"])
    except (ValueError, OverflowError):
        raise ValueError("invalid_recovery_wait_envelope") from None
    wait.remote_rollback_confirmed = document["rollback_confirmed"]
    return wait


def _probe_recheck_at(row):
    """Re-observe a live lease without inferring the physical attempt outcome."""
    now = row["now"]
    limit = now + timedelta(seconds=30)
    expiry = row.get("probe_expires_at")
    return max(now + timedelta(seconds=5), min(expiry, limit)) if expiry and expiry > now else limit


class FailureCategory(StrEnum):
    MISSING_INPUT = "missing_input"
    TRANSPORT = "transport"
    INVALID_OUTPUT = "invalid_output"
    CAPACITY = "capacity"
    STATE_CHANGED = "state_changed"
    SOURCE_INVALID = "source_invalid"
    UNKNOWN_EFFECT = "unknown_effect"
    INFRASTRUCTURE = "infrastructure"
    INTERRUPTED = "interrupted"
    OTHER = "other"


def classify_failure(code: str | None, *, external_outcome_unknown=False) -> FailureCategory:
    """Classify known failures conservatively; unknown effects need observation."""
    if external_outcome_unknown:
        return FailureCategory.UNKNOWN_EFFECT
    value = (code or "").strip().lower()
    if value == "consumer_interrupted":
        return FailureCategory.INTERRUPTED
    if any(part in value for part in ("withdraw", "retract", "source_invalid", "source_revoked", "forgotten")):
        return FailureCategory.SOURCE_INVALID
    if any(part in value for part in ("cancel", "supersed", "state_changed", "stale", "revision_conflict", "permission")):
        return FailureCategory.STATE_CHANGED
    if any(part in value for part in ("missing_input", "input_media_required", "material_missing")):
        return FailureCategory.MISSING_INPUT
    if any(part in value for part in ("budget_exceeded", "context_length", "too_large", "capacity_exceeded")):
        return FailureCategory.CAPACITY
    if any(part in value for part in ("invalid_output", "invalid_response", "malformed", "schema_invalid")):
        return FailureCategory.INVALID_OUTPUT
    if any(part in value for part in ("database", "postgres", "infrastructure", "disk", "broker_unavailable")):
        return FailureCategory.INFRASTRUCTURE
    match = re.fullmatch(r"(?:upstream_http_|http_)(\d{3})", value)
    if (match and (int(match[1]) == 429 or 500 <= int(match[1]) <= 599)) or any(
        part in value for part in ("timeout", "connection", "transport", "rate_limit", "overloaded", "unavailable")
    ):
        return FailureCategory.TRANSPORT
    return FailureCategory.OTHER


def retry_after_seconds(value, *, now=None):
    if value is None:
        return 0.0
    now = now or datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("retry_after_timezone_required")
        return max(0.0, (value - now).total_seconds())
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError("invalid_retry_after")
        return max(0.0, seconds)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                raise ValueError
            return max(0.0, (parsed - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_retry_after") from None


def retry_due(attempts: int, *, retry_after=None, now=None, random_value=None):
    """Full jitter with Retry-After as a lower bound, without sleeping a worker."""
    if type(attempts) is not int or attempts < 1:
        raise ValueError("positive_attempt_number_required")
    now = now or datetime.now(UTC)
    value = random.random() if random_value is None else random_value
    if not 0 <= value <= 1:
        raise ValueError("invalid_jitter")
    maximum = min(900.0, 30.0 * 2 ** min(attempts - 1, 5))
    return now + timedelta(seconds=max(retry_after_seconds(retry_after, now=now), maximum * value))


def lineage_id(domain: str, material_id: str, *, generation=None) -> str:
    """Stable across route, job ID and administrative revision changes."""
    if not all(isinstance(value, str) and value for value in (domain, material_id)):
        raise ValueError("invalid_recovery_lineage")
    encoded = json.dumps([generation, domain, material_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def closed_partitions(groups, dependencies):
    """Validate a domain-proposed split against its *complete* dependency graph.

    A cross-person relation is a dependency edge, not a reason to split by person.
    The domain must prove that the graph is complete and atomically supersede the
    old batch before using this result.
    """
    partitions = tuple(frozenset(group) for group in groups)
    nodes = set(dependencies)
    if len(partitions) < 2 or any(not group for group in partitions):
        return False
    flattened = [item for group in partitions for item in group]
    if len(flattened) != len(set(flattened)) or set(flattened) != nodes:
        return False
    return all(set(dependencies[item]) <= group for group in partitions for item in group)


def compatible_routes(routes, *, purpose, protocol=None, embedding_space=None):
    """Only routes explicitly configured for this purpose and wire space qualify."""
    return tuple(route for route in routes
        if route.get("enabled", True) and purpose in route.get("purposes", ())
        and (protocol is None or protocol in route.get("protocols", ()))
        and (embedding_space is None or route.get("embedding_space") == embedding_space))


def _require_model_unknown(receipt, *, attempt_id, generation):
    """Accept only the original, locally finished receipt fetched from Models."""
    if (not isinstance(receipt, ModelReceipt) or receipt.attempt_id != attempt_id
            or receipt.data_generation != generation or receipt.status != "unknown"
            or not isinstance(receipt.finished_at, datetime) or receipt.finished_at.tzinfo is None
            or receipt.cancellation_reason == "explicit_cancel"):
        raise AppError("recovery_model_unknown_unconfirmed", status=409)


def validate_model_recovery_facts(facts, *, receipt, expected_status, data_generation):
    """Compare locked Models facts to the original receipt without touching its table."""
    if expected_status not in {"failed", "unknown", "cancelled"}:
        raise ValueError("invalid_model_recovery_status")
    if not isinstance(receipt, ModelReceipt):
        raise AppError("recovery_model_receipt_mismatch", status=409)
    required = {"attempt_id", "intent_id", "status", "dispatch", "error_code", "finished_at",
                "data_generation", "request_digest", "cancellation_reason"}
    if not isinstance(facts, Mapping) or not required <= facts.keys():
        raise AppError("recovery_model_receipt_unconfirmed", status=409)
    if facts["cancellation_reason"] == "explicit_cancel" or receipt.cancellation_reason == "explicit_cancel":
        raise AppError("recovery_model_cancelled", status=409)
    if (not isinstance(facts["finished_at"], datetime) or facts["finished_at"].utcoffset() is None
            or not isinstance(receipt.finished_at, datetime) or receipt.finished_at.utcoffset() is None
            or not isinstance(facts["request_digest"], str) or not facts["request_digest"]
            or not isinstance(receipt.request_digest, str) or not receipt.request_digest
            or not isinstance(receipt.intent_id, str) or not receipt.intent_id
            or not isinstance(facts["data_generation"], str) or not facts["data_generation"]
            or facts["cancellation_reason"] not in {None, "consumer_interrupted", "explicit_cancel"}):
        raise AppError("recovery_model_receipt_unconfirmed", status=409)
    if (facts["attempt_id"] != receipt.attempt_id or facts["intent_id"] != receipt.intent_id
            or facts["data_generation"] != data_generation or receipt.data_generation != data_generation
            or facts["request_digest"] != receipt.request_digest):
        raise AppError("recovery_model_receipt_mismatch", status=409)
    if (receipt.status != expected_status or facts["status"] != expected_status
            or facts["dispatch"] != receipt.dispatch or facts["error_code"] != receipt.error_code
            or facts["finished_at"] != receipt.finished_at or facts["cancellation_reason"] != receipt.cancellation_reason
            or expected_status == "cancelled" and not (
                receipt.error_code == "consumer_interrupted" and receipt.cancellation_reason == "consumer_interrupted"
                and facts["cancellation_reason"] == "consumer_interrupted")):
        raise AppError("recovery_model_outcome_changed", status=409)


def _consumer_interruption(receipt):
    return (isinstance(receipt, ModelReceipt) and receipt.status == "cancelled"
            and receipt.error_code == "consumer_interrupted"
            and receipt.cancellation_reason == "consumer_interrupted")


def _automatic_failure(row, category):
    return (category in {FailureCategory.TRANSPORT, FailureCategory.INFRASTRUCTURE}
            or category == FailureCategory.INTERRUPTED and row["recovery_kind"] == "pure_computation")


async def _connection_budget_on(connection, row):
    result = await (await connection.execute(
        "SELECT count(*) AS total FROM runtime.recovery_not_sent WHERE lineage_id=%s",
        (row["lineage_id"],))).fetchone()
    return result["total"]


def _budget_reason(row, connection_attempts):
    if connection_attempts >= MAX_CONNECTION_ATTEMPTS:
        return "recovery_connection_budget_exhausted"
    if row["attempts"] - connection_attempts >= row["max_attempts"]:
        return "recovery_budget_exhausted"
    return None


async def _budget_reason_on(connection, row):
    return _budget_reason(row, await _connection_budget_on(connection, row))


async def _require_budget_on(connection, row):
    reason = await _budget_reason_on(connection, row)
    if reason:
        raise AppError(reason, status=409)


def _budget_mapping(row, count, dependency):
    reason = _budget_reason(row, count)
    deadlines = [row["due_at"]] if row["due_at"] is not None else []
    if deadlines and row["claim_token"] and row["claim_expires_at"] and row["claim_expires_at"] > row["now"]:
        deadlines.append(row["claim_expires_at"])
    if deadlines and dependency:
        cooldown = dependency["cooldown_until"]
        if cooldown and cooldown > row["now"]:
            deadlines.append(cooldown)
        expiry = dependency["probe_expires_at"]
        if dependency["probe_token"] and expiry and expiry > row["now"]:
            deadlines.append(expiry)
    return {"total_attempts": row["attempts"], "computation_attempts": row["attempts"] - count,
            "connection_attempts": count, "max_computation_attempts": row["max_attempts"],
            "max_connection_attempts": MAX_CONNECTION_ATTEMPTS, "can_retry": reason is None,
            "retry_at": max(deadlines) if deadlines and reason is None else None,
            "dependency": row["dependency"], "reason": reason or "available"}


async def _budget_mapping_on(connection, row):
    count = await _connection_budget_on(connection, row)
    dependency = await (await connection.execute(
        "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies "
        "WHERE data_generation=%s AND dependency=%s", (row["data_generation"], row["dependency"]))).fetchone()
    return _budget_mapping(row, count, dependency)


async def _restore_failed_due_on(connection, row, attempt, result):
    """Wake only this latest failed attempt when restored credits permit it."""
    if (not result["can_retry"] or not result["connection_attempts"]
            or attempt["attempt_number"] != row["attempts"] or row["due_at"] is not None
            or row["claim_token"] or attempt["status"] != "failed"
            or not _automatic_failure(row, row["failure_category"])):
        return row, result
    # fail_attempt_on established Retry-After even if the old budget was
    # exhausted. Keep both the dependency cooldown and any live probe lease.
    due = retry_due(row["attempts"], now=row["now"])
    dependency = await (await connection.execute(
        "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies "
        "WHERE data_generation=%s AND dependency=%s", (row["data_generation"], row["dependency"]))).fetchone()
    if dependency:
        if dependency["cooldown_until"]:
            due = max(due, dependency["cooldown_until"])
        if dependency["probe_token"] and dependency["probe_expires_at"]:
            due = max(due, dependency["probe_expires_at"])
    row = await (await connection.execute(
        "UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s RETURNING *,now() AS now",
        (due, row["lineage_id"]))).fetchone()
    return row, await _budget_mapping_on(connection, row)


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    attempt_id: str
    lineage_id: str
    number: int
    status: str
    probe_token: str | None = None
    dependency: str | None = None


class RecoveryPolicy:
    """Transactional shared accounting, with a single owner per logical work."""

    async def computation_budget_on(self, connection, lineage_id, *, owner, expected_version):
        """Read the shared budget; external source and dependency gates still apply."""
        # One statement keeps the lineage and classifications in one MVCC
        # snapshot, even when a concurrent transaction archives a new attempt.
        row = await (await connection.execute(
            "SELECT l.*,now() AS now,g.id AS current_generation,b.total AS connection_total, "
            "d.cooldown_until,d.probe_token,d.probe_expires_at "
            "FROM runtime.recovery_lineages l "
            "LEFT JOIN runtime.installation_generation g ON g.singleton=true "
            "LEFT JOIN runtime.recovery_dependencies d ON d.data_generation=l.data_generation AND d.dependency=l.dependency "
            "LEFT JOIN LATERAL (SELECT count(*) AS total FROM runtime.recovery_not_sent n "
            "WHERE n.lineage_id=l.lineage_id) b ON true WHERE l.lineage_id=%s",
            (lineage_id,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if row["current_generation"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        return _budget_mapping(row, row["connection_total"], row)

    async def note_not_sent_on(self, connection, attempt_id, *, owner, expected_version, model_receipt):
        """Record proven connection failures after normal failure archival, once.

        An older result can add evidence but cannot move a successor's schedule,
        claim or shared probe. Insufficient evidence never grants budget credit;
        an independently proven earlier credit may restore a latest failure's due.
        """
        original = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not original:
            raise AppError("recovery_attempt_not_active", status=409)
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        interruption_facts = None
        if _consumer_interruption(model_receipt):
            from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
            interruption_facts = await lock_recovery_facts_on(connection, model_receipt.attempt_id)
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (original["lineage_id"],))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        attempt = await (await connection.execute(
            "SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s FOR UPDATE", (attempt_id,))).fetchone()
        if not attempt or attempt["lineage_id"] != row["lineage_id"]:
            raise AppError("recovery_attempt_not_active", status=409)
        if (row["current_version"] != expected_version or attempt["version"] != expected_version
                or row["paused"] or row["cancelled"] or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        if not isinstance(model_receipt, ModelReceipt) or model_receipt.attempt_id != attempt_id:
            raise AppError("recovery_model_receipt_mismatch", status=409)
        if model_receipt.data_generation != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        evidence = confirmed_not_sent(model_receipt) if attempt["status"] == "failed" else None
        if evidence is None:
            result = await _budget_mapping_on(connection, row)
            # Historical credits may have made the latest ordinary failed
            # attempt eligible again. Its own definitive receipt and existing
            # failure category are still required; unknown never authorizes it.
            if (model_receipt.status == "failed" and model_receipt.cancellation_reason is None
                    and isinstance(model_receipt.finished_at, datetime) and model_receipt.finished_at.utcoffset() is not None
                    and isinstance(model_receipt.error_code, str)
                    and _automatic_failure(row, classify_failure(model_receipt.error_code))):
                row, result = await _restore_failed_due_on(connection, row, attempt, result)
            elif _consumer_interruption(model_receipt) and row["recovery_kind"] == "pure_computation":
                validate_model_recovery_facts(interruption_facts, receipt=model_receipt, expected_status="cancelled",
                                              data_generation=row["data_generation"])
                row, result = await _restore_failed_due_on(connection, row, attempt, result)
            if result["can_retry"]:
                result["reason"] = "not_confirmed_not_sent"
            return result
        inserted = await (await connection.execute(
            "INSERT INTO runtime.recovery_not_sent(attempt_id,lineage_id,evidence_kind,models_finished_at) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING RETURNING attempt_id",
            (attempt_id, row["lineage_id"], evidence, model_receipt.finished_at))).fetchone()
        if not inserted:
            saved = await (await connection.execute(
                "SELECT evidence_kind,models_finished_at FROM runtime.recovery_not_sent "
                "WHERE attempt_id=%s", (attempt_id,))).fetchone()
            if saved["evidence_kind"] != evidence or saved["models_finished_at"] != model_receipt.finished_at:
                raise AppError("recovery_model_receipt_mismatch", status=409)
        result = await _budget_mapping_on(connection, row)
        # An earlier observation may have been budget-exhausted until older
        # evidence arrived. Rechecking the latest receipt must then restore
        # its due, without moving an existing due or a successor's schedule.
        row, result = await _restore_failed_due_on(connection, row, attempt, result)
        if result["can_retry"]:
            result["reason"] = "confirmed_not_sent"
        return result

    async def inspect_wait_on(self, connection, identity, *, owner, expected_version):
        """Observe a due/claim/dependency wait without claiming or spending budget.

        The snapshot can become stale immediately. ``None`` means only that no
        wait was observed, not that a fresh attempt is authorized.
        """
        row = await (await connection.execute(
            "SELECT l.*,now() AS now,g.id AS current_generation FROM runtime.recovery_lineages l "
            "LEFT JOIN runtime.installation_generation g ON g.singleton=true WHERE l.lineage_id=%s",
            (identity,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if row["current_generation"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        await _require_budget_on(connection, row)
        if row["claim_token"] and row["claim_expires_at"] and row["claim_expires_at"] > row["now"]:
            return RecoveryWait("recovery_claim_in_progress", row["claim_expires_at"], dependency=row["dependency"])
        if row["due_at"] and row["due_at"] > row["now"]:
            return RecoveryWait("recovery_not_due", row["due_at"], dependency=row["dependency"])
        dependency = await (await connection.execute(
            "SELECT d.*,clock_timestamp() AS now FROM runtime.recovery_dependencies d "
            "WHERE d.data_generation=%s AND d.dependency=%s",
            (row["data_generation"], row["dependency"]))).fetchone()
        if dependency:
            if dependency["cooldown_until"] and dependency["cooldown_until"] > dependency["now"]:
                return RecoveryWait("recovery_dependency_cooling", dependency["cooldown_until"],
                                    dependency=row["dependency"])
            if (dependency["probe_token"] and dependency["probe_expires_at"] is not None
                    and dependency["probe_expires_at"] > dependency["now"]):
                return RecoveryWait("recovery_dependency_probe_in_progress", _probe_recheck_at(dependency),
                                    dependency=row["dependency"])
        return None

    async def register_on(self, connection, *, domain, subject_id, owner, material_id,
                          material_digest, dependency, current_version, max_attempts, data_generation=None,
                          recovery_kind="external_effect"):
        if not all(isinstance(value, str) and value for value in
                   (domain, subject_id, owner, material_id, material_digest, dependency, current_version)):
            raise ValueError("invalid_recovery_lineage")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("positive_recovery_budget_required")
        if recovery_kind not in {"external_effect", "pure_computation"}:
            raise ValueError("invalid_recovery_kind")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        if not generation:
            raise AppError("installation_generation_missing", status=503)
        if data_generation is not None and data_generation != generation["id"]:
            raise AppError("job_generation_expired", status=409)
        identity = lineage_id(domain, material_id, generation=generation["id"])
        await connection.execute(
            "INSERT INTO runtime.recovery_lineages(lineage_id,data_generation,domain,subject_id,owner,material_digest,"
            "dependency,current_version,max_attempts,recovery_kind) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (identity, generation["id"], domain, subject_id, owner, material_digest, dependency, current_version,
             max_attempts, recovery_kind))
        row = await (await connection.execute(
            "SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (identity,))).fetchone()
        if (row["owner"] != owner or row["subject_id"] != subject_id
                or row["material_digest"] != material_digest or row["max_attempts"] != max_attempts
                or row["recovery_kind"] != recovery_kind):
            raise AppError("recovery_lineage_conflict", status=409)
        if row["dependency"] != dependency:
            raise AppError("recovery_dependency_changed", status=409)
        return row

    async def record_computation_unknown_on(self, connection, attempt_id, *, owner, expected_version,
                                            model_receipt, retry_after=None, random_value=None):
        """Schedule a pure computation only from a fresh Models unknown receipt.

        The owner fetches that receipt outside this transaction. The first
        observation fixes the due time; polling never postpones or revives it.
        """
        retry_after_seconds(retry_after)
        original = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not original:
            raise AppError("recovery_attempt_not_active", status=409)
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (original["lineage_id"],))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        if row["recovery_kind"] != "pure_computation":
            raise AppError("recovery_not_pure_computation", status=409)
        _require_model_unknown(model_receipt, attempt_id=attempt_id, generation=row["data_generation"])
        attempt = await (await connection.execute(
            "SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if (attempt["attempt_number"] != row["attempts"] or attempt["version"] != expected_version
                or attempt["status"] not in {"reserved", "unknown"}):
            raise AppError("recovery_attempt_not_current_unknown", status=409)
        if attempt["status"] == "reserved":
            await self.fail_attempt_on(connection, attempt_id, code=model_receipt.error_code or "model_outcome_unknown",
                                       category=FailureCategory.UNKNOWN_EFFECT)
        if await _budget_reason_on(connection, row):
            return None
        recorded = await (await connection.execute(
            "SELECT due_at FROM runtime.recovery_computation_unknowns WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if recorded:
            return row["due_at"]
        due = max(row["now"] + timedelta(seconds=30), retry_due(
            row["attempts"], retry_after=retry_after, now=row["now"], random_value=random_value))
        await connection.execute(
            "INSERT INTO runtime.recovery_computation_unknowns(attempt_id,models_finished_at,due_at) VALUES(%s,%s,%s)",
            (attempt_id, model_receipt.finished_at, due))
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=%s WHERE lineage_id=%s",
                                 (due, row["lineage_id"]))
        return due

    async def assert_attempt_applicable_on(self, connection, attempt_id, *, owner, expected_version):
        """Fence a superseded computation in the domain's apply transaction.

        The domain must still validate current sources, permissions and its own
        per-object CAS and receipts; separate successful continuations can apply
        independently when the owning domain's business rules permit them.
        """
        attempt = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not attempt:
            raise AppError("recovery_attempt_not_active", status=409)
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (attempt["lineage_id"],))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        if row["recovery_kind"] != "pure_computation":
            raise AppError("recovery_not_pure_computation", status=409)
        attempt = await (await connection.execute(
            "SELECT status,version,EXISTS (SELECT 1 FROM runtime.recovery_attempts newer "
            "WHERE newer.supersedes_attempt_id=a.attempt_id) AS superseded "
            "FROM runtime.recovery_attempts a WHERE attempt_id=%s",
            (attempt_id,))).fetchone()
        if (attempt["status"] != "succeeded" or attempt["version"] != expected_version
                or attempt["superseded"]):
            raise AppError("recovery_attempt_not_applicable", status=409)
        return {"attempt_id": attempt_id}

    async def update_dependency_on(self, connection, identity, *, owner, expected_version,
                                   expected_dependency, new_dependency):
        """Move a lineage to another compatible dependency without resetting budget."""
        if not isinstance(new_dependency, str) or not new_dependency:
            raise ValueError("invalid_recovery_dependency")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (identity,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        if row["dependency"] != expected_dependency:
            raise AppError("recovery_dependency_changed", status=409)
        if row["dependency"] == new_dependency:
            return row
        active = await (await connection.execute(
            "SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=%s AND a.status IN ('reserved','unknown') "
            "AND NOT EXISTS (SELECT 1 FROM runtime.recovery_attempts newer WHERE newer.supersedes_attempt_id=a.attempt_id) "
            "AND NOT (a.status='unknown' AND a.attempt_number=%s AND a.version=%s AND %s='pure_computation' "
            "AND EXISTS (SELECT 1 FROM runtime.recovery_computation_unknowns u WHERE u.attempt_id=a.attempt_id)) LIMIT 1",
            (identity, row["attempts"], expected_version, row["recovery_kind"]))).fetchone()
        if active:
            raise AppError("recovery_original_not_settled", status=409)
        await connection.execute(
            "INSERT INTO runtime.recovery_dependency_changes(lineage_id,previous_dependency,new_dependency,version) "
            "VALUES(%s,%s,%s,%s)", (identity, expected_dependency, new_dependency, expected_version))
        return await (await connection.execute(
            "UPDATE runtime.recovery_lineages SET dependency=%s,claim_token=NULL,claim_expires_at=NULL,"
            "continuation_attempt_id=NULL,continuation_reason=NULL WHERE lineage_id=%s RETURNING *",
            (new_dependency, identity))).fetchone()

    async def resume_repaired_on(self, connection, lineage_id, *, owner, expected_version,
                                 original_attempt_id, model_receipt, repair_ref):
        """Give a known failed original one fenced same-version repair wake.

        The caller owns the repair evidence, frozen request digest comparison,
        current business head and source/permission checks in this transaction.
        A repair never settles, retries or replaces the physical attempt itself.
        """
        if (not isinstance(repair_ref, str) or not repair_ref.strip() or len(repair_ref) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in repair_ref)):
            raise ValueError("invalid_recovery_repair_ref")
        if not isinstance(original_attempt_id, str) or not original_attempt_id:
            raise ValueError("invalid_recovery_attempt")

        def decision(reason=None, retry_at=None):
            return {"eligible": reason is None, "reason": reason, "lineage_id": lineage_id,
                    "retry_at": retry_at, "repair_ref": repair_ref}

        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
        facts = await lock_recovery_facts_on(connection, original_attempt_id)
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (lineage_id,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if row["current_version"] != expected_version:
            raise AppError("recovery_version_changed", status=409)
        if row["source_withdrawn"]:
            return decision("recovery_source_withdrawn")
        if row["cancelled"]:
            return decision("recovery_cancelled")
        if row["paused"]:
            return decision("recovery_paused")

        attempt = await (await connection.execute(
            "SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s FOR UPDATE",
            (original_attempt_id,))).fetchone()
        if not attempt or attempt["lineage_id"] != lineage_id:
            raise AppError("recovery_attempt_not_active", status=409)
        if attempt["version"] != expected_version:
            raise AppError("recovery_version_changed", status=409)
        if not isinstance(model_receipt, ModelReceipt) or model_receipt.attempt_id != original_attempt_id:
            raise AppError("recovery_model_receipt_mismatch", status=409)
        if model_receipt.data_generation != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if model_receipt.status != "failed":
            return decision("recovery_model_not_failed")
        if model_receipt.cancellation_reason == "explicit_cancel":
            raise AppError("recovery_model_cancelled", status=409)
        if (not isinstance(model_receipt.finished_at, datetime) or model_receipt.finished_at.utcoffset() is None
                or not isinstance(model_receipt.intent_id, str) or not model_receipt.intent_id
                or not isinstance(model_receipt.request_digest, str) or not model_receipt.request_digest):
            return decision("recovery_model_receipt_unconfirmed")

        previous = await (await connection.execute(
            "SELECT attempt_id,attempt_version,model_intent_id,model_request_digest,models_finished_at "
            "FROM runtime.recovery_repairs WHERE lineage_id=%s AND repair_ref=%s",
            (lineage_id, repair_ref))).fetchone()
        if previous and (previous["attempt_id"] != original_attempt_id
                or previous["attempt_version"] != expected_version
                or previous["model_intent_id"] != model_receipt.intent_id
                or previous["model_request_digest"] != model_receipt.request_digest
                or previous["models_finished_at"] != model_receipt.finished_at):
            raise AppError("recovery_repair_ref_conflict", status=409)

        latest = await (await connection.execute(
            "SELECT attempt_id,attempt_number,status FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number DESC LIMIT 1", (lineage_id,))).fetchone()
        if (not latest or latest["attempt_id"] != original_attempt_id
                or latest["attempt_number"] != row["attempts"]):
            return decision("recovery_attempt_superseded")
        if attempt["status"] == "unknown":
            return decision("recovery_original_unknown")
        if attempt["status"] == "reserved":
            return decision("recovery_original_in_progress")
        if attempt["status"] != "failed":
            return decision("recovery_original_not_failed")
        active = await (await connection.execute(
            "SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=%s "
            "AND a.status IN ('reserved','unknown') AND NOT EXISTS "
            "(SELECT 1 FROM runtime.recovery_attempts newer WHERE newer.supersedes_attempt_id=a.attempt_id) LIMIT 1",
            (lineage_id,))).fetchone()
        if active:
            return decision("recovery_original_not_settled")
        reason = await _budget_reason_on(connection, row)
        if reason:
            return decision(reason)
        if row["claim_token"] and (row["claim_expires_at"] is None or row["claim_expires_at"] > row["now"]):
            return decision("recovery_claim_in_progress", row["claim_expires_at"])
        if previous and row["due_at"] is None:
            # The same authorization must never recreate a consumed wake.
            return decision("recovery_repair_already_consumed")

        # Models facts were locked before the Runtime lineage and remain held
        # until this domain transaction commits or rolls back.
        validate_model_recovery_facts(facts, receipt=model_receipt, expected_status="failed",
                                      data_generation=row["data_generation"])
        if not previous:
            await connection.execute(
                "INSERT INTO runtime.recovery_repairs(lineage_id,repair_ref,attempt_id,attempt_version,"
                "model_intent_id,model_request_digest,models_finished_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (lineage_id, repair_ref, original_attempt_id, expected_version,
                 model_receipt.intent_id, model_receipt.request_digest, model_receipt.finished_at))
            if row["due_at"] is None:
                row = await (await connection.execute(
                    "UPDATE runtime.recovery_lineages SET due_at=now() WHERE lineage_id=%s RETURNING *,now() AS now",
                    (lineage_id,))).fetchone()
        budget = await _budget_mapping_on(connection, row)
        return decision(retry_at=budget["retry_at"])

    async def update_version_on(self, connection, identity, *, owner, expected_version,
                                current_version, paused=None, cancelled=None,
                                source_withdrawn=None, resume_due=False):
        """CAS a domain/config version; optionally schedule one repaired failure.

        The owner supplies actual version identities and records source withdrawal
        when authoritative source state changes. A caller must also revalidate
        current sources before applying any eventual result.
        """
        if (not isinstance(expected_version, str) or not expected_version
                or not isinstance(current_version, str) or not current_version
                or any(value is not None and type(value) is not bool
                       for value in (paused, cancelled, source_withdrawn))
                or type(resume_due) is not bool):
            raise ValueError("invalid_recovery_version_update")
        if resume_due and expected_version == current_version:
            raise AppError("recovery_version_unchanged", status=409)
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (identity,))).fetchone()
        if (not row or not generation or row["owner"] != owner or row["current_version"] != expected_version
                or row["data_generation"] != generation["id"]):
            raise AppError("recovery_version_changed", status=409)
        if resume_due:
            latest = await (await connection.execute(
                "SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s "
                "ORDER BY attempt_number DESC LIMIT 1", (identity,))).fetchone()
            active = await (await connection.execute(
                "SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=%s "
                "AND a.status IN ('reserved','unknown') AND NOT EXISTS "
                "(SELECT 1 FROM runtime.recovery_attempts newer WHERE newer.supersedes_attempt_id=a.attempt_id) LIMIT 1",
                (identity,))).fetchone()
            if (row["paused"] or row["cancelled"] or row["source_withdrawn"]
                    or paused is True or cancelled is True or source_withdrawn is True
                    or await _budget_reason_on(connection, row) or not latest
                    or latest["status"] != "failed" or active):
                raise AppError("recovery_resume_not_eligible", status=409)
        return await (await connection.execute(
            "UPDATE runtime.recovery_lineages SET current_version=%s,paused=COALESCE(%s,paused),"
            "cancelled=COALESCE(%s,cancelled),source_withdrawn=COALESCE(%s,source_withdrawn),"
            "due_at=CASE WHEN %s THEN now() ELSE due_at END,"
            "claim_token=NULL,claim_expires_at=NULL,continuation_attempt_id=NULL,continuation_reason=NULL "
            "WHERE lineage_id=%s RETURNING *",
            (current_version, paused, cancelled, source_withdrawn, resume_due, identity))).fetchone()

    async def claim_continuation_on(self, connection, identity, *, owner, expected_version,
                                    prior_attempt_id, reason, lease_seconds=30):
        """Fence a legitimate next request after a physically successful attempt.

        Domain code validates the original result, current sources and any
        dependency-closed split; this method preserves outcome and budget.
        """
        if reason not in CONTINUATION_REASONS or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_recovery_continuation")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (identity,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        await _require_budget_on(connection, row)
        previous = await (await connection.execute(
            "SELECT attempt_id,status FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number DESC LIMIT 1", (identity,))).fetchone()
        if not previous or previous["attempt_id"] != prior_attempt_id or previous["status"] != "succeeded":
            raise AppError("recovery_continuation_original_not_succeeded", status=409)
        active = await (await connection.execute(
            "SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=%s AND a.status IN ('reserved','unknown') "
            "AND NOT EXISTS (SELECT 1 FROM runtime.recovery_attempts newer WHERE newer.supersedes_attempt_id=a.attempt_id) LIMIT 1",
            (identity,))).fetchone()
        if active:
            raise AppError("recovery_original_not_failed", status=409)
        if row["claim_token"] and row["claim_expires_at"] and row["claim_expires_at"] > row["now"]:
            if row["continuation_attempt_id"] == prior_attempt_id and row["continuation_reason"] == reason:
                return {"lineage_id": identity, "claim_token": row["claim_token"],
                        "prior_attempt_id": prior_attempt_id, "reason": reason}
            raise RecoveryWait("recovery_claim_in_progress", row["claim_expires_at"], dependency=row["dependency"])
        token = str(uuid4())
        await connection.execute(
            "UPDATE runtime.recovery_lineages SET claim_token=%s,claim_expires_at=now()+(%s*interval '1 second'),"
            "continuation_attempt_id=%s,continuation_reason=%s WHERE lineage_id=%s",
            (token, lease_seconds, prior_attempt_id, reason, identity))
        return {"lineage_id": identity, "claim_token": token, "prior_attempt_id": prior_attempt_id, "reason": reason}

    async def reserve_attempt_on(self, connection, identity, *, owner, expected_version,
                                 attempt_id, request_digest, route, original_state, claim_token=None,
                                 model_receipt=None):
        if not all(isinstance(value, str) and value for value in (attempt_id, request_digest, route)):
            raise ValueError("invalid_recovery_attempt")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        facts = None
        if original_state in {"failed", "unknown"} and model_receipt is not None:
            if not isinstance(model_receipt, ModelReceipt) or not model_receipt.attempt_id:
                raise AppError("recovery_model_receipt_mismatch", status=409)
            from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
            facts = await lock_recovery_facts_on(connection, model_receipt.attempt_id)
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (identity,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        existing = await (await connection.execute(
            "SELECT * FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if existing:
            if (existing["lineage_id"] != identity or existing["request_digest"] != request_digest
                    or existing["route"] != route):
                raise AppError("recovery_attempt_conflict", status=409)
            if original_state == "unknown" and existing["supersedes_attempt_id"]:
                if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                        or row["source_withdrawn"] or existing["version"] != expected_version):
                    raise AppError("recovery_version_changed", status=409)
                _require_model_unknown(model_receipt, attempt_id=existing["supersedes_attempt_id"],
                                       generation=row["data_generation"])
            return AttemptReservation(attempt_id, identity, existing["attempt_number"], existing["status"],
                                      attempt_id if existing["dependency_probe"] else None, existing["dependency"])
        if (row["current_version"] != expected_version or row["paused"] or row["cancelled"]
                or row["source_withdrawn"]):
            raise AppError("recovery_version_changed", status=409)
        if original_state not in ({"none"} if row["attempts"] == 0 else {"failed", "succeeded", "unknown"}):
            raise AppError("recovery_original_not_failed", status=409)
        if original_state == "unknown" and row["recovery_kind"] != "pure_computation":
            raise AppError("recovery_original_not_failed", status=409)
        await _require_budget_on(connection, row)
        if row["attempts"] and (not claim_token or row["claim_token"] != claim_token
                                or row["claim_expires_at"] is None or row["claim_expires_at"] <= row["now"]):
            retry_at = (row["claim_expires_at"] if row["claim_token"] and row["claim_expires_at"]
                        and row["claim_expires_at"] > row["now"]
                        else row["due_at"] if row["due_at"] and row["due_at"] > row["now"]
                        else row["now"] + timedelta(seconds=10))
            raise RecoveryWait("recovery_claim_lost", retry_at, dependency=row["dependency"])
        previous = None
        if row["attempts"]:
            previous = await (await connection.execute(
                "SELECT attempt_id,status,version FROM runtime.recovery_attempts WHERE lineage_id=%s "
                "ORDER BY attempt_number DESC LIMIT 1", (identity,))).fetchone()
            if not previous or previous["status"] != original_state:
                raise AppError("recovery_original_not_failed", status=409)
            if original_state == "succeeded" and (
                row["continuation_attempt_id"] != previous["attempt_id"]
                or row["continuation_reason"] not in CONTINUATION_REASONS
            ):
                raise AppError("recovery_continuation_claim_required", status=409)
            if original_state == "unknown":
                if previous["version"] != expected_version:
                    raise AppError("recovery_version_changed", status=409)
                if row["due_at"] is None or row["due_at"] > row["now"]:
                    if row["due_at"] is None:
                        raise AppError("recovery_not_due", status=409)
                    raise RecoveryWait("recovery_not_due", row["due_at"], dependency=row["dependency"])
                recorded = await (await connection.execute(
                    "SELECT 1 FROM runtime.recovery_computation_unknowns WHERE attempt_id=%s",
                    (previous["attempt_id"],))).fetchone()
                if not recorded:
                    raise AppError("recovery_model_unknown_unconfirmed", status=409)
                _require_model_unknown(model_receipt, attempt_id=previous["attempt_id"],
                                       generation=row["data_generation"])
        active = await (await connection.execute(
            "SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=%s AND a.status IN ('reserved','unknown') "
            "AND (%s::text IS NULL OR a.attempt_id<>%s) AND NOT EXISTS "
            "(SELECT 1 FROM runtime.recovery_attempts newer WHERE newer.supersedes_attempt_id=a.attempt_id) LIMIT 1",
            (identity, previous["attempt_id"] if original_state == "unknown" else None,
             previous["attempt_id"] if original_state == "unknown" else None))).fetchone()
        if active:
            raise AppError("recovery_original_not_failed", status=409)
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                                 (row["data_generation"], row["dependency"]))
        dependency = await (await connection.execute(
            "SELECT *,clock_timestamp() AS now FROM runtime.recovery_dependencies WHERE data_generation=%s AND dependency=%s FOR UPDATE",
            (row["data_generation"], row["dependency"]))).fetchone()
        probe = None
        if dependency["cooldown_until"] and dependency["cooldown_until"] > dependency["now"]:
            raise RecoveryWait("recovery_dependency_cooling", dependency["cooldown_until"],
                               dependency=row["dependency"])
        if (dependency["probe_token"] and dependency["probe_expires_at"] is not None
                and dependency["probe_expires_at"] > dependency["now"]):
            raise RecoveryWait("recovery_dependency_probe_in_progress", _probe_recheck_at(dependency),
                               dependency=row["dependency"])
        if dependency["cooldown_until"] is not None or dependency["probe_token"]:
            probe = attempt_id
        if previous and original_state in {"failed", "unknown"} and model_receipt is not None:
            if not isinstance(model_receipt, ModelReceipt) or model_receipt.attempt_id != previous["attempt_id"]:
                raise AppError("recovery_model_receipt_mismatch", status=409)
            # A consumer interruption is a known stopped pure computation,
            # while the original Models receipt still truthfully says cancelled.
            interruption = original_state == "failed" and _consumer_interruption(model_receipt)
            if interruption and row["recovery_kind"] != "pure_computation":
                raise AppError("recovery_original_not_failed", status=409)
            # Facts were locked before the Runtime lineage, not after it.
            validate_model_recovery_facts(facts, receipt=model_receipt,
                expected_status="cancelled" if interruption else original_state,
                data_generation=row["data_generation"])
        inserted = await (await connection.execute(
            "INSERT INTO runtime.recovery_attempts(attempt_id,lineage_id,request_digest,route,dependency,version,attempt_number,dependency_probe,continuation_of,continuation_reason,supersedes_attempt_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING attempt_id",
            (attempt_id, identity, request_digest, route, row["dependency"], expected_version, row["attempts"] + 1, bool(probe),
             row["continuation_attempt_id"] if original_state == "succeeded" else None,
             row["continuation_reason"] if original_state == "succeeded" else None,
             previous["attempt_id"] if original_state == "unknown" else None))).fetchone()
        if not inserted:
            raise AppError("recovery_attempt_conflict", status=409)
        if probe:
            updated = await (await connection.execute(
                "UPDATE runtime.recovery_dependencies SET probe_token=%s,probe_expires_at=clock_timestamp()+interval '60 seconds' "
                "WHERE data_generation=%s AND dependency=%s AND probe_token IS NOT DISTINCT FROM %s "
                "AND probe_expires_at IS NOT DISTINCT FROM %s RETURNING dependency",
                (probe, row["data_generation"], row["dependency"], dependency["probe_token"],
                 dependency["probe_expires_at"]))).fetchone()
            if not updated:
                raise AppError("recovery_dependency_changed", status=409)
        await connection.execute("UPDATE runtime.recovery_lineages SET attempts=attempts+1,due_at=NULL,claim_token=NULL,claim_expires_at=NULL,continuation_attempt_id=NULL,continuation_reason=NULL WHERE lineage_id=%s",
                                 (identity,))
        return AttemptReservation(attempt_id, identity, row["attempts"] + 1, "reserved", probe, row["dependency"])

    async def fail_attempt_on(self, connection, attempt_id, *, code, category, retry_after=None,
                              probe_token=None, random_value=None):
        category = FailureCategory(category)
        if code is not None and not isinstance(code, str):
            raise ValueError("invalid_recovery_error_code")
        retry_after_seconds(retry_after)
        if random_value is not None and not 0 <= random_value <= 1:
            raise ValueError("invalid_jitter")
        original = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not original:
            raise AppError("recovery_attempt_not_active", status=409)
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (original["lineage_id"],))).fetchone()
        attempt = await (await connection.execute(
            "UPDATE runtime.recovery_attempts SET status=%s,error_code=%s,finished_at=now() "
            "WHERE attempt_id=%s AND status='reserved' RETURNING *",
            ("unknown" if category == FailureCategory.UNKNOWN_EFFECT else "failed", code, attempt_id))).fetchone()
        if not attempt:
            raise AppError("recovery_attempt_not_active", status=409)
        probe_token = attempt_id if attempt["dependency_probe"] else None
        await connection.execute(
            "INSERT INTO runtime.recovery_attempt_observations(attempt_id,status,error_code,observation_ref) "
            "VALUES(%s,%s,%s,%s)", (attempt_id, attempt["status"], code, attempt["observation_ref"]))
        can_retry = (_automatic_failure(row, category)
                     and not await _budget_reason_on(connection, row)
                     and not row["paused"]
                     and not row["cancelled"] and not row["source_withdrawn"]
                     and row["current_version"] == attempt["version"])
        due = retry_due(row["attempts"], retry_after=retry_after, now=row["now"], random_value=random_value) if can_retry else None
        withdrawn = category == FailureCategory.SOURCE_INVALID and any(
            marker in (code or "").lower() for marker in ("withdraw", "retract", "revoked", "forgotten"))
        await connection.execute(
            "UPDATE runtime.recovery_lineages SET due_at=%s,failure_category=%s,"
            "source_withdrawn=source_withdrawn OR %s WHERE lineage_id=%s",
            (due, category.value, withdrawn, row["lineage_id"]))
        if category in {FailureCategory.TRANSPORT, FailureCategory.INFRASTRUCTURE}:
            # Even an exhausted lineage cools the dependency; other lineages
            # must not multiply a provider outage into a request storm.
            until = due or retry_due(row["attempts"], retry_after=retry_after, now=row["now"], random_value=random_value)
            await connection.execute(
                "UPDATE runtime.recovery_dependencies SET cooldown_until=GREATEST(cooldown_until,%s),"
                "probe_token=CASE WHEN probe_token=%s THEN NULL ELSE probe_token END,"
                "probe_expires_at=CASE WHEN probe_token=%s THEN NULL ELSE probe_expires_at END "
                "WHERE data_generation=%s AND dependency=%s AND (NOT %s OR probe_token=%s)",
                (until, probe_token, probe_token, row["data_generation"], attempt["dependency"],
                 attempt["dependency_probe"], attempt_id))
        elif attempt["dependency_probe"] and category != FailureCategory.UNKNOWN_EFFECT:
            # A definitive non-transport failure releases only this probe lease;
            # it does not erase a separately recorded dependency cooldown.
            await connection.execute(
                "UPDATE runtime.recovery_dependencies SET probe_token=NULL,probe_expires_at=NULL "
                "WHERE data_generation=%s AND dependency=%s AND probe_token=%s",
                (row["data_generation"], attempt["dependency"], attempt_id))
        return due

    async def succeed_attempt_on(self, connection, attempt_id, *, probe_token=None):
        original = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not original:
            raise AppError("recovery_attempt_not_active", status=409)
        await connection.execute("SELECT 1 FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
                                 (original["lineage_id"],))
        attempt = await (await connection.execute(
            "UPDATE runtime.recovery_attempts SET status='succeeded',finished_at=now() "
            "WHERE attempt_id=%s AND status='reserved' RETURNING lineage_id,dependency_probe,observation_ref,dependency", (attempt_id,))).fetchone()
        if not attempt:
            raise AppError("recovery_attempt_not_active", status=409)
        probe_token = attempt_id if attempt["dependency_probe"] else None
        await connection.execute(
            "INSERT INTO runtime.recovery_attempt_observations(attempt_id,status,observation_ref) VALUES(%s,'succeeded',%s)",
            (attempt_id, attempt["observation_ref"]))
        row = await (await connection.execute(
            "UPDATE runtime.recovery_lineages SET due_at=NULL,failure_category=NULL WHERE lineage_id=%s "
            "RETURNING dependency,data_generation", (attempt["lineage_id"],))).fetchone()
        if probe_token is not None:
            await connection.execute(
                "UPDATE runtime.recovery_dependencies SET cooldown_until=NULL,probe_token=NULL,probe_expires_at=NULL "
                "WHERE data_generation=%s AND dependency=%s AND probe_token=%s",
                (row["data_generation"], attempt["dependency"], probe_token))

    async def resolve_unknown_on(self, connection, attempt_id, *, observation_ref, state,
                                 code=None, category=None, retry_after=None, probe_token=None):
        """Record a later authoritative observation before considering retry."""
        if not isinstance(observation_ref, str) or not observation_ref or state not in {"failed", "succeeded"}:
            raise ValueError("definitive_recovery_observation_required")
        if state == "failed" and (category is None or FailureCategory(category) == FailureCategory.UNKNOWN_EFFECT):
            raise ValueError("definitive_failure_category_required")
        if code is not None and not isinstance(code, str):
            raise ValueError("invalid_recovery_error_code")
        retry_after_seconds(retry_after)
        original = await (await connection.execute(
            "SELECT lineage_id FROM runtime.recovery_attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        if not original:
            raise AppError("recovery_attempt_not_unknown", status=409)
        lineage = await (await connection.execute(
            "SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE", (original["lineage_id"],))).fetchone()
        attempt = await (await connection.execute(
            "UPDATE runtime.recovery_attempts SET status='reserved',observation_ref=%s "
            "WHERE attempt_id=%s AND status='unknown' RETURNING *", (observation_ref, attempt_id))).fetchone()
        if not attempt:
            raise AppError("recovery_attempt_not_unknown", status=409)
        if attempt["attempt_number"] < lineage["attempts"]:
            # A later attempt owns the lineage, schedule and dependency probe.
            # Settle this original receipt only; it cannot change the successor.
            await connection.execute(
                "UPDATE runtime.recovery_attempts SET status=%s,error_code=%s,finished_at=now() WHERE attempt_id=%s",
                (state, code if state == "failed" else attempt["error_code"], attempt_id))
            await connection.execute(
                "INSERT INTO runtime.recovery_attempt_observations(attempt_id,status,error_code,observation_ref) "
                "VALUES(%s,%s,%s,%s)", (attempt_id, state, code if state == "failed" else None, observation_ref))
            return None
        if state == "succeeded":
            await self.succeed_attempt_on(connection, attempt_id, probe_token=probe_token)
            return None
        return await self.fail_attempt_on(connection, attempt_id, code=code, category=category,
                                          retry_after=retry_after, probe_token=probe_token)

    async def claim_due_on(self, connection, *, owner, limit=64, lease_seconds=30):
        """Round-robin domains/subjects among due lineages, with short fenced claims."""
        if type(limit) is not int or not 1 <= limit <= 512 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_recovery_claim")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        if not generation:
            return ()
        rows = await (await connection.execute(
            "WITH eligible AS (SELECT l.lineage_id,l.domain,l.subject_id,l.due_at FROM runtime.recovery_lineages l "
            "JOIN LATERAL (SELECT count(*) AS amount FROM runtime.recovery_not_sent e "
            "WHERE e.lineage_id=l.lineage_id) n ON true "
            "WHERE l.owner=%s AND l.data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
            "AND l.due_at<=now() AND NOT l.paused AND NOT l.cancelled AND NOT l.source_withdrawn "
            "AND l.attempts-n.amount<l.max_attempts AND n.amount<%s "
            "AND NOT EXISTS (SELECT 1 FROM runtime.recovery_attempts a WHERE a.lineage_id=l.lineage_id "
            "AND a.attempt_number=l.attempts AND a.status='unknown' AND a.version<>l.current_version) "
            "AND (claim_token IS NULL OR claim_expires_at<=now())), "
            "subjects AS (SELECT domain,subject_id,min(due_at) AS oldest FROM eligible GROUP BY domain,subject_id), "
            "subject_rank AS (SELECT *,row_number() OVER (PARTITION BY domain ORDER BY oldest,subject_id) AS position FROM subjects), "
            "ranked AS (SELECT e.*,row_number() OVER (PARTITION BY e.domain,e.subject_id ORDER BY e.due_at,e.lineage_id) AS item_position "
            "FROM eligible e) SELECT l.* FROM runtime.recovery_lineages l JOIN ranked r USING(lineage_id) "
            "JOIN subject_rank s ON s.domain=r.domain AND s.subject_id=r.subject_id "
            "ORDER BY r.item_position,s.position,r.due_at,l.lineage_id LIMIT %s FOR UPDATE OF l SKIP LOCKED",
            (owner, MAX_CONNECTION_ATTEMPTS, limit))).fetchall()
        claimed = []
        for row in rows:
            # Recheck after obtaining the lineage lock: a concurrent receipt
            # classification can exhaust the connection budget while we wait.
            if await _budget_reason_on(connection, row):
                continue
            token = str(uuid4())
            updated = await (await connection.execute(
                "UPDATE runtime.recovery_lineages SET claim_token=%s,claim_expires_at=now()+(%s * interval '1 second') "
                ",continuation_attempt_id=NULL,continuation_reason=NULL "
                "WHERE lineage_id=%s RETURNING *", (token, lease_seconds, row["lineage_id"]))).fetchone()
            claimed.append(updated)
        return tuple(claimed)

    async def claim_lineage_due_on(self, connection, lineage_id, *, owner, lease_seconds=30):
        """Claim only the requested due lineage; do not reserve other owners' work."""
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_recovery_claim")
        generation = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        row = await (await connection.execute(
            "SELECT *,now() AS now FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
            (lineage_id,))).fetchone()
        if not row or row["owner"] != owner:
            raise AppError("recovery_owner_mismatch", status=409)
        if not generation or generation["id"] != row["data_generation"]:
            raise AppError("job_generation_expired", status=409)
        if (row["paused"] or row["cancelled"] or row["source_withdrawn"]
                or await _budget_reason_on(connection, row)
                or row["due_at"] is None or row["due_at"] > row["now"]
                or (row["claim_token"] and row["claim_expires_at"] > row["now"])):
            return None
        latest = await (await connection.execute(
            "SELECT status,version FROM runtime.recovery_attempts WHERE lineage_id=%s "
            "ORDER BY attempt_number DESC LIMIT 1", (lineage_id,))).fetchone()
        if latest and latest["status"] == "unknown" and latest["version"] != row["current_version"]:
            return None
        token = str(uuid4())
        updated = await (await connection.execute(
            "UPDATE runtime.recovery_lineages SET claim_token=%s,claim_expires_at=now()+(%s*interval '1 second'),"
            "continuation_attempt_id=NULL,continuation_reason=NULL WHERE lineage_id=%s RETURNING *",
            (token, lease_seconds, lineage_id))).fetchone()
        return updated
