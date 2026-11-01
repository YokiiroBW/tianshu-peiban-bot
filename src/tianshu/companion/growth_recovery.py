"""One-time bounded conversion of pre-version-ledger Growth checkpoints.

GenerationRequests owns original prompts/attempts. This manifest only references
them, and prevents a newly assembled batch from replacing an unresolved original.
The old sequence cursor is used once to seed history, never as ongoing coverage.
"""

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from .growth_materials import reflectable
from .reflection_output import IDENTITY_PROTOCOL, frozen_episodes, generation_identity


PAGE_SIZE = 128
AUTHORIZATION_BLOCKED = "reflection_recovery_authorization_required"
IDENTITY_BLOCKED = "reflection_identity_conflict"


def authorization_error(error):
    return isinstance(error, AppError) and (error.status in {401, 403} or error.code in {
        "access_changed", "delivery_access_changed", "actor_disabled", "source_unavailable",
    })


def recovery_permission(context):
    return "companion.manage" if context.permissions & {"*", "companion.manage"} else "companion.read"


def sequence(cursor):
    if cursor is None:
        return 0
    try:
        prefix, value = cursor.split(":", 1)
        if prefix != "e" or int(value) < 0:
            raise ValueError
        return int(value)
    except (ValueError, AttributeError):
        raise AppError("reflection_material_contract_invalid") from None


