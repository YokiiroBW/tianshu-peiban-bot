"""Incident aggregation and settings, independent of any domain's result ledger."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from tianshu.runtime.api import CommandResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


_NAME = re.compile(r"^[a-zA-Z0-9_.:/-]{1,200}$")
_SEVERITY = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_SUMMARY_FIELDS = frozenset({"event", "role", "error_code", "task_name", "version", "job_id", "material_id", "failure_id",
                             "attempt_id", "status", "duration_ms", "stack_frames", "receipt", "progress"})


def identifier(value, *, fallback=None):
    if isinstance(value, str) and _NAME.fullmatch(value):
        return value
    if fallback is not None:
        return fallback
    raise AppError("invalid_operations_identifier")


def fingerprint(dependency, family, version):
    raw = json.dumps([dependency, family, version], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def recovery_target(value):
    if not isinstance(value, dict) or set(value) - {"material_id", "version", "actor_id", "activity_id", "kind", "id", "revision"}:
        raise AppError("invalid_recovery_target")
    for key, item in value.items():
        if key == "revision":
            if type(item) is not int or item < 1:
                raise AppError("invalid_recovery_target")
        else:
            identifier(item)
    return value


def target_id(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def safe_summary(event):
    """Only structured, bounded diagnostic facts cross the model/notification boundary."""
    result = {}
    for key in _SUMMARY_FIELDS:
        value = event.get(key)
        if key in {"stack_frames", "receipt", "progress"}:
            # Producers must publish structured code/position facts, never messages or arguments.
            if isinstance(value, list) and key == "stack_frames":
                result[key] = [{k: v for k, v in item.items() if k in {"module", "function", "line"}
                                and (type(v) is int or isinstance(v, str) and _NAME.fullmatch(v))}
                               for item in value[:12] if isinstance(item, dict)]
            elif isinstance(value, dict):
                result[key] = {k: v for k, v in value.items()
                               if k in {"state", "status", "dispatch", "error_code", "due", "eligible",
                                        "paused", "cancelled", "executing", "within_budget", "version"}
                               and (type(v) in (int, bool) or isinstance(v, str) and _NAME.fullmatch(v))}
        elif type(value) in (int, float, bool):
            result[key] = value
        elif isinstance(value, str) and _NAME.fullmatch(value):
            result[key] = value
    return result


def default_settings():
    return {"diagnosis": {"primary_route_id": None, "backup_route_id": None},
            "notifications": {"qq": None, "smtp": None}}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != {"diagnosis", "notifications"}:
        raise AppError("invalid_operations_settings")
    diag, notifications = value["diagnosis"], value["notifications"]
    if not isinstance(diag, dict) or set(diag) != {"primary_route_id", "backup_route_id"}:
        raise AppError("invalid_operations_settings")
    for route in diag.values():
        if route is not None:
            identifier(route)
    if not isinstance(notifications, dict) or set(notifications) != {"qq", "smtp"}:
        raise AppError("invalid_operations_settings")
    qq, smtp = notifications["qq"], notifications["smtp"]
    if qq is not None:
        if not isinstance(qq, dict) or set(qq) != {"connection_id", "recipient_id"}:
            raise AppError("invalid_qq_notification_settings")
        identifier(qq["connection_id"])
        identifier(qq["recipient_id"])
    if smtp is not None:
        required = {"host", "port", "sender", "recipient", "username", "credential_ref", "tls"}
        if not isinstance(smtp, dict) or set(smtp) != required:
            raise AppError("invalid_smtp_notification_settings")
        if (not isinstance(smtp["host"], str) or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", smtp["host"])
                or type(smtp["port"]) is not int or not 1 <= smtp["port"] <= 65535
                or smtp["tls"] not in {"starttls", "tls"}):
            raise AppError("invalid_smtp_notification_settings")
        for field in ("sender", "recipient"):
            if not isinstance(smtp[field], str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+", smtp[field]):
                raise AppError("invalid_smtp_notification_settings")
        if smtp["username"] is not None and (not isinstance(smtp["username"], str) or len(smtp["username"]) > 256):
            raise AppError("invalid_smtp_notification_settings")
        ref = smtp["credential_ref"]
        if ref is not None and not (ref == "sealed:operations:smtp" or re.fullmatch(r"(?:env:)?TIANSHU_OPS_SMTP_[A-Z0-9_]+", ref)):
            raise AppError("invalid_operations_credential_reference")
    return value


class IncidentStore:
    def __init__(self, database, commands, identity, credentials=None):
        self.database, self.commands, self.identity, self.credentials = database, commands, identity, credentials

    async def ingest(self, event):
        event_id = identifier(event.get("event_id"))
        dependency = identifier(event.get("dependency"), fallback="runtime")
        family = identifier(event.get("error_family"), fallback="unknown")
        version = identifier(event.get("version"), fallback="unknown")
        severity = event.get("severity", "error")
        if severity not in _SEVERITY:
            raise AppError("invalid_incident_severity")
        affected = event.get("affected", [])
        if not isinstance(affected, list):
            raise AppError("invalid_incident_affected")
        affected = sorted({identifier(item) for item in affected})
        material = event.get("material_id")
        target = event.get("recovery_target") or (
            {"material_id": material} if isinstance(material, str) and _NAME.fullmatch(material) else {})
        target = recovery_target(target)
        key = fingerprint(dependency, family, version)
        incident_id = "incident_" + key
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "INSERT INTO operations.incidents(incident_id,fingerprint,dependency,error_family,version,severity,summary,affected) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING",
                (incident_id, key, dependency, family, version, severity, Jsonb(safe_summary(event)), Jsonb(affected)))
            row = await (await conn.execute("SELECT * FROM operations.incidents WHERE fingerprint=%s FOR UPDATE", (key,))).fetchone()
            inserted = await (await conn.execute(
                "INSERT INTO operations.incident_events(event_id,incident_id) VALUES(%s,%s) "
                "ON CONFLICT(event_id) DO NOTHING RETURNING event_id", (event_id, row["incident_id"]))).fetchone()
            if not inserted:
                return row
            episode = row["episode"] + (1 if row["state"] == "resolved" else 0)
            root_id = event.get("root_incident_id")
            root = None
            if isinstance(root_id, str) and _NAME.fullmatch(root_id) and root_id != row["incident_id"]:
                root = await (await conn.execute(
                    "SELECT incident_id,episode FROM operations.incidents WHERE incident_id=%s AND state<>'resolved'",
                    (root_id,))).fetchone()
            if root:
                await conn.execute("INSERT INTO operations.incident_causes(incident_id,episode,root_incident_id,root_episode) "
                                   "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                   (row["incident_id"], episode, root["incident_id"], root["episode"]))
            if target:
                await conn.execute("INSERT INTO operations.incident_targets(incident_id,episode,target_id,target) VALUES(%s,%s,%s,%s) "
                                   "ON CONFLICT DO NOTHING", (row["incident_id"], episode, target_id(target), Jsonb(target)))
            severity_increased = (row["state"] != "resolved" and row["occurrences"] > 0
                                  and _SEVERITY[severity] > _SEVERITY[row["severity"]])
            if row["state"] != "resolved":
                severity = max((row["severity"], severity), key=lambda item: _SEVERITY[item])
            changed = row["state"] == "resolved" or severity != row["severity"]
            joined = affected if row["state"] == "resolved" else sorted(set(row["affected"]) | set(affected))
            row = await (await conn.execute(
                "UPDATE operations.incidents SET occurrences=occurrences+1,unscoped_occurrences=CASE WHEN state='resolved' THEN %s "
                "ELSE unscoped_occurrences+%s END,uncorrelated_occurrences=CASE WHEN state='resolved' THEN %s "
                "ELSE uncorrelated_occurrences+%s END,last_seen=now(),severity=%s,"
                "state=CASE WHEN state='resolved' THEN 'open' ELSE state END,"
                "episode=CASE WHEN state='resolved' THEN episode+1 ELSE episode END,"
                "diagnosis_count=CASE WHEN state='resolved' THEN 0 ELSE diagnosis_count END,"
                "diagnostic_state=CASE WHEN state='resolved' THEN 'pending' ELSE diagnostic_state END,"
                "recovery_action=CASE WHEN state='resolved' THEN NULL ELSE recovery_action END,"
                "recovery_lineage=CASE WHEN state='resolved' THEN NULL ELSE recovery_lineage END,"
                "last_observation=CASE WHEN state='resolved' THEN NULL ELSE last_observation END,"
                "revision=revision+%s,summary=%s,affected=%s,resolved_at=CASE WHEN state='resolved' THEN NULL ELSE resolved_at END "
                "WHERE incident_id=%s RETURNING *",
                (0 if target else 1, 0 if target else 1, 0 if root else 1, 0 if root else 1,
                 severity, 1 if changed else 0, Jsonb(safe_summary(event)), Jsonb(joined), row["incident_id"]))).fetchone()
            row["severity_increased"] = severity_increased
            return row

    async def get(self, incident_id):
        identifier(incident_id)
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await self._get_on(conn, incident_id)

    @staticmethod
    async def _get_on(conn, incident_id):
        row = await (await conn.execute("SELECT * FROM operations.incidents WHERE incident_id=%s", (incident_id,))).fetchone()
        if not row:
            raise AppError("incident_not_found", status=404)
        row["diagnostics"] = await (await conn.execute(
            "SELECT * FROM operations.diagnostics WHERE incident_id=%s ORDER BY created_at", (incident_id,))).fetchall()
        row["recoveries"] = await (await conn.execute(
            "SELECT * FROM operations.recoveries WHERE incident_id=%s ORDER BY created_at", (incident_id,))).fetchall()
        row["targets"] = await (await conn.execute(
            "SELECT episode,target_id,target,state FROM operations.incident_targets WHERE incident_id=%s ORDER BY episode,target_id", (incident_id,))).fetchall()
        row["caused_incidents"] = await (await conn.execute(
            "SELECT c.incident_id,c.episode,c.root_episode,i.state,i.error_family,i.severity,i.affected "
            "FROM operations.incident_causes c JOIN operations.incidents i ON i.incident_id=c.incident_id "
            "WHERE c.root_incident_id=%s ORDER BY c.root_episode,c.incident_id", (incident_id,))).fetchall()
        row["notifications"] = await (await conn.execute(
            "SELECT notification_id,kind,channel,status,attempts,receipt,last_error,updated_at "
            "FROM operations.notification_outbox WHERE incident_id=%s ORDER BY created_at", (incident_id,))).fetchall()
        return row

    async def list(self, body):
        limit = body.get("limit", 50)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AppError("invalid_limit")
        state = body.get("state")
        if state is not None and state not in {"open", "observing", "escalated", "resolved"}:
            raise AppError("invalid_incident_state")
        cursor = body.get("cursor")
        if cursor is not None:
            identifier(cursor)
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (await conn.execute(
                "SELECT * FROM operations.incidents WHERE (%s::text IS NULL OR state=%s) "
                "AND (%s::text IS NULL OR incident_id>%s) ORDER BY incident_id LIMIT %s",
                (state, state, cursor, cursor, limit + 1))).fetchall()
        return {"items": rows[:limit], "next_cursor": rows[limit-1]["incident_id"] if len(rows) > limit else None,
                "coverage": {"complete": len(rows) <= limit}}

    @staticmethod
    async def settings_on(conn, credentials=None):
        row = await (await conn.execute("SELECT revision,value FROM operations.settings WHERE singleton")).fetchone()
        value = row["value"] if row else default_settings()
        smtp = value["notifications"]["smtp"]
        credential_configured = None
        if smtp and smtp.get("username"):
            ref = smtp.get("credential_ref")
            if not ref:
                credential_configured = False
            elif ref.startswith("sealed:"):
                credential_configured = bool(await (await conn.execute(
                    "SELECT 1 FROM runtime.credentials WHERE reference=%s", (ref,))).fetchone())
            elif credentials:
                credential_configured = bool(credentials.secrets.environ.get(ref.removeprefix("env:")))
        return {"revision": row["revision"] if row else 0, "value": value,
                "diagnosis_configured": bool(value["diagnosis"]["primary_route_id"]),
                "notification_configured": {"qq": bool(value["notifications"]["qq"]),
                                            "smtp": bool(smtp) and credential_configured is not False},
                "smtp_credential_configured": credential_configured}

    async def settings(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            return await self.settings_on(conn, self.credentials)

    async def diagnostic_context(self):
        """Refresh only the trusted author who actually saved operations settings."""
        from tianshu.models.codec import context_from_wire
        from tianshu.types import OriginKind
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (await conn.execute("SELECT author_context FROM operations.settings WHERE singleton")).fetchone()
        if not row or not row["author_context"]:
            raise AppError("operations_settings_author_unavailable", status=503)
        original = context_from_wire(row["author_context"])
        current = await self.identity.refresh_scheduled_context(original, permission="config.publish", require_actor=False)
        return replace(current, origin_kind=OriginKind.SYSTEM)

    async def save_settings(self, context, body):
        value = validate_settings(body.get("value"))
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise AppError("expected_revision_required")
        password = body.get("smtp_password")
        clear = body.get("clear_smtp_password", False)
        if (type(clear) is not bool or clear and password is not None
                or password is not None and (not isinstance(password, str) or not 1 <= len(password) <= 16384)):
            raise AppError("invalid_smtp_credential_change")
        if password is not None and (not self.credentials or not value["notifications"]["smtp"]
                                     or value["notifications"]["smtp"]["credential_ref"] != "sealed:operations:smtp"):
            raise AppError("invalid_smtp_credential_change")

        async def apply(conn):
            await self.identity.check_context(context, connection=conn)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operations.settings',0))")
            current = await self.settings_on(conn, self.credentials)
            if current["revision"] != expected:
                raise AppError("revision_conflict", status=409)
            if clear:
                await conn.execute("DELETE FROM runtime.credentials WHERE reference='sealed:operations:smtp'")
            if password is not None:
                await self.credentials.put(conn, "sealed:operations:smtp", password)
            await conn.execute("INSERT INTO operations.settings(singleton,revision,value,author_context) VALUES(true,%s,%s,%s) "
                               "ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,"
                               "value=excluded.value,author_context=excluded.author_context,updated_at=now()",
                               (expected+1, Jsonb(value), Jsonb(wire(context))))
            await conn.execute("UPDATE operations.incidents SET state='open',diagnostic_state='pending' "
                               "WHERE state='escalated' AND diagnosis_count=0 "
                               "AND diagnostic_state IN ('model_not_configured','system_context_unavailable')")
            return CommandResult(body["client_request_id"], data=await self.settings_on(conn, self.credentials), revision=expected+1)

        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            return replace(original, data=await self.settings_on(conn, self.credentials))

        return await self.commands.execute(context, "operations.settings.save", body["client_request_id"],
                                           body_hash(body), apply, on_replay=replay)

    async def request_recovery(self, context, body):
        incident_id, action = identifier(body.get("incident_id")), identifier(body.get("action"))
        revision = body.get("expected_revision")
        if type(revision) is not int or revision < 1:
            raise AppError("expected_revision_required")
        target = recovery_target(body.get("target", {}))

        async def apply(conn):
            await self.identity.check_context(context, connection=conn)
            row = await (await conn.execute("SELECT * FROM operations.incidents WHERE incident_id=%s FOR UPDATE", (incident_id,))).fetchone()
            if not row:
                raise AppError("incident_not_found", status=404)
            if row["revision"] != revision or row["state"] == "resolved":
                raise AppError("revision_conflict", status=409)
            attempt = "opsrec_" + uuid4().hex
            tid = target_id(target) if target else None
            if tid:
                await conn.execute("INSERT INTO operations.incident_targets(incident_id,episode,target_id,target) VALUES(%s,%s,%s,%s) "
                                   "ON CONFLICT DO NOTHING", (incident_id, row["episode"], tid, Jsonb(target)))
            await conn.execute("INSERT INTO operations.recoveries(attempt_id,incident_id,lineage_id,incident_revision,action,target,target_id,state) "
                               "VALUES(%s,%s,'',%s,%s,%s,%s,'requested')", (attempt, incident_id, revision, action, Jsonb(target), tid))
            return CommandResult(body["client_request_id"], action_id=incident_id, attempt_id=attempt,
                                 revision=revision, data={"status": "requested"})

        async def replay(conn, original):
            await self.identity.check_context(context, connection=conn)
            row = await (await conn.execute("SELECT state,result,observation FROM operations.recoveries WHERE attempt_id=%s", (original.attempt_id,))).fetchone()
            return replace(original, data=row)

        return await self.commands.execute(context, "operations.recover", body["client_request_id"], body_hash(body), apply, on_replay=replay)
