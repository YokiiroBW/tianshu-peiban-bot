"""Lock source dependencies before a mutable companion row.

Withdrawal locks Sources before calling each dependant. Writers use the same
order and reject a changed snapshot instead of locking a newly introduced
source while already holding the dependant row.
"""
import re

from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from .codec import signature, source_refs


async def lock_material_row(connection, sources, context, query, parameters=(), *,
                            additional_refs=(), related_refs=None):
    unlocked = re.sub(r"\s+FOR UPDATE(?:\s+OF\s+[a-zA-Z_][a-zA-Z_0-9]*)?\s*$", "", query)
    if unlocked == query:
        raise ValueError("A material row query must end with FOR UPDATE")
    snapshot = await (await connection.execute(unlocked, parameters)).fetchone()
    dependencies = source_refs(snapshot.get("source_refs", ())) if snapshot else ()
    if snapshot and related_refs:
        dependencies = unique_refs(dependencies, await related_refs(snapshot, connection))
    dependencies = unique_refs(dependencies, additional_refs)
    if not await sources.validate_persistence(connection, context, dependencies):
        raise AppError("source_unavailable", "材料已失效，请重新读取当前内容", status=409)
    current = await (await connection.execute(query, parameters)).fetchone()
    if signature(snapshot) != signature(current):
        raise AppError("revision_conflict", "内容已更新，请读取当前版本", status=409,
                       details={"revision": current.get("revision") if current else None})
    # Related revisions are immutable; the owning row pins their identity.
    return current
