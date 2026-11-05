"""Domain checkpoints live in the original Action and follow its source fence."""

import json

from .contracts import Fault, canonical
from .execution_context import CURRENT_EXECUTION
from .withdrawal import depend, validate_sources
from tianshu.sources.dependencies import unique_refs


async def load(database, action, *, force=False, field="video_observation"):
    if CURRENT_EXECUTION.get() is None and not force:
        return {}
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT document FROM capabilities.actions WHERE id=%s", (action.action_id,))).fetchone()
    if not row:
        raise Fault("not_found", 404)
    return json.loads(row["document"]).get(field, {})


async def save(database, sources, action, attempt, update, refs, *, field="video_observation"):
    execution = CURRENT_EXECUTION.get()
    if execution is None:
        return
    refs = unique_refs(action.source_refs, tuple(refs))
    async with database.transaction() as conn:
        if not await validate_sources(conn, action.context, refs, sources):
            raise Fault("source_revoked", 410)
        row = await (await conn.execute(
            "SELECT document,attempt,receipt,execution_owner,EXISTS(SELECT 1 FROM capabilities.execution_owners o "
            "WHERE o.id=capabilities.actions.execution_owner AND o.expires_at>now() AND o.stopped_at IS NULL) AS owner_live "
            "FROM capabilities.actions WHERE id=%s FOR UPDATE",
            (action.action_id,))).fetchone()
        if not row or row["attempt"]["attempt_id"] != attempt.attempt_id:
            raise Fault("attempt_changed", 409)
        document = json.loads(row["document"])
        await execution["owner"].jobs.assert_generation_on(conn, document.get("data_generation"))
        if (document.get("source_revoked") or document.get("cancel_requested")
                or row["receipt"]["execution"] != "running"
                or not row["owner_live"]
                or row["execution_owner"] != execution["owner"].owners.id):
            raise Fault("action_checkpoint_owner_changed", 409)
        checkpoint = {**document.get(field, {}), **update}
        document[field] = checkpoint
        await depend(conn, "action", action.action_id, refs)
        await conn.execute("UPDATE capabilities.actions SET document=%s WHERE id=%s",
                           (canonical(document), action.action_id))