class GrowthRecovery:
    def __init__(self, growth):
        self.growth = growth
        self.companion = growth.companion

    @staticmethod
    def observes_original(batch, blocked_material):
        return bool(batch and (batch.get("legacy_material") or
            (blocked_material or {}).get("recovery") == "observe_original_attempt"))

    @staticmethod
    def same_scene(left, right):
        return ((left.principal_id, left.actor_id, left.person_id, left.conversation_id)
                == (right.principal_id, right.actor_id, right.person_id, right.conversation_id))

    async def step(self, state):
        """Read at most one page, persist progress, and use the existing job queue."""
        if state["last_error"] == AUTHORIZATION_BLOCKED:
            return self.blocked(state)
        original = context_from_record(state["context"])
        try:
            try:
                await self.companion.identity.check(original)
                context = original
            except AppError as error:
                if error.code != "access_changed" or state["active_batch"]:
                    raise
                # Only authorize inventory/seed reads. Never replace the context
                # of an existing GenerationRequest, active batch or model read.
                context = await self.companion.identity.refresh_scheduled_context(
                    original, permission=recovery_permission(original))
            if state["legacy_state"] == "requests":
                # Null-request tombstones still use the ORIGINAL context key,
                # including its epoch. Keep that discovery identity across pages.
                await self._requests(state, original)
            elif state["legacy_state"] == "episodes":
                await self._episodes(state, context)
        except AppError as error:
            if not authorization_error(error):
                raise
            return await self.block(state, error)
        return {"state": "recovering", "phase": state["legacy_state"]}

    @staticmethod
    def blocked(state):
        return {"state": "blocked", "error_code": AUTHORIZATION_BLOCKED,
                "phase": state["legacy_state"], "recovery": (
                    "observe_original_attempt" if state["active_batch"] else "reauthorize_original_scope")}

    async def reader(self, original, *, connection=None):
        """Current bounded authorization for reading; never rekey an old intent."""
        return await self.companion.identity.refresh_scheduled_context(
            original, permission=recovery_permission(original), connection=connection)

    async def register_active_on(self, connection, state):
        """Register only pre-v2 in-flight intents in the existing finite manifest."""
        batch = state["active_batch"]
        if not batch or batch.get("reflection_identity_protocol") == IDENTITY_PROTOCOL or batch.get("identity_conversion_key"):
            return state
        current = await (await connection.execute(
            "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
            (state["actor_id"], state["scope_key"]))).fetchone()
        if current is None:
            return state
        batch = current["active_batch"]
        if not batch or batch.get("reflection_identity_protocol") == IDENTITY_PROTOCOL or batch.get("identity_conversion_key"):
            return current
        await connection.execute(
            "INSERT INTO companion.growth_legacy_requests(actor_id,scope_key,material_key,context,inputs,end_cursor,state,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,'pending',coalesce((SELECT created_at FROM conversation.generation_requests "
            "WHERE material_key=%s),now())) ON CONFLICT(actor_id,scope_key,material_key) DO NOTHING",
            (current["actor_id"], current["scope_key"], batch["material_key"], document(batch["context"]),
             document(batch["inputs"]), batch["end_cursor"], batch["material_key"]))
        batch = {**batch, "legacy_material": True, "identity_conversion_key": batch["material_key"]}
        return await (await connection.execute(
            "UPDATE companion.organize_state SET active_batch=%s WHERE actor_id=%s AND scope_key=%s RETURNING *",
            (document(batch), current["actor_id"], current["scope_key"]))).fetchone()

    @staticmethod
    def _proposal_fingerprint(item, episodes):
        proposal = item.proposal
        closure = unique_refs(proposal.source_refs, *(value.source_refs for value in episodes.values()))
        return signature({"proposal": {"id": proposal.proposal_id, "actor_id": proposal.actor_id,
            "episode_ids": sorted(proposal.episode_ids), "content": proposal.content, "kind": proposal.kind,
            "conditions": proposal.conditions, "counterexamples": proposal.counterexamples,
            "scope_ids": sorted(proposal.scope_ids)}, "sources": sorted(signature(ref) for ref in closure)})

    @staticmethod
    def _same_legacy(row, item, episodes, original, context):
        proposal = item.proposal
        closure = unique_refs(proposal.source_refs, *(value.source_refs for value in episodes.values()))
        old_refs = source_refs(row["source_refs"])
        return (row["active"] and row["interpretation"]
            and (row["actor_id"], row["principal_id"], row["person_id"], row["conversation_id"])
                == (original.actor_id, original.principal_id, original.person_id, original.conversation_id)
            and set(row["scope_ids"]) <= set(context.scope_ids)
            and sorted(row["episode_ids"]) == sorted(proposal.episode_ids)
            and row["kind"] == proposal.kind and row["content"] == proposal.content
            and row["conditions"] == proposal.conditions and tuple(row["counterexamples"]) == proposal.counterexamples
            and len(old_refs) == len(unique_refs(old_refs))
            and {signature(ref) for ref in old_refs} == {signature(ref) for ref in closure})

    async def reflection_identities(self, state, context, batch, request, episodes, prepared):
        """One-time pre-v2 manifest conversion; ordinary v2 batches never enter."""
        key = batch.get("identity_conversion_key")
        if key is None:
            return prepared, batch
        if key != request.intent_id or key != batch["material_key"]:
            raise AppError(IDENTITY_BLOCKED, status=409)
        expected = {item.proposal.proposal_id: self._proposal_fingerprint(item, episodes) for item in prepared}
        async with self.companion.database.transaction() as connection:
            await self.growth._validate_material(connection, context, episodes, request.source_refs)
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if not current or not current["active_batch"] or current["active_batch"]["material_key"] != key:
                raise AppError("generation_no_longer_current", status=409)
            manifest = await (await connection.execute(
                "SELECT * FROM companion.growth_legacy_requests WHERE actor_id=%s AND scope_key=%s AND material_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"], key))).fetchone()
            if manifest and (manifest["context"] is None or manifest["state"] == "invalidated"):
                raise AppError("source_unavailable", status=403)
            if (not manifest or manifest["state"] != "pending" or manifest["inputs"] != batch["inputs"]
                    or context_from_record(manifest["context"]) != request.context):
                raise AppError(IDENTITY_BLOCKED, status=409)
            saved = manifest["context"].get("reflection_identity_conversion")
            if saved is None:
                # This v1 formula is restricted to this registered old intent.
                # Persist its decision once, never probe old IDs for a new batch.
                candidates = {item.proposal.proposal_id: str(uuid5(NAMESPACE_URL,
                    f"reflection:{request.context.actor_id}:{item.proposal.kind}:{':'.join(item.support_ids)}"))
                    for item in prepared}
                rows = await (await connection.execute(
                    "SELECT * FROM memory.reflections WHERE actor_id=%s AND proposal_id=ANY(%s)",
                    (context.actor_id, list(candidates.values())))).fetchall() if candidates else []
                old = {row["proposal_id"]: row for row in rows}
                aliases = {item.proposal.proposal_id: candidates[item.proposal.proposal_id] for item in prepared
                    if candidates[item.proposal.proposal_id] in old and self._same_legacy(
                        old[candidates[item.proposal.proposal_id]], item, episodes, request.context, context)}
                saved = {"protocol": IDENTITY_PROTOCOL, "attempt_id": request.attempt_id,
                    "generation": signature(generation_identity(request, episodes)), "proposals": expected, "aliases": aliases}
                await connection.execute(
                    "UPDATE companion.growth_legacy_requests SET context=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND material_key=%s",
                    (document({**manifest["context"], "reflection_identity_conversion": saved}),
                     state["actor_id"], state["scope_key"], key))
            if (saved.get("protocol") != IDENTITY_PROTOCOL or saved.get("attempt_id") != request.attempt_id
                    or saved.get("generation") != signature(generation_identity(request, episodes))
                    or saved.get("proposals") != expected):
                raise AppError(IDENTITY_BLOCKED, status=409)
            aliases = saved["aliases"]
            rows = await (await connection.execute(
                "SELECT * FROM memory.reflections WHERE actor_id=%s AND proposal_id=ANY(%s)",
                (context.actor_id, list(aliases.values())))).fetchall() if aliases else []
            existing = {row["proposal_id"]: row for row in rows}
            converted = []
            for item in prepared:
                identifier = aliases.get(item.proposal.proposal_id)
                if identifier is None:
                    converted.append(item)
                    continue
                row = existing.get(identifier)
                if row is None or not self._same_legacy(row, item, episodes, request.context, context):
                    raise AppError(IDENTITY_BLOCKED, status=409)
                converted.append(replace(item, proposal=replace(item.proposal, proposal_id=identifier,
                    source_refs=source_refs(row["source_refs"])),
                    affect=replace(item.affect, event_id=identifier) if item.affect else None))
            batch = {**current["active_batch"], "reflection_identity_protocol": IDENTITY_PROTOCOL}
            await connection.execute(
                "UPDATE companion.organize_state SET active_batch=%s WHERE actor_id=%s AND scope_key=%s",
                (document(batch), state["actor_id"], state["scope_key"]))
        return tuple(converted), batch

    async def block(self, state, error):
        async with self.companion.database.transaction() as connection:
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if (not current or current["revision"] != state["revision"]
                    or current["context"] != state["context"] or current["active_batch"] != state["active_batch"]):
                return {"state": "changed_since_recovery_read"}
            if current["last_error"] != AUTHORIZATION_BLOCKED:
                blocked = {"recovery": "reauthorize_original_scope", "phase": current["legacy_state"],
                    "error_code": error.code, "previous_error": current["last_error"],
                    "previous_blocked_material": current["blocked_material"],
                    "reason": "原授权已失效；原处理位置和原请求已保留，需在原权限范围恢复后继续。"}
                await connection.execute(
                    "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s",
                    (AUTHORIZATION_BLOCKED, document(blocked), state["actor_id"], state["scope_key"]))
        return self.blocked(state)

    async def resume(self, connection, current, caller):
        """Reauthorize inventory or observation, without replacing an attempt."""
        batch = current["active_batch"]
        previous = context_from_record(batch["context"] if batch else current["context"])
        if not self.same_scene(caller, previous):
            raise AppError("reflection_recovery_context_mismatch", status=403)
        refreshed = await self.reader(previous, connection=connection)
        blocked = current["blocked_material"] or {}
        previous_error = blocked.get("previous_error")
        restored = blocked.get("previous_blocked_material")
        if batch:
            # Control metadata is separate from immutable batch.context/inputs.
            # Even an authoritative absence may only be observed on this path.
            restored = {"recovery": "observe_original_attempt",
                "reason": "在原权限范围读取原请求结果；不会提交新请求或替换未知结果。"}
        next_revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,context=%s,revision=%s,updated_at=now() "
            "WHERE actor_id=%s AND scope_key=%s",
            (previous_error, document(restored),
             document(previous if not batch and current["legacy_state"] == "requests" else refreshed), next_revision,
             current["actor_id"], current["scope_key"]))
        await self.growth._enqueue(connection, current["actor_id"], current["scope_key"], next_revision,
                                   "authorization-resume")
        return next_revision

    async def _requests(self, state, context):
        materials = await self.growth.generations.pending_materials(
            context, prefix="organize:", after=state["legacy_request_cursor"], limit=PAGE_SIZE)
        entries = []
        for item in materials:
            # A frozen cut excludes new intents created during/after migration.
            if item["created_at"] > state["legacy_started_at"]:
                continue
            request = item["request"]
            if request is None:
                entries.append((item, (), state["cursor"], "invalidated", "source_unavailable"))
                continue
            try:
                episodes = tuple(frozen_episodes(request, check_dependencies=False).values())
                values = tuple(episode.sequence if type(episode.sequence) is int else
                               sequence(episode.cursor) if episode.cursor else None for episode in episodes)
                # Already consumed originals do not re-enter Growth. A legacy
                # payload without a trustworthy cursor is retained conservatively.
                if all(value is not None and value <= sequence(state["cursor"]) for value in values):
                    continue
                end = "e:" + str(max(value for value in values if value is not None)) if any(
                    value is not None for value in values) else state["cursor"]
                inputs = tuple((episode.episode_id, episode.revision) for episode in episodes)
                entries.append((item, inputs, end, "pending", None))
            except AppError as error:
                # Still observe this exact original. A known result may later be
                # rejected; an unknown must never become a newly generated intent.
                entries.append((item, (), state["cursor"], "pending", error.code))
        async with self.companion.database.transaction() as connection:
            current = await self._locked(connection, state)
            if not current or current["legacy_request_cursor"] != state["legacy_request_cursor"]:
                return
            for item, inputs, end, status, error in entries:
                request = item["request"]
                await connection.execute(
                    "INSERT INTO companion.growth_legacy_requests(actor_id,scope_key,material_key,context,inputs,"
                    "end_cursor,state,error_code,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT(actor_id,scope_key,material_key) DO NOTHING",
                    (state["actor_id"], state["scope_key"], item["material_key"],
                     document(request.context) if request else None, document(inputs), end, status, error, item["created_at"]))
            await connection.execute(
                "UPDATE companion.organize_state SET legacy_request_cursor=%s,legacy_state=%s,"
                "legacy_episode_cursor=COALESCE(legacy_episode_cursor,cursor),updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s",
                (materials[-1]["material_key"] if materials else state["legacy_request_cursor"],
                 "requests" if len(materials) == PAGE_SIZE else "episodes", state["actor_id"], state["scope_key"]))
            await self.growth._enqueue(connection, state["actor_id"], state["scope_key"], current["revision"], "legacy-requests")

    async def _episodes(self, state, context):
        page = await self.companion.memory.episodes_since(context, state["legacy_episode_cursor"], PAGE_SIZE)
        async with self.companion.database.transaction() as connection:
            current = await self._locked(connection, state)
            if not current or current["legacy_episode_cursor"] != state["legacy_episode_cursor"]:
                return
            for episode in page.items:
                if reflectable(episode):
                    await self.growth._register_input(connection, context, state["scope_key"], episode)
            missing = sorted(set(current["legacy_missing"]) | set(page.coverage.missing))
            await connection.execute(
                "UPDATE companion.organize_state SET legacy_episode_cursor=%s,legacy_state=%s,legacy_missing=%s,"
                "last_error=CASE WHEN active_batch IS NULL THEN NULL ELSE last_error END,context=%s,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s",
                (page.next_cursor or page.watermark, "episodes" if page.next_cursor else "complete",
                 missing, document(context), state["actor_id"], state["scope_key"]))
            await self.growth._enqueue(connection, state["actor_id"], state["scope_key"], current["revision"], "legacy-episodes")

    async def _locked(self, connection, state):
        current = await (await connection.execute(
            "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
            (state["actor_id"], state["scope_key"]))).fetchone()
        return current if current and current["legacy_state"] == state["legacy_state"] else None

    async def activate(self, state):
        """An original manifest entry always precedes newly assembled material."""
        async with self.companion.database.transaction() as connection:
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if current["active_batch"]:
                return current
            original = await (await connection.execute(
                "SELECT * FROM companion.growth_legacy_requests WHERE actor_id=%s AND scope_key=%s "
                "AND state='pending' ORDER BY created_at,material_key LIMIT 1",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if original is None:
                return current
            batch = {"material_key": original["material_key"], "context": original["context"],
                     "inputs": original["inputs"], "start_cursor": current["cursor"],
                     "end_cursor": original["end_cursor"] or current["cursor"],
                     "watermark": current["watermark"], "has_more": True, "legacy_material": True,
                     "identity_conversion_key": original["material_key"]}
            return await (await connection.execute(
                "UPDATE companion.organize_state SET active_batch=%s,last_error=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s RETURNING *",
                (document(batch), state["actor_id"], state["scope_key"]))).fetchone()
