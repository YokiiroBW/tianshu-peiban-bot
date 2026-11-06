"""Append a user's disposition without changing execution or business outcomes."""

from psycopg.types.json import Jsonb

from .errors import AppError


async def generation_on(connection, expected):
    row = await (await connection.execute(
        "SELECT id::text AS id FROM runtime.installation_generation WHERE singleton FOR SHARE"
    )).fetchone()
    if not row or row["id"] != expected:
        raise AppError("job_generation_expired", status=409)


async def recorded_on(connection, owner, key):
    return await (await connection.execute(
        "SELECT r.* FROM runtime.user_work_retirements r JOIN runtime.installation_generation g "
        "ON g.singleton AND g.id::text=r.data_generation WHERE r.owner=%s AND r.object_key=%s",
        (owner, Jsonb(key)))).fetchone()


async def record_on(connection, *, owner, key, generation, fingerprint, request_ref, requested_by, apply):
    previous = await recorded_on(connection, owner, key)
    if previous:
        return {"state": "already_retired", "retired_at": previous["retired_at"]}
    if not apply:
        return {"state": "eligible"}
    await connection.execute(
        "INSERT INTO runtime.user_work_retirements "
        "(data_generation,owner,object_key,request_ref,requested_by,original_fingerprint) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (generation, owner, Jsonb(key), request_ref, requested_by, fingerprint))
    saved = await recorded_on(connection, owner, key)
    return {"state": "retired", "retired_at": saved["retired_at"]}


async def retire_failed_job_on(connection, target, **request):
    """Retire a known failed native job; never delete its events or reset its key."""
    await generation_on(connection, request["generation"])
    row = await (await connection.execute(
        "SELECT id,status,attempts,md5(args::text) AS args_digest FROM procrastinate_jobs "
        "WHERE id=%s FOR UPDATE", (target["id"],))).fetchone()
    if not row or row["status"] != "failed":
        return {"state": "skipped", "reason": "original_not_failed"}
    if row["attempts"] != target["attempts"] or row["args_digest"] != target["args_digest"]:
        return {"state": "skipped", "reason": "original_changed"}
    protected = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE job_id=%s LIMIT 1", (row["id"],))).fetchone()
    if protected:
        return {"state": "skipped", "reason": "execution_proof_retained"}
    return await record_on(connection, owner="runtime", key={"job_id": row["id"]},
        fingerprint=f"{row['attempts']}:{row['args_digest']}", **request)
