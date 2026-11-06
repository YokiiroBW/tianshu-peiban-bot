"""Archive an observed incident episode without claiming recovery."""

from tianshu.runtime.work_retirement import generation_on, record_on


UNSETTLED = frozenset({"unknown", "outcome_unknown", "timeout_unknown"})


async def archive_incident_on(connection, target, **request):
    await generation_on(connection, request["generation"])
    row = await (await connection.execute(
        "SELECT * FROM operations.incidents WHERE incident_id=%s FOR UPDATE",
        (target["incident_id"],))).fetchone()
    if not row or row["state"] == "resolved":
        return {"state": "skipped", "reason": "incident_not_open"}
    if any(row[k] != target[k] for k in ("revision", "episode", "occurrences")) or str(row["last_seen"]) != target["last_seen"]:
        return {"state": "skipped", "reason": "incident_changed"}
    if row["error_family"] in UNSETTLED:
        return {"state": "skipped", "reason": "current_or_unknown_problem_retained"}
    # Serialize with the outbox claim before deciding whether a send is active.
    await (await connection.execute(
        "SELECT notification_id FROM operations.notification_outbox WHERE incident_id=%s FOR UPDATE",
        (row["incident_id"],))).fetchall()
    active = await (await connection.execute(
        "SELECT 1 FROM operations.recoveries WHERE incident_id=%s "
        "AND state IN ('requested','inspecting','executing','observing','unknown') UNION ALL "
        "SELECT 1 FROM operations.diagnostics WHERE incident_id=%s AND status IN ('submitting','queued','running','unknown') UNION ALL "
        "SELECT 1 FROM operations.notification_outbox WHERE incident_id=%s AND status IN ('sending','unknown','observing') LIMIT 1",
        (row["incident_id"], row["incident_id"], row["incident_id"]))).fetchone()
    if active:
        return {"state": "skipped", "reason": "incident_work_active"}
    return await record_on(connection, owner="operations",
        key={"incident_id": row["incident_id"], "episode": row["episode"]},
        fingerprint=f"{row['revision']}:{row['occurrences']}:{row['last_seen']}", **request)
