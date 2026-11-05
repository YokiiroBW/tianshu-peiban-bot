"""Lock source dependencies before a mutable companion row.

Withdrawal locks Sources before calling each dependant. Writers use the same
order and reject a changed snapshot instead of locking a newly introduced
source while already holding the dependant row.
"""
import re
from datetime import UTC, datetime, timedelta

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
from tianshu.sources.dependencies import unique_refs
from .codec import signature, source_refs


class MaterialSnapshotChanged(RecoveryWait):
    """An internal source-lock snapshot changed, not a caller's expected revision."""

    def __init__(self, revision=None):
        super().__init__("revision_conflict", datetime.now(UTC) + timedelta(seconds=5),
                         dependency="material_snapshot")
        self.message = "内容已更新，请读取当前版本"
        self.details = {"revision": revision}


async def retry_material_snapshot(operation, *, attempts=3):
    """Retry only the marked race after the entire local transaction rolled back."""
    for index in range(attempts):
        try:
            return await operation()
        except MaterialSnapshotChanged as error:
            if (getattr(error, "transaction_rollback_confirmed", None) is not True
                    or not recovery_wait_retryable(error) or index + 1 == attempts):
                raise


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
        raise MaterialSnapshotChanged(current.get("revision") if current else None)
    # Related revisions are immutable; the owning row pins their identity.
    return current
