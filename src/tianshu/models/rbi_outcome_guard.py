"""Expose locked Models facts without transferring ownership of its ledger."""

from .rbg_admission import lock_admission_on


async def lock_recovery_facts_on(connection, attempt_id):
    """Read the original and latest cancellation under the caller's transaction.

    The shared admission lock also serializes an as-yet-absent cancellation.
    Locks stay held until the caller commits or rolls back. The Runtime/domain
    owner checks the returned facts, sources, lineage, generation and budget.
    """
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("invalid_model_attempt_id")
    # request_cancel acquires this same advisory lock before the attempt row.
    await lock_admission_on(connection, attempt_id)
    row = await (await connection.execute(
        "SELECT a.attempt_id,a.intent_id,a.status,a.dispatch,a.error_code,a.finished_at,a.data_generation,"
        "s.request_digest,c.reason AS cancellation_reason FROM models.attempts a "
        "LEFT JOIN models.submissions s USING(attempt_id) "
        "LEFT JOIN models.cancellations c USING(attempt_id) "
        "WHERE a.attempt_id=%s FOR UPDATE OF a", (attempt_id,))).fetchone()
    if row is None:
        return None
    facts = dict(row)
    if facts["data_generation"] is not None:
        facts["data_generation"] = str(facts["data_generation"])
    return facts
