"""Read-only, body-free attribution of frozen Memory consolidation work."""

from copy import deepcopy
import json

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from . import codec
from .authority import request_identity


def material_digest(frozen):
    """Only compare complete episode versions, exact ranges and source identity."""
    episodes = frozen.get("episodes") or ()
    materials = frozen.get("materials") or {}
    if not episodes or not isinstance(materials, dict):
        return None
    if any(not item.get("content_digest") or not item.get("source_refs") for item in episodes):
        return None
    new = {key: value for key,value in materials.items() if value.get("role") == "new"}
    if not new or any(not item.get("source_refs") for item in new.values()):
        return None
    # The frozen mapping has no text. Source versions/ranges and the original
    # Episode digest prove its selected inputs; unprovable legacy rows stay None.
    return body_hash({"episodes":episodes,"new_materials":new})


def input_digest(frozen, context_fingerprint, request):
    if material_digest(frozen) is None or not isinstance(request, dict) or not isinstance(request.get("context"), dict):
        return None
    value = deepcopy(frozen)
    value.pop("batch_id",None)
    # Compare the actual persisted prompt, route and generation settings, not
    # just provenance. Only per-attempt identifiers and deadlines are removed.
    identity = request_identity(request)
    identity.pop("intent_id", None)
    identity.pop("attempt_id", None)
    try:
        part = identity["messages"][0]["parts"][0]
        payload = json.loads(part["text"])
        if part["kind"] != "text" or not isinstance(payload, dict) or payload.get("batch_id") != frozen.get("batch_id"):
            return None
        # The generated envelope contains a unique batch identifier; only
        # that known identity field is normalized. Prompt and content remain
        # part of the hash and are never returned by the attribution query.
        payload["batch_id"] = "<batch>"
        part["text"] = payload
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return body_hash({"frozen":value,"authority":context_fingerprint,"request":identity})


def attribute_attempts(batches, attempts):
    """Do not call same material a duplicate unless the full frozen input matches."""
    by_batch = {}
    for attempt in attempts:
        by_batch.setdefault(attempt["batch_id"],[]).append(attempt)
    seen_input, seen_material, output = {}, set(), []
    for batch in batches:
        manifest = batch["manifest"]
        steps = []
        for attempt in by_batch.get(batch["batch_id"],()):
            frozen = manifest.get("steps",{}).get(attempt["step_id"]) or manifest.get("frozen") or {}
            material = material_digest(frozen)
            complete_input = input_digest(frozen,batch["context_fingerprint"],attempt.get("request"))
            kind = attempt["kind"]
            if kind == "context":
                reason = "context_supplement"
            elif kind == "repair":
                reason = "output_correction"
            elif kind == "retry":
                reason = "independent_retry"
            elif material is None or complete_input is None:
                reason = "input_identity_unproven"
            elif complete_input in seen_input and attempt["status"] == "succeeded":
                reason = "duplicate_completed_input"
            elif material in seen_material:
                reason = "same_material_changed_input"
            else:
                reason = "new_material"
            if material is not None:
                seen_material.add(material)
            if complete_input is not None and kind == "original" and attempt["status"] == "succeeded":
                seen_input.setdefault(complete_input,attempt["step_id"])
            steps.append({"step_id":attempt["step_id"],"attempt_id":attempt["model_attempt_id"],
                "kind":kind,"status":attempt["status"],"attribution":reason,
                "material_digest":material,"input_digest":complete_input,
                "same_input_as":seen_input.get(complete_input) if reason == "duplicate_completed_input" else None})
        output.append({"batch_id":batch["batch_id"],"status":batch["status"],"error_code":batch["error_code"],
            "created_at":wire(batch["created_at"]),"retry_due_at":wire(batch.get("retry_due_at")),"steps":steps})
    return output


class ConsolidationAttribution:
    def __init__(self, memory):
        self.memory = memory

    async def for_episode(self, context, episode_id, *, limit=20):
        if not isinstance(episode_id,str) or not episode_id or type(limit) is not int or not 1 <= limit <= 50:
            raise AppError("invalid_memory_attribution_request")
        async with self.memory.database.transaction(read_only=True) as conn:
            await self.memory.identity.check_context(context,connection=conn)
            episode = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s "
                "AND active AND scope_ids<@%s::text[]",(episode_id,context.actor_id,list(context.scope_ids)))).fetchone()
            if not episode or not all(await self.memory.sources.check_many_on(conn,context,
                    ((*codec.refs(episode["source_refs"]),*codec.refs(episode.get("supplement_refs"))),))):
                raise AppError("episode_unavailable",status=404)
            rows = await (await conn.execute("SELECT b.* FROM memory.consolidation_batches b "
                "JOIN memory.consolidation_state s USING(partition_id) WHERE s.actor_id=%s "
                "AND b.manifest->'episodes' @> %s::jsonb ORDER BY b.created_at DESC,b.batch_id DESC LIMIT %s",
                (context.actor_id,Jsonb([{"episode_id":episode_id}]),limit+1))).fetchall()
            truncated = len(rows)>limit
            rows = rows[:limit]
            visible, chunk, keys = [], [], set()
            async def check_chunk():
                if chunk:
                    visible.extend(await self.memory.sources.check_many_on(conn,context,tuple(chunk)))
            for row in rows:
                refs = codec.refs(row["dependency_refs"])
                batch_keys = {(ref.source_id,ref.revision) for ref in refs}
                if chunk and (len(keys | batch_keys) > 1800 or len(chunk) >= 128):
                    await check_chunk()
                    chunk, keys = [], set()
                chunk.append(refs)
                keys.update(batch_keys)
            await check_chunk()
            allowed = [row for row,ok in zip(rows,visible,strict=True) if ok]
            ids = [row["batch_id"] for row in allowed]
            attempts = await (await conn.execute("SELECT a.batch_id,a.step_id,a.model_attempt_id,a.kind,a.status,a.number,s.request "
                "FROM memory.consolidation_attempts a LEFT JOIN memory.model_steps s ON s.step_id=a.step_id "
                "WHERE a.batch_id=ANY(%s::text[]) ORDER BY a.batch_id,a.number",
                (ids,))).fetchall() if ids else []
        allowed.reverse()
        return {"episode_id":episode_id,"revision":episode["revision"],
            "items":attribute_attempts(allowed,attempts),"truncated":truncated,
            "coverage":{"complete":not truncated and all(visible),
                        "reason": "source_unavailable" if not all(visible) else "limit" if truncated else None}}
