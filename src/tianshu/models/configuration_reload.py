"""Durable broker reload work, tied to the original configuration command."""

from collections.abc import Mapping
from datetime import datetime

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


def _cursor_key(value):
    try:
        if not isinstance(value, Mapping) or not isinstance(value["created_at"], str):
            raise ValueError
        created_at = datetime.fromisoformat(value["created_at"])
        identity = value["event_id"]
        if created_at.tzinfo is None or created_at.utcoffset() is None or not isinstance(identity, str) or not identity:
            raise ValueError
        return created_at, identity
    except (KeyError, TypeError, ValueError):
        raise AppError("invalid_reload_cursor") from None


def _cursor_value(key):
    return {"created_at": key[0].isoformat(), "event_id": key[1]}


async def pending(database, *, limit=100, cursor=None):
    """Page one finite ordered sweep; restart without a cursor to retry pending work."""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise AppError("invalid_reload_limit")
    if cursor is not None:
        if not isinstance(cursor, Mapping):
            raise AppError("invalid_reload_cursor")
        after = _cursor_key(cursor.get("after"))
        through = _cursor_key(cursor.get("through"))
    else:
        after = None
    async with database.transaction(control=True, read_only=True) as conn:
        if cursor is None:
            last = await (await conn.execute("SELECT created_at,event_id FROM models.configuration_reloads "
                "WHERE state='pending' ORDER BY created_at DESC,event_id DESC LIMIT 1")).fetchone()
            if last is None:
                return {"items": [], "next_cursor": None}
            through = (last["created_at"], last["event_id"])
        query = ("SELECT * FROM models.configuration_reloads WHERE state='pending' "
                 "AND (created_at,event_id) <= (%s,%s) ")
        args = [*through]
        if after is not None:
            query += "AND (created_at,event_id) > (%s,%s) "
            args.extend(after)
        query += "ORDER BY created_at,event_id LIMIT %s"
        args.append(limit + 1)
        rows = await (await conn.execute(query, tuple(args))).fetchall()
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = {"after": _cursor_value((last["created_at"], last["event_id"])),
                       "through": _cursor_value(through)}
    return {"items": items, "next_cursor": next_cursor}


async def mark_applied(database, identity, revision):
    async with database.transaction(control=True) as conn:
        row = await (await conn.execute("UPDATE models.configuration_reloads SET state='applied',"
            "error_code=NULL,applied_at=COALESCE(applied_at,now()) "
            "WHERE event_id=%s AND revision=%s RETURNING *", (identity, revision))).fetchone()
        if row is None:
            raise AppError("model_configuration_reload_revision_conflict", status=409)
        return row
