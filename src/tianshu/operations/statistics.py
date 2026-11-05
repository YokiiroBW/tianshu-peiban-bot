"""Revisable, transactionally maintained aggregates of authoritative changes.

Owners call record_on in the same transaction as their own state change. Event
IDs are deduplication keys, never aggregation dimensions. NULL means unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


FAMILIES = frozenset({"channels", "messages", "models", "memory", "growth", "life", "media", "recovery"})
PRODUCERS = frozenset({"channels", "conversation", "models", "memory", "growth", "life", "media", "operations", "capabilities"})
REQUIRED_PRODUCERS = {
    "channels.ingress": ("channels.ingress",), "channels.outbound": ("channels.outbound",),
    "channels.media": ("channels.media",), "conversation.messages": ("conversation.messages",),
    "conversation.delivery": ("conversation.delivery",), "models": ("models",),
    "memory": ("memory",), "growth": ("growth",),
    "life.planning": ("life.planning", "life.plan"), "life.experience": ("life.experience",),
    "media": ("capabilities.media.jobs", "media"), "operations": ("operations",),
}
PRODUCER_SOURCES = {
    "channels.ingress": "channels.events",
    "channels.outbound": "channels.calls",
    "channels.media": "channels.media_supplements",
    "conversation.messages": "conversation.turns",
    "conversation.delivery": "conversation.expressions",
    "life.planning": "life.plan_generations",
}
METRICS = frozenset({
    "channel_ingress_accepted", "channel_ingress_processed", "channel_ingress_withdrawn",
    "channel_calls_total", "channel_calls_inflight", "channel_calls_confirmed",
    "channel_calls_rejected", "channel_calls_unknown",
    "received", "processed", "reply_required", "delivered", "withdrawn",
    "physical_attempts", "business_success", "input_tokens", "output_tokens",
    "estimated_input_tokens", "estimated_output_tokens",
    "cached_input_tokens", "cache_write_tokens", "reasoning_tokens",
    "actual_cost", "estimated_cost", "queue_ms", "execution_ms", "ttft_ms",
    "pool_wait_ms", "dns_ms", "connect_ms", "send_ms", "response_wait_ms",
    "read_ms", "effective_output_ms", "http_total_ms",
    "memory_applied", "memory_failed", "growth_material_blocked", "growth_review_blocked",
    "life_scheduled", "life_intentional_idle", "life_deferred_no_slot", "life_stale_state",
    "life_invalid_proposal", "uncovered_material_age_seconds", "media_completed",
    "media_failed", "recovery_eligible", "recovery_evaluated", "recovery_verified", "recovery_failed",
})
STATES = frozenset({"", "received", "processed", "reply_required", "delivered", "withdrawn",
                    "queued", "running", "succeeded", "failed", "cancelled", "unknown",
                    "applied", "blocked", "scheduled", "intentional_idle", "deferred_no_slot",
                    "stale_state", "invalid_proposal", "eligible", "verified"})
MODULES = frozenset({"channels", "conversation", "models", "memory", "growth", "life", "media",
                     "operations", "independent", "runtime"})


def _text(value, name, *, optional=False):
    if optional and value is None:
        return ""
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise AppError("invalid_statistic_" + name)
    return value


def _event(value):
    if not isinstance(value, dict):
        raise AppError("invalid_statistic_event")
    family = value.get("family")
    if family not in FAMILIES:
        raise AppError("invalid_statistic_family")
    revision = value.get("revision")
    if type(revision) is not int or revision < 1:
        raise AppError("invalid_statistic_revision")
    occurred = value.get("occurred_at")
    if isinstance(occurred, str):
        try:
            occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except ValueError:
            raise AppError("invalid_statistic_time") from None
    if not isinstance(occurred, datetime) or occurred.tzinfo is None:
        raise AppError("invalid_statistic_time")
    dimensions = value.get("dimensions") or {}
    if not isinstance(dimensions, dict) or set(dimensions) - {"state"}:
        raise AppError("invalid_statistic_dimensions")
    metrics = value.get("metrics")
    if not isinstance(metrics, dict) or not metrics or set(metrics) - METRICS:
        raise AppError("invalid_statistic_metrics")
    for item in metrics.values():
        if item is not None and (type(item) not in (int, float) or not math.isfinite(item) or item < 0):
            raise AppError("invalid_statistic_value")
    event = {"family": family, "event_id": _text(value.get("event_id"), "event_id"),
             "revision": revision, "occurred_at": occurred.astimezone(UTC),
             "module": _text(value.get("module"), "module"),
             "actor_id": _text(value.get("actor_id"), "actor", optional=True),
             "route_id": _text(value.get("route_id"), "route", optional=True),
             "state": _text(dimensions.get("state"), "state", optional=True),
             "metrics": metrics}
    if event["module"] not in MODULES or event["state"] not in STATES:
        raise AppError("invalid_statistic_dimensions")
    return event


def _digest(event):
    encoded = json.dumps(wire(event), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class StatisticsProjection:
    def __init__(self, database, diagnostics=None, resources=None, infrastructure=None):
        self.database, self.diagnostics = database, diagnostics
        self.resources = resources
        self.infrastructure = infrastructure

    @staticmethod
    async def _apply(conn, event, sign):
        for grain in ("minute", "hour"):
            for metric, value in event["metrics"].items():
                await conn.execute(
                    "INSERT INTO operations.statistic_buckets "
                    "(grain,bucket,family,module,actor_id,route_id,state,metric,total,samples,unknown) "
                    "VALUES(%s,date_trunc(%s,%s::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(grain,bucket,family,module,actor_id,route_id,state,metric) DO UPDATE "
                    "SET total=operations.statistic_buckets.total+excluded.total, "
                    "samples=operations.statistic_buckets.samples+excluded.samples, "
                    "unknown=operations.statistic_buckets.unknown+excluded.unknown",
                    (grain, grain, event["occurred_at"], event["family"], event["module"],
                     event["actor_id"], event["route_id"], event["state"], metric,
                     sign * (value if value is not None else 0), sign if value is not None else 0,
                     sign if value is None else 0))

    async def record_on(self, conn, value):
        event = _event(value)
        digest = _digest(event)
        # A rebuild takes the exclusive form; ordinary independent sources can
        # project concurrently, including transactions committed out of order.
        await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('operations.statistics',0))")
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                           ("operations.statistic:" + event["family"] + ":" + event["event_id"],))
        old = await (await conn.execute(
            "SELECT * FROM operations.statistic_events WHERE family=%s AND event_id=%s FOR UPDATE",
            (event["family"], event["event_id"]))).fetchone()
        if old and old["revision"] >= event["revision"]:
            if old["revision"] == event["revision"] and old["digest"] != digest:
                raise AppError("statistic_revision_conflict", status=409)
            return True  # Already covered, or a late older revision.
        if old:
            await self._apply(conn, old, -1)
        await self._apply(conn, event, 1)
        await conn.execute(
            "INSERT INTO operations.statistic_events "
            "(family,event_id,revision,digest,occurred_at,module,actor_id,route_id,state,metrics) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(family,event_id) DO UPDATE SET "
            "revision=excluded.revision,digest=excluded.digest,occurred_at=excluded.occurred_at,"
            "module=excluded.module,actor_id=excluded.actor_id,route_id=excluded.route_id,"
            "state=excluded.state,metrics=excluded.metrics,updated_at=now()",
            (event["family"], event["event_id"], event["revision"], digest, event["occurred_at"],
             event["module"], event["actor_id"], event["route_id"], event["state"], Jsonb(event["metrics"])))
        return True

    async def record_safely_on(self, conn, value):
        """Keep projection failure within a savepoint, so it cannot roll back business.

        A False result is a coverage gap, not proof that the source was projected;
        the owner must retain/replay its authoritative state and watermark.
        """
        try:
            async with conn.transaction():
                return await self.record_on(conn, value)
        except Exception:
            return False

    @staticmethod
    async def mark_producer_on(conn, producer, *, watermark=None, complete=False, detail=None):
        if (not isinstance(producer, str) or len(producer) > 120
                or not any(producer == name or producer.startswith(name + ".") for name in PRODUCERS)
                or type(complete) is not bool):
            raise AppError("invalid_statistic_producer")
        await conn.execute(
            "INSERT INTO operations.statistic_producers(producer,watermark,complete,detail) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(producer) DO UPDATE SET watermark=excluded.watermark,"
            "complete=excluded.complete,detail=excluded.detail,last_seen=now()",
            (producer, Jsonb(wire(watermark)), complete, Jsonb(wire(detail))))

    @staticmethod
    async def register_live_producer_on(conn, producer, *, source):
        """Register a live source without changing an existing backfill result."""
        if (not isinstance(producer, str) or len(producer) > 120
                or not any(producer == name or producer.startswith(name + ".") for name in PRODUCERS)):
            raise AppError("invalid_statistic_producer")
        detail = {"source": source, "mode": "live", "backfill": {"state": "not_started"}}
        row = await (await conn.execute(
            "INSERT INTO operations.statistic_producers(producer,watermark,complete,detail) "
            "VALUES(%s,NULL,false,%s) ON CONFLICT(producer) DO NOTHING RETURNING producer",
            (producer, Jsonb(detail)))).fetchone()
        return row is not None

    @staticmethod
    async def lock_producer_on(conn, producer, *, source):
        """Serialize a coverage check against concurrent failed live projections."""
        await StatisticsProjection.register_live_producer_on(conn, producer, source=source)
        return await (await conn.execute(
            "SELECT * FROM operations.statistic_producers WHERE producer=%s FOR UPDATE",
            (producer,))).fetchone()

    @staticmethod
    def matches_projection(value, stored):
        if not stored:
            return False
        event = _event(value)
        return stored.get("revision") == event["revision"] and stored.get("digest") == _digest(event)

    async def rebuild(self):
        async with self.database.transaction(control=True) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operations.statistics',0))")
            await conn.execute("DELETE FROM operations.statistic_buckets")
            cursor = await conn.execute("SELECT * FROM operations.statistic_events ORDER BY family,event_id")
            # Rebuild is an explicit administrative operation, never part of a page read.
            for event in await cursor.fetchall():
                await self._apply(conn, event, 1)

    async def query(self, body, *, context=None):
        if not isinstance(body, dict):
            raise AppError("invalid_input")
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        try:
            zone = ZoneInfo(body.get("timezone", "Asia/Shanghai"))
            end = _parse_time(body.get("end")) if body.get("end") else now
            start = _parse_time(body.get("start")) if body.get("start") else end - timedelta(days=1)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise AppError("invalid_statistics_range") from None
        start, end = (value.astimezone(UTC).replace(second=0, microsecond=0) for value in (start, end))
        if end <= start or end - start > timedelta(days=7):
            raise AppError("invalid_statistics_range")
        module = _text(body.get("module"), "module", optional=True)
        actor = _text(body.get("actor_id"), "actor", optional=True)
        route = _text(body.get("route_id"), "route", optional=True)
        condition = " AND (%s='' OR module=%s) AND (%s='' OR actor_id=%s) AND (%s='' OR route_id=%s)"
        params = (module, module, actor, actor, route, route)
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (await conn.execute(
                "SELECT date_trunc('hour',bucket AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket,family,module,actor_id,route_id,state,metric,"
                "sum(total) AS total,sum(samples) AS samples,sum(unknown) AS unknown "
                "FROM operations.statistic_buckets WHERE grain='minute' AND bucket >= %s AND bucket < %s" + condition +
                " GROUP BY 1,2,3,4,5,6,7 HAVING sum(samples)<>0 OR sum(unknown)<>0 ORDER BY 1",
                (start, end, *params))).fetchall()
            cumulative = await (await conn.execute(
                "SELECT metric,sum(total) AS total,sum(samples) AS samples,sum(unknown) AS unknown "
                "FROM operations.statistic_buckets WHERE grain='minute' AND bucket < %s" + condition +
                " GROUP BY metric HAVING sum(samples)<>0 OR sum(unknown)<>0", (end, *params))).fetchall()
            producers = await (await conn.execute("SELECT * FROM operations.statistic_producers ORDER BY producer")).fetchall()
        overview, series, breakdowns = {}, {}, {}
        for row in rows:
            metric = row["metric"]
            _merge(overview, metric, row)
            stamp = row["bucket"].astimezone(zone).isoformat()
            _merge(series.setdefault(stamp, {}), metric, row)
            for dimension in ("family", "module", "actor_id", "route_id", "state"):
                key = row[dimension] or "unassigned"
                _merge(breakdowns.setdefault(dimension, {}).setdefault(key, {}), metric, row)
        present = {row["producer"]: row for row in producers}
        missing = sorted(name for name, aliases in REQUIRED_PRODUCERS.items()
                         if not any(alias in present for alias in aliases))
        incomplete = sorted(name for name, row in present.items() if not row["complete"])
        resources = None
        if self.diagnostics is not None:
            try:
                resources = await self.diagnostics.resources()
            except Exception:
                resources = {"status": "unavailable"}
        if self.resources is not None:
            try:
                if resources is None:
                    resources = {}
                resources["prometheus"] = await self.resources.read()
            except Exception:
                resources["prometheus"] = {"status": "unavailable"}
        if self.infrastructure is not None and context is not None:
            if resources is None:
                resources = {}
            try:
                resources["host"] = await self.infrastructure.route("/api/v1/infrastructure/host", {}, context)
            except Exception:
                resources["host"] = {"status": "unavailable"}
        eligible, evaluated, verified = (overview.get(name, {}).get("total") for name in
                                          ("recovery_eligible", "recovery_evaluated", "recovery_verified"))
        missing_evaluation = sorted(module for module, metrics in breakdowns.get("module", {}).items()
                                    if {"recovery_eligible", "recovery_verified", "recovery_failed"} & metrics.keys()
                                    and "recovery_evaluated" not in metrics)
        recovery_rate = {"eligible": eligible, "evaluated": evaluated, "verified": verified,
                         "rate": verified/evaluated if not missing_evaluation and evaluated and verified is not None
                         and verified <= evaluated else None, "scope": "observed_evaluated_events",
                         "missing_evaluated": missing_evaluation}
        scopes = _producer_scopes(present)
        outcomes = _outcome_summary(rows, breakdowns, scopes)
        return {"range": {"start": start, "end": end, "timezone": str(zone), "resolution": "minute"},
                "overview": overview, "cumulative": {row["metric"]: _metric(row) for row in cumulative},
                "series": [{"time": key, "metrics": val} for key, val in series.items()],
                "breakdowns": breakdowns, "resources": resources, "recovery_rate": recovery_rate,
                "outcomes": outcomes,
                "freshness": {"source_watermarks": {row["producer"]: row["watermark"] for row in producers},
                              "source_last_seen": {row["producer"]: row["last_seen"] for row in producers}},
                "coverage": {"complete": not missing and not incomplete, "missing": missing,
                             "incomplete": incomplete, "producers": producers,
                             "producer_scopes": scopes,
                             "percentiles": "unavailable_without_distribution"},
                "as_of": datetime.now(UTC)}


def _producer_scopes(present):
    scopes = {}
    for name, aliases in REQUIRED_PRODUCERS.items():
        actual = next((alias for alias in aliases if alias in present), None)
        if actual is None:
            scopes[name] = {"producer": None, "source": PRODUCER_SOURCES.get(name),
                            "status": "not_registered", "complete": False,
                            "mode": "unknown", "backfill": {"state": "unknown"}}
            continue
        row = present[actual]
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        reason = detail.get("reason")
        mode = detail.get("mode") if detail.get("mode") in {"live", "batch"} else (
            "live" if reason == "live_producer" else "unknown")
        backfill = detail.get("backfill")
        if not isinstance(backfill, dict):
            if row["complete"]:
                state = "complete"
            elif reason == "live_producer":
                # The old producer exhausted a live scan but supplied neither a
                # finite boundary nor proof that failed writes were replayed.
                state = "scanned_unverified"
            elif reason in {"scan_in_progress", "plan_event_pending_replay"}:
                state = "pending"
            elif reason in {"replay_failed", "projection_failed"}:
                state = "failed"
            else:
                state = "unknown"
            backfill = {"state": state, "reason": reason}
        else:
            backfill = dict(backfill)
            if backfill.get("state") not in {"complete", "pending", "failed", "not_started", "scanned_unverified", "unknown"}:
                backfill["state"] = "unknown"
            if backfill["state"] == "complete" and not row["complete"]:
                backfill["state"] = "scanned_unverified"
        scopes[name] = {"producer": actual, "source": detail.get("source") or PRODUCER_SOURCES.get(name),
                        "status": "complete" if row["complete"] else "partial",
                        "complete": bool(row["complete"]), "mode": mode, "backfill": backfill,
                        "watermark": row.get("watermark"), "last_seen": row.get("last_seen")}
    return scopes


def _availability(metric):
    if metric is None:
        return {"total": None, "samples": 0, "unknown": 0, "knowledge": "not_observed"}
    known, unknown = metric["samples"], metric["unknown"]
    knowledge = "partial" if known and unknown else "known" if known else "unknown" if unknown else "not_observed"
    return {**metric, "knowledge": knowledge}


def _outcome_summary(rows, breakdowns, scopes):
    families = breakdowns.get("family", {})
    status_counts = {}
    for row in rows:
        if row["family"] == "models" and row["metric"] == "physical_attempts":
            state = row["state"] or "unknown"
            status_counts[state] = status_counts.get(state, 0) + int(row["samples"]) + int(row["unknown"])
    model_complete = scopes["models"]["complete"]
    model_metrics = families.get("models", {})
    business_families = ("messages", "memory", "growth", "life", "media")
    business_scopes = ("channels.ingress", "channels.outbound", "channels.media",
                       "conversation.messages", "conversation.delivery", "memory", "growth",
                       "life.planning", "life.experience", "media")
    return {
        "business": {"basis": "domain_projections", "complete": all(scopes[name]["complete"] for name in business_scopes),
                     "families": {name: families[name] for name in business_families if name in families}},
        "channel_transport": {"basis": "channels_event_and_call_ledgers",
                              "metrics": families.get("channels", {}),
                              "delivery": "not_proven_by_call_confirmation"},
        "recovery": {"basis": "domain_verified_recovery_observations",
                     "metrics": families.get("recovery", {}), "coverage": "see_producer_scopes"},
        "original_model_attempts": {"basis": "models_attempt_ledger_projection", "complete": model_complete,
                                    "total": sum(status_counts.values()) if status_counts or model_complete else None,
                                    "states": status_counts},
        "billing": {"basis": "models_attempt_ledger_projection", "currency": "unknown",
                    "input_tokens": _availability(model_metrics.get("input_tokens")),
                    "output_tokens": _availability(model_metrics.get("output_tokens")),
                    "actual_cost": _availability(model_metrics.get("actual_cost")),
                    "estimated_cost": _availability(model_metrics.get("estimated_cost"))},
    }


def _parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def _metric(row):
    return {"total": row["total"] if row["samples"] else None,
            "samples": int(row["samples"]), "unknown": int(row["unknown"])}


def _merge(target, metric, row):
    current = target.setdefault(metric, {"total": 0.0, "samples": 0, "unknown": 0})
    current["total"] = (current["total"] or 0) + (row["total"] or 0)
    current["samples"] += int(row["samples"])
    current["unknown"] += int(row["unknown"])
    if not current["samples"]:
        current["total"] = None
