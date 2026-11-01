"""One contact proposal per actual photo action, including scheduled deliveries."""
from datetime import UTC, datetime, timedelta

from tianshu.conversation.codec import source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from .types import ContactCandidate
from .validation import timestamp


async def prepare(worker, context, result, dependencies, summary):
    photo=result.result.get("photo_intent") or {}
    sharing=result.result.get("sharing") or {}
    if (result.execution!="succeeded" or result.effect=="unknown" or result.delivery=="delivered"
            or not result.artifacts or not photo or photo.get("delivery_kind")=="create_only"
            or sharing.get("decision")=="archive"):
        return None
    if photo.get("target_ref")!=context.conversation_id or not context.delivery_allowed:
        raise AppError("contact_target_mismatch",status=403)
    from tianshu.capabilities.actions import result_sources
    refs=unique_refs(tuple(dependencies),result_sources(result))
    window=photo.get("window") or {}
    due=timestamp(window["due_at"]) if window.get("due_at") else datetime.now(UTC)
    expires=timestamp(window["expires_at"]) if window.get("expires_at") else due+timedelta(hours=4)
    parts=([{ "kind":"text","text":sharing["notice"]}] if sharing.get("notice") else [])
    parts.extend({"kind":"image" if item.media.mime_type.startswith("image/") else "audio" if item.media.mime_type.startswith("audio/") else "file","media":wire(item.media)} for item in result.artifacts)
    return await worker.prepare_contact(ContactCandidate("action:"+result.action_id,context.actor_id,context.conversation_id,
        "creation",summary,due,expires,context,refs,explicit=photo["delivery_kind"]=="requested_delivery",
        action_id=result.action_id,delivery_kind=photo["delivery_kind"],payload={"artifacts":wire(result.artifacts),"public_parts":parts}))
