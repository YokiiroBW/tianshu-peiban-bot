"""A sourced experience can become an optional proposal, never an automatic send."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from tianshu.conversation.codec import signature, source_refs
from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.serialization import wire
from .types import ContactCandidate


def recipient_key(context):
    return signature({"actor":context.actor_id,"person":context.person_id,"conversation":context.conversation_id})


async def material(worker, context, now):
    """Supply contact controls without importing another private scene's text."""
    life = worker.life
    if life.contacts is None:
        return {"enabled":False,"reason":"contacts_not_configured"}, (), ()
    target = await life.contacts.resolve_target(context,identity=life.identity)
    if target is None:
        return {"enabled":False,"reason":"no_opted_in_recipient"}, (), ()
    preferences = await life.contacts.preferences(target)
    history = await life.contacts.history(target,conversation_only=True)
    window = life.attention.window(preferences,history,now=now)
    key = recipient_key(target)
    day = now.astimezone(ZoneInfo(preferences["timezone"])).date().isoformat()
    delivered = next((row for row in history if row["delivered_at"]),None)
    opportunity = "contact:" + signature({"recipient":key,"day":day,
        "last_expression":delivered["expression_id"] if delivered else None})
    basis = [{"id":opportunity,"kind":"contact_opportunity","source_refs":[]}]
    # A trusted destination is not permission to read its messages from a
    # different routine scene. Conversation material is read only in that scene.
    if context.conversation_id == target.conversation_id and life.memory is not None:
        page = await life.memory.episodes_between(context,now-timedelta(days=1),now,
            limit=32,include_consolidation=False)
        for episode in page.items[-8:]:
            if episode.conversation_id != context.conversation_id or not episode.source_refs:
                continue
            basis.append({"id":f"conversation:{episode.episode_id}:{episode.revision}",
                "kind":"conversation","summary":episode.summary[:500],
                "source_refs":wire(episode.source_refs),"actual_result":{
                    name:(episode.actual_result or {}).get(name) for name in (
                        "experience_kind","imagined","representation","software_action","execution","effect","delivery")}})
    refs = unique_refs(*(source_refs(item["source_refs"]) for item in basis))
    return {"enabled":True,"recipient_key":key,"basis_id":opportunity,
        "window":wire(window),"last_contact_at":wire(delivered["delivered_at"]) if delivered else None,
        "unanswered":sum(bool(row["delivered_at"] and not row["replied_at"]) for row in history),
        "conversation_read":context.conversation_id == target.conversation_id}, tuple(basis), refs


def schema():
    return {"anyOf":[{"type":"null"},{"type":"object","additionalProperties":False,
        "required":["summary","reason","basis_ids","after_minutes","expires_minutes"],"properties":{
            "summary":{"type":"string","minLength":1,"maxLength":1000},
            "reason":{"type":"string","minLength":1,"maxLength":300},
            "basis_ids":{"type":"array","minItems":1,"maxItems":4,"uniqueItems":True,"items":{"type":"string"}},
            "after_minutes":{"type":"integer","minimum":0,"maximum":120},
            "expires_minutes":{"type":"integer","minimum":5,"maximum":1440}}}]}


async def prepare(worker, context, value, data, now):
    if not value:
        return None
    bases={item["id"]:item for item in data["basis"]}
    refs=unique_refs(*(source_refs(bases[identifier]["source_refs"]) for identifier in value["basis_ids"]))
    target=await worker.life.contacts.resolve_target(context,refs,identity=worker.life.identity)
    if target is None:
        return None
    configured = data.get("contact_opportunity",{}).get("recipient_key")
    if configured is not None and configured != recipient_key(target):
        return None
    due=now+timedelta(minutes=value["after_minutes"])
    expires=now+timedelta(minutes=value["expires_minutes"])
    if due>=expires:
        return None
    identity = signature({"actor_id": context.actor_id, "target_ref": target.conversation_id,
        "basis_ids": sorted(value["basis_ids"])})
    return await worker.prepare_contact(ContactCandidate("plan:"+identity,context.actor_id,target.conversation_id,
        "experience",value["summary"],due,expires,target,refs,payload={"reason":value["reason"],
            "planner_decision":{"reason":value["reason"],"basis_ids":list(value["basis_ids"])},
            "public_parts":[{"kind":"text","text":value["summary"]}]}))
