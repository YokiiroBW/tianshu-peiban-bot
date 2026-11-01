"""One-time bounded conversion of pre-version-ledger Growth checkpoints.

GenerationRequests owns original prompts/attempts. This manifest only references
them, and prevents a newly assembled batch from replacing an unresolved original.
The old sequence cursor is used once to seed history, never as ongoing coverage.
"""

from tianshu.conversation.codec import context_from_record, document
from tianshu.runtime.errors import AppError
from .growth_materials import reflectable
from .reflection_output import frozen_episodes


PAGE_SIZE = 128


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

    async def step(self, state):
        """Read at most one page, persist progress, and use the existing job queue."""
        context = context_from_record(state["context"])
        await self.companion.identity.check(context)
        if state["legacy_state"] == "requests":
            await self._requests(state, context)
        elif state["legacy_state"] == "episodes":
            await self._episodes(state, context)

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
                "last_error=CASE WHEN active_batch IS NULL THEN NULL ELSE last_error END,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s",
                (page.next_cursor or page.watermark, "episodes" if page.next_cursor else "complete",
                 missing, state["actor_id"], state["scope_key"]))
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
                     "watermark": current["watermark"], "has_more": True, "legacy_material": True}
            return await (await connection.execute(
                "UPDATE companion.organize_state SET active_batch=%s,last_error=NULL,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s RETURNING *",
                (document(batch), state["actor_id"], state["scope_key"]))).fetchone()
