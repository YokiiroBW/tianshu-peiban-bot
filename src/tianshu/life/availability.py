"""Sourced temporary contact state, separate from explicit contact rules."""
from datetime import UTC, datetime, timedelta

from tianshu.conversation.codec import document, signature
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from .validation import timestamp


async def save(life,context,body,refs):
    if not context.person_id or not context.conversation_id or not refs:
        raise AppError("availability_human_source_required")
    state=body.get("availability")
    if state not in {"unknown","free","busy","sleeping","away"}:
        raise AppError("invalid_availability")
    minutes=body.get("valid_for_minutes",480 if state=="sleeping" else 60)
    if type(minutes) is not int or not 1<=minutes<=1440:
        raise AppError("invalid_availability_window")
    inherited=await life.contacts.preferences(context)
    async def apply(connection):
        descriptions=await life.sources.describe_for_write_on(connection,context,refs)
        human=[row for row in descriptions if row["origin_kind"]=="human" and row["speaker_id"]==context.person_id]
        if not human:
            raise AppError("availability_human_source_required")
        at=max(row["occurred_at"] for row in human)
        event=signature(sorted((row["evidence_unit"],row["speaker_id"]) for row in human))
        until=timestamp(body.get("available_until")) if body.get("available_until") else at+timedelta(minutes=minutes)
        if until<=at or until>at+timedelta(days=7):
            raise AppError("invalid_availability_window")
        await connection.execute("INSERT INTO life.contact_preferences(actor_id,person_id,scope_ids,timezone,quiet_start,quiet_end,cooldown_seconds,daily_quota,unanswered_limit,responsiveness,enabled,expiry_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (context.actor_id,context.person_id,list(context.scope_ids),inherited["timezone"],inherited["quiet_start"],inherited["quiet_end"],inherited["cooldown_seconds"],inherited["daily_quota"],inherited["unanswered_limit"],inherited["responsiveness"],inherited["enabled"],inherited["expiry_seconds"]))
        row=await (await connection.execute("SELECT * FROM life.contact_preferences WHERE actor_id=%s AND person_id=%s FOR UPDATE",(context.actor_id,context.person_id))).fetchone()
        if (row["availability_occurred_at"] and row["availability_occurred_at"]>=at) or until<=datetime.now(UTC):
            return CommandResult(body["client_request_id"],{"state":"unchanged","availability":row["availability"],"available_until":row["available_until"]},row["revision"])
        await connection.execute("UPDATE life.contact_preferences SET availability=%s,available_until=%s,availability_event_id=%s,availability_occurred_at=%s,availability_source_refs=%s,revision=revision+1 WHERE actor_id=%s AND person_id=%s",(state,until,event,at,document(refs),context.actor_id,context.person_id))
        return CommandResult(body["client_request_id"],{"state":"recorded","availability":state,"available_until":until},row["revision"]+1)
    return await life.commands.execute(context,"life.availability",body["client_request_id"],signature({"body":body,"refs":refs}),apply)
