"""User retirement of failed consolidation work; episode content and coverage stay unchanged."""

from tianshu.runtime.work_retirement import generation_on, record_on


async def retire_failed_batch_on(connection, target, **request):
    await generation_on(connection, request["generation"])
    snapshot = await (await connection.execute(
        "SELECT partition_id FROM memory.consolidation_batches WHERE batch_id=%s", (target["batch_id"],))).fetchone()
    if not snapshot:
        return {"state": "skipped", "reason": "original_not_failed"}
    # Lock Models facts before the domain row, as in normal recovery.
    attempts = await (await connection.execute(
        "SELECT a.attempt_id,a.status,a.dispatch FROM models.attempts a "
        "JOIN memory.consolidation_attempts c ON c.model_attempt_id=a.attempt_id "
        "WHERE c.batch_id=%s ORDER BY a.attempt_id FOR SHARE OF a", (target["batch_id"],))).fetchall()
    expected_attempts = await (await connection.execute(
        "SELECT count(DISTINCT model_attempt_id) AS count FROM memory.consolidation_attempts WHERE batch_id=%s",
        (target["batch_id"],))).fetchone()
    if len(attempts) != expected_attempts["count"]:
        return {"state": "skipped", "reason": "original_model_unavailable"}
    if any(a["status"] in {"queued", "running", "unknown"} or a["dispatch"] == "unknown" for a in attempts):
        return {"state": "skipped", "reason": "original_model_unsettled"}
    active = await (await connection.execute(
        "SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s FOR SHARE",
        (snapshot["partition_id"],))).fetchone()
    row = await (await connection.execute(
        "SELECT b.*,md5(b.manifest::text) AS manifest_digest FROM memory.consolidation_batches b "
        "WHERE b.batch_id=%s FOR UPDATE", (target["batch_id"],))).fetchone()
    if not row or row["status"] != "failed":
        return {"state": "skipped", "reason": "original_not_failed"}
    if row["manifest_digest"] != target["manifest_digest"] or str(row["updated_at"]) != target["updated_at"]:
        return {"state": "skipped", "reason": "original_changed"}
    if row["manifest"].get("data_generation") != request["generation"]:
        return {"state": "skipped", "reason": "original_generation_unavailable"}
    if row["manifest"].get("remote_wait_unresolved") or row["manifest"].get("original_observation_only"):
        return {"state": "skipped", "reason": "original_model_unsettled"}
    if row["step_ids"] and not attempts:
        return {"state": "skipped", "reason": "original_model_unavailable"}
    if active and active["active_batch_id"] == row["batch_id"]:
        return {"state": "skipped", "reason": "original_batch_active"}
    return await record_on(connection, owner="memory", key={"batch_id": row["batch_id"]},
        fingerprint=row["manifest_digest"], **request)
