"""A sourced experience can become an optional proposal, never an automatic send."""
from datetime import timedelta

from tianshu.conversation.codec import signature, source_refs
from tianshu.sources.dependencies import unique_refs
from .types import ContactCandidate


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
    due=now+timedelta(minutes=value["after_minutes"])
    expires=now+timedelta(minutes=value["expires_minutes"])
    if due>=expires:
        return None
    identity = signature({"actor_id": context.actor_id, "target_ref": target.conversation_id,
        "basis_ids": sorted(value["basis_ids"])})
    return await worker.prepare_contact(ContactCandidate("plan:"+identity,context.actor_id,target.conversation_id,
        "experience",value["summary"],due,expires,target,refs,payload={"reason":value["reason"],
            "public_parts":[{"kind":"text","text":value["summary"]}]}))
