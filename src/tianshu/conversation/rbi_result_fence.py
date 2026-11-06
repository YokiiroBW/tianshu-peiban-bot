"""Build a transaction fence from the authoritative Action result head."""

from tianshu.capabilities.actions import result_sources
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import current_execution_generation
from tianshu.sources.dependencies import unique_refs

from .codec import receipt, source_refs


def combine_result_fences(*fences):
    leaves = [item for fence in fences if fence for item in getattr(fence, "components", (fence,))]
    ordered = sorted(leaves, key=lambda fence: getattr(fence, "action_id", ""))
    if not ordered:
        return None

    async def fence(connection):
        for item in ordered:
            await item(connection)

    fence.source_refs = unique_refs(*(getattr(item, "source_refs", ()) for item in ordered))
    fence.action_id = getattr(ordered[0], "action_id", "") if len(ordered) == 1 else ""
    fence.action_ids = frozenset(action_id for item in ordered for action_id in
        (getattr(item, "action_ids", ()) or (getattr(item, "action_id", ""),)) if action_id)
    fence.components = tuple(ordered)
    return fence


async def action_result_fence(database, jobs, actions, context, result, *, result_revision=None,
                              refresh=False):
    if result is None or not result.action_id:
        return result, result_revision, None
    if actions is None:
        raise AppError("action_head_unavailable", status=503)
    try:
        current = await actions.get(result.action_id, context)
    except AppError as error:
        if error.status == 404:
            raise AppError("action_head_unavailable", status=503) from error
        raise
    if refresh:
        result = receipt(current.get("receipt"))
        if result is None:
            raise AppError("result_head_unavailable", status=409)
        result_revision = current["result_revision"]
    elif result_revision is None:
        result_revision = current["result_revision"]
    refs = unique_refs(source_refs(current["action"].get("source_refs", ())), result_sources(result))
    async with database.transaction(read_only=True) as connection:
        generation = current_execution_generation() or await jobs.current_generation_on(connection)

    async def fence(connection):
        await actions.assert_result_head_on(connection, context, action_id=result.action_id,
            expected_receipt=result, result_revision=result_revision, data_generation=generation, source_refs=refs)

    fence.source_refs = refs
    fence.action_id = result.action_id
    fence.action_ids = frozenset({result.action_id})
    return result, result_revision, fence
