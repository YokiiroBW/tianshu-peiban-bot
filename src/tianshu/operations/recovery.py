"""Narrow, registered domain commands with original-version verification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Awaitable, Callable
from uuid import uuid4

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from .incidents import recovery_target, target_id


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    """Domain-owned callbacks must check the supplied version in their own transaction.

    inspect returns eligible, version, material_id, material_digest, dependency,
    and original_state. invoke performs a single scoped command; observe reads its
    authoritative result and returns state=verified/failed/pending/unknown. invoke
    must call guard_on in its own transaction before the domain's version/CAS check.
    """

    name: str
    error_families: frozenset[str]
    inspect: Callable[..., Awaitable[dict]]
    invoke: Callable[..., Awaitable[dict]]
    observe: Callable[..., Awaitable[dict]]
    max_attempts: int = 3
    observation_only: bool = False
    unknown_observable: bool = False
    domain_managed: bool = False
    resolves_dependency: bool = False


class RecoveryExecutor:
    def __init__(self, database, policy, outbox, actions=(), statistics=None):
        self.database, self.policy, self.outbox = database, policy, outbox
        self.statistics = statistics
        self.actions = {}
        for action in actions:
            if action.name in self.actions or not 1 <= action.max_attempts <= 5:
                raise ValueError("invalid_recovery_registration")
            self.actions[action.name] = action

    async def _project(self, conn, row, *, phase, outcome):
        if self.statistics is None:
            return False
        await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('operations.recovery_projection',0))")
        incident = await (await conn.execute("SELECT first_seen FROM operations.incidents WHERE incident_id=%s",
                                             (row["incident_id"],))).fetchone()
        evaluated = outcome in {"verified", "failed"}
        metrics = {"recovery_eligible": 1, "recovery_evaluated": 1 if evaluated else 0,
                   "recovery_verified": 1 if outcome == "verified" else 0,
                   "recovery_failed": 1 if outcome == "failed" else 0}
        recorded = await self.statistics.record_safely_on(conn, {"family": "recovery", "event_id": row["lineage_id"],
            "revision": row["attempt_number"] * 10 + {"reserved": 1, "settled": 2, "reconciled": 3}[phase],
            "occurred_at": incident["first_seen"], "module": "operations",
            "dimensions": {"state": outcome if outcome in {"verified", "failed", "unknown"} else "eligible"},
            "metrics": metrics})
        if not recorded:
            try:
                async with conn.transaction():
                    await self.statistics.mark_producer_on(conn, "operations", complete=False,
                                                           detail={"reason": "recovery_projection_pending"})
            except Exception:
                pass
        return recorded

    async def replay_statistics(self):
        """Rebuild the Operations-owned projection after a missed savepoint."""
        if self.statistics is None:
            return
        async with self.database.transaction(control=True) as conn:
            producer = await (await conn.execute("SELECT complete FROM operations.statistic_producers "
                                                "WHERE producer='operations'")).fetchone()
            if producer and producer["complete"]:
                return
            # Operations controls are serialized only during this exceptional
            # repair; business domains and normal statistics stay independent.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operations.recovery_projection',0))")
            rows = await (await conn.execute(
                "SELECT DISTINCT ON (r.lineage_id) r.*,l.due_at AS lineage_due,a.observation_ref "
                "FROM operations.recoveries r LEFT JOIN runtime.recovery_lineages l ON l.lineage_id=r.lineage_id "
                "LEFT JOIN runtime.recovery_attempts a ON a.attempt_id=r.attempt_id "
                "WHERE r.policy_mode='reserved' AND r.lineage_id<>'' AND r.attempt_number IS NOT NULL "
                "ORDER BY r.lineage_id,r.attempt_number DESC,r.created_at DESC")).fetchall()
            complete = True
            for row in rows:
                outcome = row["state"] if row["state"] in {"verified", "failed", "unknown"} else "eligible"
                if outcome == "failed" and row["lineage_due"] is not None:
                    outcome = "eligible"
                phase = ("reconciled" if row["observation_ref"] else
                         "settled" if row["state"] in {"verified", "failed", "unknown"} else "reserved")
                if not await self._project(conn, row, phase=phase, outcome=outcome):
                    complete = False
                    break
            await self.statistics.mark_producer_on(conn, "operations", watermark=datetime.now(UTC), complete=complete,
                                                   detail={"source": "operations.recoveries", "scanned": len(rows)})

    def applicable(self, incident):
        return tuple(action.name for action in self.actions.values()
                     if incident["error_family"] in action.error_families)

    @staticmethod
    async def _target_state(conn, row, state):
        if row.get("target_id"):
            await conn.execute("UPDATE operations.incident_targets t SET state=%s FROM operations.incidents i "
                "WHERE t.incident_id=i.incident_id AND i.incident_id=%s AND i.revision=%s "
                "AND t.episode=i.episode AND t.target_id=%s",
                (state, row["incident_id"], row["incident_revision"], row["target_id"]))

    @staticmethod
    async def _resolve_incident(conn, row, action, observation):
        return await (await conn.execute(
            "UPDATE operations.incidents i SET state='resolved',resolved_at=now(),revision=revision+1,last_observation=%s "
            "WHERE i.incident_id=%s AND i.revision=%s AND i.state IN ('observing','escalated') "
            "AND (%s OR (%s::text IS NOT NULL AND i.unscoped_occurrences=0 AND NOT EXISTS "
            "(SELECT 1 FROM operations.incident_targets t WHERE t.incident_id=i.incident_id "
            "AND t.episode=i.episode AND t.state<>'verified'))) RETURNING i.*",
            (Jsonb(wire(observation)), row["incident_id"], row["incident_revision"],
             action.resolves_dependency, row.get("target_id")))).fetchone()

    @staticmethod
    async def guard_on(conn, attempt_id, owner_token):
        """Fence a domain mutation in its transaction; it is not authorization itself."""
        row = await (await conn.execute(
            "SELECT r.*,i.revision AS current_revision,i.state AS incident_state,"
            "(SELECT id FROM runtime.installation_generation WHERE singleton) AS current_generation "
            "FROM operations.recoveries r JOIN operations.incidents i USING(incident_id) "
            "WHERE r.attempt_id=%s FOR UPDATE OF r,i", (attempt_id,))).fetchone()
        if (not row or row["state"] not in {"executing", "observing"} or row["owner_token"] != owner_token
                or row["lease_until"] is None or row["incident_state"] == "resolved"
                or row["incident_revision"] != row["current_revision"]
                or not row["current_generation"] or row["data_generation"] != row["current_generation"]):
            raise AppError("recovery_owner_lost", status=409)
        valid = await (await conn.execute("SELECT %s::timestamptz>now() AS valid", (row["lease_until"],))).fetchone()
        if not valid["valid"]:
            raise AppError("recovery_owner_lost", status=409)
        if row["lineage_id"]:
            lineage = await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
                                               (row["lineage_id"],))).fetchone()
            if (not lineage or lineage["paused"] or lineage["cancelled"]
                    or lineage["data_generation"] != row["current_generation"]
                    or str(row["result"]["facts"]["version"]) != lineage["current_version"]):
                raise AppError("recovery_owner_lost", status=409)
        return row

    async def enqueue_automatic(self, incident, name, target=None):
        if name not in self.applicable(incident):
            raise AppError("recovery_action_unavailable", status=409)
        target = recovery_target(target or {})
        tid = target_id(target) if target else None
        async with self.database.transaction(control=True) as conn:
            row = await (await conn.execute("SELECT revision,state FROM operations.incidents WHERE incident_id=%s FOR UPDATE",
                                            (incident["incident_id"],))).fetchone()
            if not row or row["revision"] != incident["revision"] or row["state"] == "resolved":
                return None
            old = await (await conn.execute(
                "SELECT attempt_id FROM operations.recoveries WHERE incident_id=%s AND action=%s "
                "AND incident_revision=%s AND target_id IS NOT DISTINCT FROM %s ORDER BY created_at LIMIT 1",
                (incident["incident_id"], name, incident["revision"], tid))).fetchone()
            if old:
                return old["attempt_id"]
            attempt = "opsrec_" + uuid4().hex
            await conn.execute("INSERT INTO operations.recoveries(attempt_id,incident_id,lineage_id,incident_revision,action,target,target_id,state) "
                               "VALUES(%s,%s,'',%s,%s,%s,%s,'requested')",
                               (attempt, incident["incident_id"], incident["revision"], name, Jsonb(target), tid))
            return attempt

    async def claim_retries(self):
        async with self.database.transaction(control=True) as conn:
            candidates = await (await conn.execute(
                "SELECT DISTINCT l.lineage_id FROM runtime.recovery_lineages l JOIN operations.recoveries r "
                "ON r.lineage_id=l.lineage_id WHERE l.owner='operations' AND r.policy_mode='reserved' "
                "AND l.due_at<=now() AND NOT EXISTS (SELECT 1 FROM operations.recoveries a "
                "WHERE a.lineage_id=l.lineage_id AND a.state IN ('requested','inspecting','executing','observing')) "
                "ORDER BY l.lineage_id LIMIT 32")).fetchall()
            for candidate in candidates:
                claim = await self.policy.claim_lineage_due_on(conn, candidate["lineage_id"],
                                                               owner="operations", lease_seconds=120)
                if not claim:
                    continue
                prior = await (await conn.execute(
                    "SELECT * FROM operations.recoveries WHERE lineage_id=%s ORDER BY created_at DESC LIMIT 1",
                    (claim["lineage_id"],))).fetchone()
                if not prior:
                    continue
                await conn.execute("INSERT INTO operations.recoveries(attempt_id,incident_id,lineage_id,incident_revision,"
                                   "action,target,target_id,state,claim_token) VALUES(%s,%s,%s,%s,%s,%s,%s,'requested',%s)",
                                   ("opsrec_"+uuid4().hex, prior["incident_id"], claim["lineage_id"],
                                    prior["incident_revision"], prior["action"], Jsonb(prior["target"]), prior["target_id"], claim["claim_token"]))

    async def process_requested(self, *, limit=16):
        # Claim with a separate state so two operations processes cannot invoke
        # one request. An abandoned executing request is an unknown effect.
        async with self.database.transaction(control=True) as conn:
            await conn.execute("UPDATE operations.recoveries SET state='requested',owner_token=NULL,lease_until=NULL "
                               "WHERE state='inspecting' AND lease_until<=now()")
            abandoned = await (await conn.execute(
                "SELECT * FROM operations.recoveries WHERE state='executing' AND lease_until<=now() "
                "FOR UPDATE SKIP LOCKED")).fetchall()
            for item in abandoned:
                from tianshu.runtime.recovery_policy import FailureCategory
                if item["policy_mode"] == "reserved":
                    await self.policy.fail_attempt_on(conn, item["attempt_id"], code="recovery_owner_lost",
                        category=FailureCategory.UNKNOWN_EFFECT, probe_token=(item["result"] or {}).get("probe_token"))
                await conn.execute("UPDATE operations.recoveries SET state='unknown',owner_token=NULL,lease_until=NULL,"
                                   "finished_at=now() WHERE attempt_id=%s", (item["attempt_id"],))
                await self._target_state(conn, item, "unknown")
                if item["policy_mode"] == "reserved":
                    await self._project(conn, item, phase="settled", outcome="unknown")
            rows = await (await conn.execute(
                "SELECT r.*,i.dependency,i.error_family,i.version,i.summary,i.revision AS current_revision,"
                "i.state AS incident_state FROM operations.recoveries r JOIN operations.incidents i USING(incident_id) "
                "WHERE r.state='requested' AND r.due_at<=now() ORDER BY r.due_at,r.created_at LIMIT %s FOR UPDATE OF r SKIP LOCKED", (limit,))).fetchall()
            for row in rows:
                row["owner_token"] = uuid4().hex
                row["inspection_attempts"] += 1
                await conn.execute("UPDATE operations.recoveries SET state='inspecting',owner_token=%s,"
                                   "inspection_attempts=inspection_attempts+1,lease_until=now()+interval '120 seconds' WHERE attempt_id=%s",
                                   (row["owner_token"], row["attempt_id"]))
        outcomes = await asyncio.gather(*(self._execute(row) for row in rows), return_exceptions=True)
        if any(isinstance(outcome, Exception) for outcome in outcomes):
            raise RuntimeError("recovery_processing_failed")

    async def _set_state(self, attempt_id, owner_token, state, result=None):
        async with self.database.transaction(control=True) as conn:
            await conn.execute("UPDATE operations.recoveries SET state=%s,result=%s,finished_at=now() "
                               "WHERE attempt_id=%s AND owner_token=%s AND lease_until>now() AND state IN ('inspecting','executing')",
                               (state, Jsonb(wire(result)) if result is not None else None, attempt_id, owner_token))

    async def _reserve_domain_control_on(self, conn, row, action, facts, generation):
        """Bound wake/observe controls without reserving a domain physical attempt."""
        lineage_id = facts.get("lineage_id") or ""
        if (facts["original_state"] == "failed" and not lineage_id and not action.observation_only
                and action.name != "capability_reroute_pending"):
            raise AppError("domain_recovery_lineage_required", status=409)
        if lineage_id:
            from tianshu.runtime.recovery_policy import lineage_id as canonical_lineage_id
            if (not facts.get("policy_domain") or canonical_lineage_id(
                    facts["policy_domain"], facts["material_id"], generation=generation) != lineage_id):
                raise AppError("domain_recovery_lineage_changed", status=409)
            lineage = await (await conn.execute("SELECT *,now() AS observed_now FROM runtime.recovery_lineages "
                "WHERE lineage_id=%s FOR UPDATE", (lineage_id,))).fetchone()
            if (not lineage or lineage["data_generation"] != generation or lineage["paused"] or lineage["cancelled"]
                    or lineage["current_version"] != str(facts["version"])
                    or lineage["material_digest"] != facts["material_digest"]
                    or facts.get("policy_domain") and lineage["domain"] != facts["policy_domain"]
                    or facts.get("owner") and lineage["owner"] != facts["owner"]
                    or facts.get("subject_id") and lineage["subject_id"] != facts["subject_id"]
                    or facts.get("max_attempts") and lineage["max_attempts"] != facts["max_attempts"]):
                raise AppError("domain_recovery_lineage_changed", status=409)
            if lineage["dependency"] != facts["dependency"]:
                await self.policy.update_dependency_on(conn, lineage_id, owner=lineage["owner"],
                    expected_version=str(facts["version"]), expected_dependency=lineage["dependency"],
                    new_dependency=facts["dependency"])
            if not action.observation_only:
                previous = await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE lineage_id=%s "
                    "ORDER BY attempt_number DESC LIMIT 1", (lineage_id,))).fetchone()
                if not previous or previous["status"] != facts["original_state"]:
                    raise AppError("domain_recovery_not_eligible", status=409)
                if previous["status"] == "failed":
                    budget = await self.policy.computation_budget_on(
                        conn, lineage_id, owner=lineage["owner"],
                        expected_version=str(facts["version"]),
                    )
                    if not budget["can_retry"] or lineage["due_at"] is None:
                        raise AppError("domain_recovery_not_eligible", status=409)
                    due_at = budget["retry_at"] or lineage["due_at"]
                    if due_at > lineage["observed_now"]:
                        raise AppError("domain_recovery_not_due", status=409, details={"due_at": due_at})
                elif previous["status"] != "succeeded":
                    raise AppError("domain_recovery_not_eligible", status=409)
        identity = json.dumps([generation, facts.get("policy_domain") or action.name, facts["material_id"]],
                              ensure_ascii=False, separators=(",", ":"))
        control_id = hashlib.sha256(identity.encode()).hexdigest()
        await conn.execute("INSERT INTO operations.control_lineages(control_id,data_generation,material_digest,max_attempts) "
                           "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                           (control_id, generation, facts["material_digest"], action.max_attempts))
        control = await (await conn.execute("SELECT * FROM operations.control_lineages WHERE control_id=%s FOR UPDATE",
                                           (control_id,))).fetchone()
        if control["material_digest"] != facts["material_digest"] or control["max_attempts"] != action.max_attempts:
            raise AppError("recovery_control_conflict", status=409)
        active = await (await conn.execute("SELECT 1 FROM operations.recoveries WHERE control_id=%s "
                                          "AND state IN ('executing','observing') LIMIT 1", (control_id,))).fetchone()
        if active:
            raise AppError("recovery_control_active", status=409)
        if control["attempts"] >= control["max_attempts"]:
            raise AppError("recovery_control_budget_exhausted", status=409)
        await conn.execute("UPDATE operations.control_lineages SET attempts=attempts+1 WHERE control_id=%s", (control_id,))
        return lineage_id, control_id, control["attempts"] + 1

    async def _execute(self, row):
        action = self.actions.get(row["action"])
        if action is None or row["error_family"] not in action.error_families:
            await self._set_state(row["attempt_id"], row["owner_token"], "unavailable", {"reason": "recovery_action_unavailable"})
            return
        if row["incident_state"] == "resolved" or row["incident_revision"] != row["current_revision"]:
            await self._set_state(row["attempt_id"], row["owner_token"], "stale", {"reason": "incident_revision_changed"})
            return
        try:
            facts = await action.inspect(row, row["target"])
        except Exception as exc:
            if row["inspection_attempts"] < action.max_attempts:
                async with self.database.transaction(control=True) as conn:
                    await conn.execute("UPDATE operations.recoveries SET state='requested',owner_token=NULL,lease_until=NULL,"
                        "due_at=now()+(%s * interval '1 second'),result=%s "
                        "WHERE attempt_id=%s AND owner_token=%s AND lease_until>now() AND state='inspecting'",
                        (30 * row["inspection_attempts"], Jsonb({"reason": type(exc).__name__}),
                         row["attempt_id"], row["owner_token"]))
                return
            await self._set_state(row["attempt_id"], row["owner_token"], "unavailable", {"reason": type(exc).__name__})
            return
        required = {"eligible", "version", "material_id", "material_digest", "dependency", "original_state"}
        if not isinstance(facts, dict) or not required <= facts.keys() or not facts["eligible"]:
            await self._set_state(row["attempt_id"], row["owner_token"], "ineligible", {"reason": "domain_preconditions_not_met"})
            return
        if facts.get("paused") or facts.get("cancelled") or (facts["original_state"] == "unknown" and not action.observation_only):
            await self._set_state(row["attempt_id"], row["owner_token"], "ineligible", {"reason": "paused_cancelled_or_unknown"})
            return
        try:
            async with self.database.transaction(control=True) as conn:
                current = await (await conn.execute("SELECT revision,state FROM operations.incidents WHERE incident_id=%s FOR UPDATE",
                                                    (row["incident_id"],))).fetchone()
                if not current or current["revision"] != row["incident_revision"] or current["state"] == "resolved":
                    raise AppError("incident_revision_changed", status=409)
                generation = await (await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton FOR SHARE")).fetchone()
                if not generation:
                    raise AppError("installation_generation_missing", status=503)
                reservation, control_id = None, None
                if action.domain_managed or action.observation_only:
                    lineage_id, control_id, number = await self._reserve_domain_control_on(conn, row, action, facts, generation["id"])
                    mode, probe = "domain_managed", None
                else:
                    policy_domain = facts.get("policy_domain", "operations")
                    policy_owner = facts.get("policy_owner", "operations")
                    policy_subject = facts.get("policy_subject_id", facts["material_id"])
                    lineage = await self.policy.register_on(conn, domain=policy_domain, subject_id=policy_subject,
                        owner=policy_owner, material_id=facts["material_id"], material_digest=facts["material_digest"],
                        dependency=facts["dependency"], current_version=str(facts["version"]), max_attempts=facts.get("max_attempts", action.max_attempts))
                    lineage_id = lineage["lineage_id"]
                    if facts.get("lineage_id") and facts["lineage_id"] != lineage_id or row["lineage_id"] and row["lineage_id"] != lineage_id:
                        raise AppError("recovery_lineage_conflict", status=409)
                    claim_token = row["claim_token"] or facts.get("claim_token")
                    reservation = await self.policy.reserve_attempt_on(conn, lineage_id, owner=policy_owner,
                        expected_version=str(facts["version"]), attempt_id=row["attempt_id"],
                        request_digest=body_hash({"action": action.name, "target": row["target"], "version": facts["version"]}),
                        route=action.name, original_state="none" if not lineage["attempts"] else facts["original_state"],
                        claim_token=claim_token)
                    number, mode, probe = reservation.number, "reserved", reservation.probe_token
                claimed = await (await conn.execute("UPDATE operations.recoveries SET state='executing',lineage_id=%s,control_id=%s,"
                                   "data_generation=%s,policy_mode=%s,attempt_number=%s,result=%s "
                                   "WHERE attempt_id=%s AND state='inspecting' AND owner_token=%s AND lease_until>now() RETURNING attempt_id",
                                   (lineage_id, control_id, generation["id"], mode, number,
                                    Jsonb({"facts": wire(facts), "probe_token": probe}), row["attempt_id"], row["owner_token"]))).fetchone()
                if not claimed:
                    raise AppError("recovery_owner_lost", status=409)
                await conn.execute("UPDATE operations.incidents SET recovery_action=%s,recovery_lineage=%s,state='observing' "
                                   "WHERE incident_id=%s AND revision=%s", (action.name, lineage_id or None, row["incident_id"], row["incident_revision"]))
                await self._target_state(conn, row, "observing")
                if mode == "reserved":
                    await self._project(conn, {**row, "lineage_id": lineage_id, "attempt_number": number},
                                        phase="reserved", outcome="eligible")
        except AppError as exc:
            if exc.code == "domain_recovery_not_due":
                async with self.database.transaction(control=True) as conn:
                    await conn.execute("UPDATE operations.recoveries SET state='requested',due_at=%s,owner_token=NULL,lease_until=NULL "
                                       "WHERE attempt_id=%s AND owner_token=%s AND state='inspecting'",
                                       (exc.details["due_at"], row["attempt_id"], row["owner_token"]))
                return
            await self._set_state(row["attempt_id"], row["owner_token"], "ineligible", {"reason": exc.code})
            return
        try:
            # Domain owner must atomically recheck version/eligibility before effects.
            result = await action.invoke(row, row["target"], facts, row["attempt_id"])
        except Exception:
            result = {"state": "unknown", "reason": "domain_command_interrupted"}
        if not isinstance(result, dict) or result.get("state") not in {"accepted", "failed", "unknown"}:
            result = {"state": "unknown", "reason": "invalid_domain_receipt"}
        async with self.database.transaction(control=True) as conn:
            current = await (await conn.execute("SELECT 1 FROM operations.recoveries WHERE attempt_id=%s "
                "AND state='executing' AND owner_token=%s AND lease_until>now() FOR UPDATE",
                (row["attempt_id"], row["owner_token"]))).fetchone()
            if not current:
                return
            if result["state"] == "accepted":
                await conn.execute("UPDATE operations.recoveries SET state='observing',result=%s "
                                   "WHERE attempt_id=%s AND state='executing'",
                                   (Jsonb({"facts": wire(facts), "receipt": wire(result), "probe_token": probe}), row["attempt_id"]))
            else:
                from tianshu.runtime.recovery_policy import FailureCategory
                category = (FailureCategory.UNKNOWN_EFFECT if result["state"] == "unknown" else
                            FailureCategory(result["category"]) if result.get("category") in {"transport", "infrastructure"}
                            else FailureCategory.OTHER)
                due = None
                if mode == "reserved":
                    due = await self.policy.fail_attempt_on(conn, row["attempt_id"], code=result.get("reason", "recovery_failed"),
                                                            category=category, probe_token=probe)
                await conn.execute("UPDATE operations.recoveries SET state=%s,result=%s,finished_at=now() WHERE attempt_id=%s",
                                   (result["state"], Jsonb({"facts": wire(facts), "receipt": wire(result),
                                                           "probe_token": probe}), row["attempt_id"]))
                await self._target_state(conn, row, result["state"])
                if mode == "reserved":
                    await self._project(conn, {**row, "lineage_id": lineage_id, "attempt_number": number},
                                        phase="settled", outcome="eligible" if due else result["state"])

    async def observe_pending(self, *, limit=32):
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (await conn.execute("SELECT * FROM operations.recoveries WHERE state='observing' "
                                            "ORDER BY last_observed_at NULLS FIRST,created_at LIMIT %s", (limit,))).fetchall()
        for row in rows:
            action = self.actions.get(row["action"])
            if action is None:
                continue
            async with self.database.transaction(control=True) as conn:
                renewed = await (await conn.execute(
                    "UPDATE operations.recoveries r SET lease_until=now()+interval '120 seconds' "
                    "FROM operations.incidents i WHERE r.attempt_id=%s "
                    "AND r.state='observing' AND r.owner_token=%s AND r.lease_until>now() "
                    "AND i.incident_id=r.incident_id AND i.revision=r.incident_revision AND i.state<>'resolved' "
                    "AND r.data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton) "
                    "AND (r.lineage_id='' OR EXISTS (SELECT 1 FROM runtime.recovery_lineages l "
                    "WHERE l.lineage_id=r.lineage_id AND NOT l.paused AND NOT l.cancelled "
                    "AND l.data_generation=r.data_generation AND l.current_version=r.result->'facts'->>'version')) "
                    "RETURNING r.attempt_id",
                    (row["attempt_id"], row["owner_token"]))).fetchone()
                if not renewed:
                    current = await (await conn.execute("SELECT * FROM operations.recoveries WHERE attempt_id=%s FOR UPDATE",
                                                        (row["attempt_id"],))).fetchone()
                    if current and current["state"] == "observing":
                        from tianshu.runtime.recovery_policy import FailureCategory
                        if row["policy_mode"] == "reserved":
                            await self.policy.fail_attempt_on(conn, row["attempt_id"], code="recovery_owner_lost",
                                category=FailureCategory.UNKNOWN_EFFECT, probe_token=(row["result"] or {}).get("probe_token"))
                        await conn.execute("UPDATE operations.recoveries SET state='unknown',finished_at=now() WHERE attempt_id=%s",
                                           (row["attempt_id"],))
                        await self._target_state(conn, row, "unknown")
                        if row["policy_mode"] == "reserved":
                            await self._project(conn, row, phase="settled", outcome="unknown")
                    continue
            try:
                result = await action.observe(row, row["target"], row["result"]["facts"], row["attempt_id"])
            except Exception:
                continue  # Reading failed; the original attempt remains observable.
            if not isinstance(result, dict) or result.get("state") not in {"verified", "failed", "pending", "unknown"}:
                continue
            if result["state"] == "pending" or result["state"] == "unknown" and action.observation_only:
                async with self.database.transaction(control=True) as conn:
                    await conn.execute("UPDATE operations.recoveries SET observation=%s,last_observed_at=now() WHERE attempt_id=%s AND state='observing'",
                                       (Jsonb(wire(result)), row["attempt_id"]))
                continue
            incident = None
            async with self.database.transaction(control=True) as conn:
                current = await (await conn.execute("SELECT * FROM operations.recoveries WHERE attempt_id=%s FOR UPDATE", (row["attempt_id"],))).fetchone()
                if not current or current["state"] != "observing":
                    continue
                probe = row["result"].get("probe_token")
                due = None
                if result["state"] == "verified":
                    if row["policy_mode"] == "reserved":
                        await self.policy.succeed_attempt_on(conn, row["attempt_id"], probe_token=probe)
                    await self._target_state(conn, row, "verified")
                    incident = await self._resolve_incident(conn, row, action, result)
                else:
                    from tianshu.runtime.recovery_policy import FailureCategory
                    category = (FailureCategory.UNKNOWN_EFFECT if result["state"] == "unknown" else
                                FailureCategory(result["category"]) if result.get("category") in {"transport", "infrastructure"}
                                else FailureCategory.OTHER)
                    if row["policy_mode"] == "reserved":
                        due = await self.policy.fail_attempt_on(conn, row["attempt_id"], code=result.get("reason", "recovery_unverified"),
                                                                category=category, probe_token=probe)
                    await self._target_state(conn, row, result["state"])
                await conn.execute("UPDATE operations.recoveries SET state=%s,observation=%s,finished_at=now() WHERE attempt_id=%s",
                                   (result["state"], Jsonb(wire(result)), row["attempt_id"]))
                await self._target_state(conn, row, result["state"])
                if row["policy_mode"] == "reserved":
                    await self._project(conn, row, phase="settled",
                                        outcome="eligible" if result["state"] != "verified" and due else result["state"])
            if incident:
                await self.outbox.enqueue(incident, "resolved")

    async def observe_unknown(self, *, limit=32):
        """Observe an original result later; never repeat its uncertain effect."""
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (await conn.execute("SELECT * FROM operations.recoveries WHERE state='unknown' "
                                            "ORDER BY last_observed_at NULLS FIRST,created_at LIMIT %s", (limit,))).fetchall()
        for row in rows:
            action = self.actions.get(row["action"])
            facts = (row.get("result") or {}).get("facts")
            if not action or not (action.observation_only or action.unknown_observable) or not facts:
                continue
            try:
                result = await action.observe(row, row["target"], facts, row["attempt_id"])
            except Exception:
                continue
            async with self.database.transaction(control=True) as conn:
                await conn.execute("UPDATE operations.recoveries SET last_observed_at=now() WHERE attempt_id=%s AND state='unknown'",
                                   (row["attempt_id"],))
            reference = result.get("observation_ref") or ("receipt:" + str(result["receipt_id"]) if result.get("receipt_id") else None)
            if result.get("state") not in {"verified", "failed"} or not reference:
                continue
            incident = None
            async with self.database.transaction(control=True) as conn:
                current = await (await conn.execute("SELECT * FROM operations.recoveries WHERE attempt_id=%s FOR UPDATE", (row["attempt_id"],))).fetchone()
                if not current or current["state"] != "unknown":
                    continue
                from tianshu.runtime.recovery_policy import FailureCategory
                state = "succeeded" if result["state"] == "verified" else "failed"
                if row["policy_mode"] == "reserved":
                    await self.policy.resolve_unknown_on(conn, row["attempt_id"], observation_ref=reference, state=state,
                        code=result.get("reason"), category=FailureCategory.OTHER if state == "failed" else None,
                        probe_token=(row["result"] or {}).get("probe_token"))
                await conn.execute("UPDATE operations.recoveries SET state=%s,observation=%s,finished_at=now() WHERE attempt_id=%s",
                                   (result["state"], Jsonb(wire(result)), row["attempt_id"]))
                await self._target_state(conn, row, result["state"])
                if row["policy_mode"] == "reserved":
                    await self._project(conn, row, phase="reconciled", outcome=result["state"])
                if result["state"] == "verified":
                    incident = await self._resolve_incident(conn, row, action, result)
            if incident:
                await self.outbox.enqueue(incident, "resolved")
