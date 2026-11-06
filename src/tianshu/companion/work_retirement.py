"""Retire failed reflection work without reviewing its inputs or rewriting its receipts."""

from tianshu.runtime.work_retirement import generation_on, record_on


async def retire_failed_batch_on(connection, target, **request):
    await generation_on(connection, request["generation"])
    key = {name: target[name] for name in ("actor_id", "scope_key", "retry_key")}
    args = tuple(key.values())
    attempts = await (await connection.execute(
        "SELECT a.attempt_id,a.status,a.dispatch FROM models.attempts a "
        "WHERE a.attempt_id IN (SELECT h->>'attempt_id' FROM companion.growth_retry_batches r,"
        "jsonb_array_elements(r.history) h WHERE r.actor_id=%s AND r.scope_key=%s AND r.retry_key=%s) "
        "ORDER BY a.attempt_id FOR SHARE", args)).fetchall()
    if not attempts or any(a["status"] in {"queued", "running", "unknown"}
            or a["dispatch"] == "unknown" for a in attempts):
        return {"state": "skipped", "reason": "original_model_unsettled"}
    partition = await (await connection.execute(
        "SELECT active_batch FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR SHARE",
        args[:2])).fetchone()
    row = await (await connection.execute(
        "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s "
        "FOR UPDATE", args)).fetchone()
    if not row or row["state"] != "blocked" or row["waiting_on_original"]:
        return {"state": "skipped", "reason": "original_not_settled_failure"}
    if row["revision"] != target["revision"]:
        return {"state": "skipped", "reason": "original_changed"}
    expected_ids = {h.get("attempt_id") for h in row["history"]}
    if None in expected_ids or expected_ids != {a["attempt_id"] for a in attempts}:
        return {"state": "skipped", "reason": "original_model_unavailable"}
    if row["batch"].get("runtime_generation") != request["generation"]:
        return {"state": "skipped", "reason": "original_generation_unavailable"}
    if (row["batch"].get("rbi_review") or {}).get("state") == "requested":
        return {"state": "skipped", "reason": "review_requested"}
    active = (partition or {}).get("active_batch") or {}
    if active and (active.get("retry_key") == row["retry_key"]
                   or active.get("material_key") == row["current_material_key"]):
        return {"state": "skipped", "reason": "original_batch_active"}
    return await record_on(connection, owner="growth", key=key, fingerprint=str(row["revision"]), **request)
