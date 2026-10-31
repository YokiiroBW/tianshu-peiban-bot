"""Replay a domain command response, atomically with its owner's actual business writes."""

import hashlib
import json

from psycopg.types.json import Jsonb

from .api import CommandResult
from .errors import AppError
from .serialization import wire


def body_hash(body):
    return hashlib.sha256(json.dumps(wire(body), sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def lock_key(context, operation, client_request_id):
    return int.from_bytes(hashlib.sha256((context.principal_id+":"+operation+":"+client_request_id)
                                        .encode()).digest()[:8], "big", signed=True)


class CommandStore:
    """Does not own CAS, validation or Action/Attempt semantics; the callback does."""

    def __init__(self, database):
        self.database = database

    async def execute(self, context, operation, client_request_id, body_hash, callback, *, budget=None):
        async with self.database.transaction(budget) as connection:
            return await self.execute_on(connection, context, operation, client_request_id,
                                         body_hash, callback)

    async def execute_on(self, connection, context, operation, client_request_id, body_hash, callback):
        key = (context.principal_id, operation, client_request_id)
        await connection.execute("SELECT pg_advisory_xact_lock(%s)",
                                  (lock_key(context, operation, client_request_id),))
        old = await (await connection.execute("SELECT body_hash,result FROM runtime.commands "
            "WHERE principal_id=%s AND operation=%s AND client_request_id=%s", key)).fetchone()
        if old:
            if old["body_hash"] != body_hash:
                raise AppError("client_request_id_conflict", status=409)
            return CommandResult(**old["result"])
        result = await callback(connection)
        if not isinstance(result, CommandResult) or result.client_request_id != client_request_id:
            raise TypeError("domain_command_result_required")
        await connection.execute("INSERT INTO runtime.commands(principal_id,operation,"
            "client_request_id,body_hash,result) VALUES(%s,%s,%s,%s,%s)",
            (*key, body_hash, Jsonb(wire(result))))
        return result

    def execute_sync(self, context, operation, client_request_id, body_hash, callback, *, budget=None):
        with self.database.sync_transaction(budget) as connection:
            return self.execute_sync_on(connection, context, operation, client_request_id,
                                        body_hash, callback)

    def execute_sync_on(self, connection, context, operation, client_request_id, body_hash, callback):
        key = (context.principal_id, operation, client_request_id)
        connection.execute("SELECT pg_advisory_xact_lock(%s)",
                           (lock_key(context, operation, client_request_id),))
        old = connection.execute("SELECT body_hash,result FROM runtime.commands WHERE "
            "principal_id=%s AND operation=%s AND client_request_id=%s", key).fetchone()
        if old:
            if old["body_hash"] != body_hash:
                raise AppError("client_request_id_conflict", status=409)
            return CommandResult(**old["result"])
        result = callback(connection)
        if not isinstance(result, CommandResult) or result.client_request_id != client_request_id:
            raise TypeError("domain_command_result_required")
        connection.execute("INSERT INTO runtime.commands(principal_id,operation,client_request_id,"
            "body_hash,result) VALUES(%s,%s,%s,%s,%s)", (*key, body_hash, Jsonb(wire(result))))
        return result
