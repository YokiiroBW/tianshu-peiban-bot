"""Durable broker reload work, tied to the original configuration command."""

from tianshu.runtime.errors import AppError
from .ledger import digest


def event_id(context, operation, client_request_id):
    return "model-config:" + digest({"principal": context.principal_id, "operation": operation,
                                     "client_request_id": client_request_id})


async def record_pending_on(connection, context, operation, client_request_id, revision):
    identity = event_id(context, operation, client_request_id)
    await connection.execute("INSERT INTO models.configuration_reloads(event_id,operation,revision,state,error_code) "
        "VALUES(%s,%s,%s,'pending','broker_reload_pending') ON CONFLICT(event_id) DO NOTHING",
        (identity, operation, revision))
    return identity


async def status(database, identity):
    async with database.transaction(control=True, read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM models.configuration_reloads WHERE event_id=%s",
                                         (identity,))).fetchone()


async def pending(database, *, limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise AppError("invalid_reload_limit")
    async with database.transaction(control=True, read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM models.configuration_reloads WHERE state='pending' "
            "ORDER BY created_at,event_id LIMIT %s", (limit,))).fetchall()


async def mark_applied(database, identity, revision):
    async with database.transaction(control=True) as conn:
        row = await (await conn.execute("UPDATE models.configuration_reloads SET state='applied',"
            "error_code=NULL,applied_at=COALESCE(applied_at,now()) "
            "WHERE event_id=%s AND revision=%s RETURNING *", (identity, revision))).fetchone()
        if row is None:
            raise AppError("model_configuration_reload_revision_conflict", status=409)
        return row
