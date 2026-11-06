"""Stable notification outbox; an uncertain send is never automatically repeated."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import hashlib
import json
import smtplib
import ssl
from uuid import uuid4

from psycopg.types.json import Jsonb

from .incidents import IncidentStore


def notification_id(incident_id, kind, channel, target, *, episode=1, severity=None):
    value = json.dumps([incident_id, kind, channel, target, episode,
                        severity if kind == "escalated" else None], ensure_ascii=False, separators=(",", ":"))
    return "notice_" + hashlib.sha256(value.encode()).hexdigest()


def message_for(incident, kind):
    return {"incident_id": incident["incident_id"], "kind": kind, "severity": incident["severity"],
            "dependency": incident["dependency"], "error_family": incident["error_family"],
            "version": incident["version"], "affected": incident["affected"],
            "summary": incident["summary"], "observation": incident.get("last_observation"),
            "state": incident["state"], "attempted_actions": incident.get("attempted_actions", []),
            "diagnostic_attempts": incident.get("diagnostic_attempts", []),
            "unfinished": incident.get("diagnostic_state"),
            "next_step": "复核受影响范围与原回执，再通过已登记领域命令处理" if kind != "resolved" else "已观察到领域结果"}


def _smtp_send(config, password, payload, notice_id):
    message = EmailMessage()
    message["From"], message["To"] = config["sender"], config["recipient"]
    message["Subject"] = "[天枢运维] " + payload["kind"] + " " + payload["dependency"]
    message["Message-ID"] = "<" + notice_id + "@tianshu.local>"
    message.set_content(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    submitted = False
    client = None
    try:
        context = ssl.create_default_context()
        client = (smtplib.SMTP_SSL(config["host"], config["port"], timeout=15, context=context)
                  if config["tls"] == "tls" else smtplib.SMTP(config["host"], config["port"], timeout=15))
        if config["tls"] == "starttls":
            client.starttls(context=context)
        if config["username"]:
            client.login(config["username"], password or "")
        submitted = True
        refused = client.send_message(message)
        if refused:
            return {"state": "failed", "code": "smtp_recipient_refused"}
        return {"state": "accepted", "transport": "smtp", "meaning": "server_accepted_not_read"}
    except smtplib.SMTPResponseException as exc:
        return {"state": "failed", "code": "smtp_" + str(exc.smtp_code)}
    except Exception as exc:
        return {"state": "unknown" if submitted else "failed", "code": type(exc).__name__}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


class NotificationOutbox:
    def __init__(self, database, credentials, *, qq_send=None):
        self.database, self.credentials, self.qq_send = database, credentials, qq_send

    async def enqueue(self, incident, kind):
        if kind not in {"opened", "escalated", "resolved"}:
            raise ValueError("invalid_notification_kind")
        async with self.database.transaction(control=True) as conn:
            from tianshu.runtime.work_retirement import recorded_on
            if await recorded_on(conn, "operations", {"incident_id": incident["incident_id"], "episode": incident["episode"]}):
                return
            if kind == "opened":
                # Suppress only a proven dependent incident while all of its
                # recorded roots are still active in their original episodes.
                suppressed = await (await conn.execute(
                    "SELECT 1 FROM operations.incidents i WHERE i.incident_id=%s AND i.episode=%s "
                    "AND i.uncorrelated_occurrences=0 AND EXISTS (SELECT 1 FROM operations.incident_causes c "
                    "WHERE c.incident_id=i.incident_id AND c.episode=i.episode) AND NOT EXISTS "
                    "(SELECT 1 FROM operations.incident_causes c JOIN operations.incidents r "
                    "ON r.incident_id=c.root_incident_id WHERE c.incident_id=i.incident_id AND c.episode=i.episode "
                    "AND (r.state='resolved' OR r.episode<>c.root_episode))", (incident["incident_id"], incident["episode"]))).fetchone()
                if suppressed:
                    return
            settings = (await IncidentStore.settings_on(conn))["value"]["notifications"]
            attempted = await (await conn.execute(
                "SELECT action,state FROM operations.recoveries WHERE incident_id=%s ORDER BY created_at DESC LIMIT 32",
                (incident["incident_id"],))).fetchall()
            diagnosed = await (await conn.execute(
                "SELECT route_id,status,error_code FROM operations.diagnostics WHERE incident_id=%s ORDER BY created_at DESC LIMIT 2",
                (incident["incident_id"],))).fetchall()
            payload = message_for({**incident, "attempted_actions": attempted, "diagnostic_attempts": diagnosed}, kind)
            for channel in ("qq", "smtp"):
                target = settings[channel]
                if target is None:
                    continue
                recipient = target["recipient_id"] if channel == "qq" else target["recipient"]
                if kind == "resolved":
                    earlier = [notification_id(incident["incident_id"], "opened", channel, recipient,
                                               episode=incident["episode"])]
                    earlier.extend(notification_id(incident["incident_id"], "escalated", channel, recipient,
                                                   episode=incident["episode"], severity=severity)
                                   for severity in ("info", "warning", "error", "critical"))
                    visible = await (await conn.execute("SELECT 1 FROM operations.notification_outbox "
                        "WHERE notification_id=ANY(%s) LIMIT 1", (earlier,))).fetchone()
                    if not visible:
                        continue
                notice_id = notification_id(incident["incident_id"], kind, channel, recipient,
                                            episode=incident["episode"], severity=incident["severity"])
                await conn.execute(
                    "INSERT INTO operations.notification_outbox(notification_id,incident_id,kind,episode,incident_revision,channel,target,payload) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (notice_id, incident["incident_id"], kind, incident["episode"], incident["revision"], channel,
                     recipient, Jsonb(payload)))

    async def release_suppressed(self):
        # A child that outlives its root, or gains an unrelated occurrence,
        # becomes an independent alert instead of remaining hidden.
        async with self.database.transaction(control=True, read_only=True) as conn:
            settings = (await IncidentStore.settings_on(conn))["value"]["notifications"]
            qq = settings["qq"]["recipient_id"] if settings["qq"] else None
            smtp = settings["smtp"]["recipient"] if settings["smtp"] else None
            if not qq and not smtp:
                return
            rows = await (await conn.execute(
                "SELECT i.* FROM operations.incidents i WHERE i.state IN ('open','observing','escalated') "
                "AND NOT runtime.work_is_retired('operations',jsonb_build_object('incident_id',i.incident_id,'episode',i.episode)) "
                "AND EXISTS (SELECT 1 FROM operations.incident_causes c WHERE c.incident_id=i.incident_id "
                "AND c.episode=i.episode) AND (i.uncorrelated_occurrences>0 OR EXISTS "
                "(SELECT 1 FROM operations.incident_causes c JOIN operations.incidents r ON r.incident_id=c.root_incident_id "
                "WHERE c.incident_id=i.incident_id AND c.episode=i.episode AND (r.state='resolved' OR r.episode<>c.root_episode))) "
                "AND ((%s::text IS NOT NULL AND NOT EXISTS (SELECT 1 FROM operations.notification_outbox o "
                "WHERE o.incident_id=i.incident_id AND o.episode=i.episode AND o.kind='opened' AND o.channel='qq' AND o.target=%s)) "
                "OR (%s::text IS NOT NULL AND NOT EXISTS (SELECT 1 FROM operations.notification_outbox o "
                "WHERE o.incident_id=i.incident_id AND o.episode=i.episode AND o.kind='opened' AND o.channel='smtp' AND o.target=%s))) "
                "ORDER BY i.last_seen LIMIT 32", (qq, qq, smtp, smtp))).fetchall()
        for row in rows:
            await self.enqueue(row, "opened")

    async def deliver_due(self, *, limit=16):
        # Any expired in-flight send may have reached its destination.
        async with self.database.transaction(control=True) as conn:
            await conn.execute("UPDATE operations.notification_outbox SET status=CASE WHEN inflight_state IN ('observing','unknown') "
                               "THEN inflight_state ELSE 'unknown' END,owner_token=NULL,"
                               "lease_until=NULL,last_error='send_owner_lost',updated_at=now() "
                               "WHERE status='sending' AND lease_until<=now()")
            rows = await (await conn.execute(
                "SELECT * FROM operations.notification_outbox WHERE (status IN ('pending','observing') "
                "OR (status='unknown' AND channel='qq')) AND due_at<=now() "
                "AND NOT runtime.work_is_retired('operations',jsonb_build_object('incident_id',incident_id,'episode',episode)) "
                "ORDER BY due_at,notification_id LIMIT %s FOR UPDATE SKIP LOCKED", (min(limit, 4),))).fetchall()
            for row in rows:
                token = uuid4().hex
                row["owner_token"] = token
                await conn.execute("UPDATE operations.notification_outbox SET inflight_state=status,status='sending',owner_token=%s,"
                                   "lease_until=now()+interval '60 seconds',attempts=attempts+CASE WHEN status='pending' THEN 1 ELSE 0 END,updated_at=now() "
                                   "WHERE notification_id=%s", (token, row["notification_id"]))
            settings = (await IncidentStore.settings_on(conn))["value"]["notifications"]
        outcomes = await asyncio.gather(*(self._settle_delivery(row, settings) for row in rows), return_exceptions=True)
        if any(isinstance(outcome, Exception) for outcome in outcomes):
            raise RuntimeError("notification_settlement_failed")

    async def _settle_delivery(self, row, settings):
        try:
            operation = self._deliver(row, settings) if row["status"] == "pending" else self._observe(row, settings)
            result = await asyncio.wait_for(operation, timeout=45)
        except (TimeoutError, asyncio.TimeoutError):
            result = {"state": "unknown", "code": "notification_timeout"}
        if not isinstance(result, dict) or result.get("state") not in {"accepted", "failed", "unknown", "observing"}:
            result = {"state": "unknown", "code": "invalid_transport_receipt"}
        state = result["state"]
        attempts = row["attempts"] + (1 if row["status"] == "pending" else 0)
        status = "pending" if state == "failed" and row["status"] == "pending" and attempts < 5 else state
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "UPDATE operations.notification_outbox SET status=%s,receipt=%s,last_error=%s,"
                "due_at=now()+(%s * interval '1 second'),owner_token=NULL,lease_until=NULL,inflight_state=NULL,updated_at=now() "
                "WHERE notification_id=%s AND status='sending' AND owner_token=%s AND lease_until>now()",
                (status, Jsonb(result), result.get("code"), 30 if status in {"observing", "unknown"}
                 else min(900, 30 * 2 ** max(0, attempts-1)),
                 row["notification_id"], row["owner_token"]))

    async def _observe(self, row, settings):
        target = settings.get("qq")
        if (row["channel"] != "qq" or not target or target.get("recipient_id") != row["target"]
                or self.qq_send is None or not hasattr(self.qq_send, "observe")):
            return {"state": "unknown", "code": "qq_observation_unavailable"}
        return await self.qq_send.observe(connection_id=target["connection_id"], recipient_id=row["target"],
                                          notification_id=row["notification_id"])

    async def _deliver(self, row, settings):
        target = settings.get(row["channel"])
        if target is None or (target.get("recipient_id") if row["channel"] == "qq" else target.get("recipient")) != row["target"]:
            return {"state": "failed", "code": "notification_target_changed"}
        if row["channel"] == "qq":
            if self.qq_send is None:
                return {"state": "failed", "code": "qq_owner_unavailable"}
            try:
                return await self.qq_send(connection_id=target["connection_id"], recipient_id=row["target"],
                                          notification_id=row["notification_id"], payload=row["payload"])
            except Exception:
                return {"state": "unknown", "code": "qq_transport_interrupted"}
        try:
            password = await self.credentials.resolve(target["credential_ref"]) if target["credential_ref"] else None
        except Exception:
            return {"state": "failed", "code": "smtp_credential_unavailable"}
        return await asyncio.to_thread(_smtp_send, target, password, row["payload"], row["notification_id"])
