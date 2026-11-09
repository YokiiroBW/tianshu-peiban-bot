"""Fair, bounded repair of missing native consolidation wakeups."""

from tianshu.runtime.errors import AppError


async def reconcile_due(workers, *, generation, limit):
    if type(limit) is not int or not 1 <= limit <= 512:
        raise ValueError("invalid_memory_reconcile_limit")
    jobs = workers.memory.jobs
    async with workers.database.transaction() as conn:
        await jobs.assert_generation_on(conn, generation)
        await conn.execute("INSERT INTO memory.rbi_reconcile_cursor(name,data_generation) "
            "VALUES('consolidation',%s) ON CONFLICT(name) DO NOTHING", (generation,))
        cursor = await (await conn.execute("SELECT * FROM memory.rbi_reconcile_cursor "
            "WHERE name='consolidation' FOR UPDATE SKIP LOCKED")).fetchone()
        if cursor is None:
            return {"scanned": 0, "woken": 0, "complete": False, "busy": True}
        after = cursor["after_partition_id"] if cursor["data_generation"] == generation else ""
        rows = await (await conn.execute("SELECT partition_id FROM memory.consolidation_state "
            "WHERE partition_id>%s ORDER BY partition_id LIMIT %s", (after, limit))).fetchall()
        complete = len(rows) < limit
        next_cursor = "" if complete else rows[-1]["partition_id"]
        await conn.execute("UPDATE memory.rbi_reconcile_cursor SET data_generation=%s,after_partition_id=%s,"
            "updated_at=now() WHERE name='consolidation'", (generation, next_cursor))
    woken = 0
    for row in rows:
        async with workers.database.transaction() as conn:
            await jobs.assert_generation_on(conn, generation)
            state = await (await conn.execute("SELECT *,now() AS db_now FROM memory.consolidation_state "
                "WHERE partition_id=%s AND (lease_until IS NULL OR lease_until<=now()) FOR UPDATE SKIP LOCKED",
                (row["partition_id"],))).fetchone()
            if not state:
                continue
            succeeded = await workers.consolidation.retries.successful_on(conn,row["partition_id"],generation)
            if succeeded:
                # Runtime's physical attempt is already finished. Memory owns
                # the remaining application, including when another batch is active.
                await workers.consolidation._schedule_on(conn,row["partition_id"],state["db_now"],
                    suffix="reconcile-success",target_batch_id=succeeded["batch_id"])
                woken += 1
            due = None
            if state["active_batch_id"] is not None:
                active = await (await conn.execute("SELECT status,manifest FROM memory.consolidation_batches "
                    "WHERE batch_id=%s", (state["active_batch_id"],))).fetchone()
                if (active and active["status"] in {"prepared", "waiting_model", "ready"}
                        and active["manifest"].get("data_generation") == generation
                        and not active["manifest"].get("remote_wait_unresolved")):
                    due = state["db_now"]
            else:
                held = await (await conn.execute("SELECT min(COALESCE(retry_due_at,now())) AS due "
                    "FROM memory.consolidation_batches WHERE partition_id=%s AND status='quarantined' "
                    "AND manifest->>'data_generation'=%s AND NOT (manifest ? 'remote_wait_unresolved' "
                    "OR manifest ? 'original_observation_only' OR manifest ? 'recovery_budget_blocked')",
                    (row["partition_id"], generation))).fetchone()
                values = [value for value in (state["wake_due_at"], held["due"]) if value is not None]
                if values:
                    due = min(values)
            if due is None:
                continue
            lock = "memory-consolidate:" + row["partition_id"] + ":generation:" + generation
            pending = await (await conn.execute("SELECT j.id,j.task_name,j.args,j.scheduled_at,"
                "k.subject_id,k.matter_id,k.payload_version,k.data_generation FROM procrastinate_jobs j "
                "LEFT JOIN LATERAL (SELECT subject_id,matter_id,payload_version,data_generation FROM runtime.job_keys "
                "WHERE job_id=j.id AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true "
                "WHERE j.status='todo' AND j.queueing_lock=%s FOR UPDATE OF j", (lock,))).fetchone()
            if pending:
                matches = (pending["task_name"] == "memory.consolidate"
                    and pending["args"] == {"partition_id": row["partition_id"]}
                    and pending["subject_id"] is None and pending["matter_id"] is None
                    and pending["payload_version"] == 1 and pending["data_generation"] == generation)
                if not matches:
                    raise AppError("job_identity_conflict", status=409)
                if pending["scheduled_at"] is None or pending["scheduled_at"] <= due:
                    continue
            await workers.consolidation._schedule_on(conn, row["partition_id"], due, suffix="reconcile")
            woken += 1
    return {"scanned": len(rows), "woken": woken, "complete": complete}
