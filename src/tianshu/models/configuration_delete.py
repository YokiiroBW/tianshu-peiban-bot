"""CAS deletion of model configuration, retaining all actual execution evidence."""

from dataclasses import replace

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from .ledger import digest


async def delete_configuration(services, context, operation, body):
    from .management import configuration, text

    expected = body.get("expected_revision")
    if type(expected) is not int or expected < 1:
        raise AppError("expected_revision_required")
    if operation == "bindings" and (body.get("actor_id") or None) != context.actor_id:
        raise AppError("binding_actor_context_required", status=403)

    async def apply(conn):
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('models.configuration',0))"
        )
        if operation in {"providers", "routes"}:
            key = "provider_id" if operation == "providers" else "route_id"
            identifier = text(body, key)
            # Table and column names come only from the two literals above.
            row = await (
                await conn.execute(
                    f"SELECT revision FROM models.{operation} WHERE {key}=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if not row or row["revision"] != expected:
                raise AppError("revision_conflict", status=409)
            routes = (
                [identifier]
                if operation == "routes"
                else [
                    item["route_id"]
                    for item in await (
                        await conn.execute(
                            "SELECT route_id FROM models.routes WHERE provider_id=%s ORDER BY route_id FOR UPDATE",
                            (identifier,),
                        )
                    ).fetchall()
                ]
            )
            if await (
                await conn.execute(
                    "SELECT 1 FROM models.embedding_profile WHERE route_id=ANY(%s)", (routes,)
                )
            ).fetchone():
                raise AppError("embedding_profile_in_use", status=409)
            await conn.execute("DELETE FROM models.bindings WHERE route_id=ANY(%s)", (routes,))
            await conn.execute("UPDATE models.bindings SET fallback_routes=ARRAY(SELECT candidate_id FROM "
                "unnest(fallback_routes) AS choices(candidate_id) WHERE NOT candidate_id=ANY(%s)),revision=revision+1 "
                "WHERE fallback_routes && %s::text[]", (routes, routes))
            await conn.execute("DELETE FROM models.routes WHERE route_id=ANY(%s)", (routes,))
            if operation == "providers":
                await conn.execute(
                    "DELETE FROM models.providers WHERE provider_id=%s", (identifier,)
                )
                await conn.execute(
                    "DELETE FROM runtime.credentials WHERE reference=%s",
                    ("sealed:provider:" + identifier,),
                )
        elif operation == "bindings":
            current = await (await conn.execute(
                "SELECT revision FROM models.bindings WHERE function=%s AND actor_id=%s "
                "ORDER BY CASE service_class WHEN 'interactive' THEN 0 WHEN 'elastic' THEN 1 ELSE 2 END LIMIT 1",
                (text(body, "function"), body.get("actor_id") or ""),
            )).fetchone()
            if not current or current["revision"] != expected:
                raise AppError("revision_conflict", status=409)
            changed = await (
                await conn.execute(
                    "DELETE FROM models.bindings WHERE function=%s AND actor_id=%s RETURNING revision",
                    (
                        text(body, "function"),
                        body.get("actor_id") or "",
                    ),
                )
            ).fetchone()
            if not changed:
                raise AppError("revision_conflict", status=409)
        elif operation == "embedding-profile":
            changed = await (
                await conn.execute(
                    "DELETE FROM models.embedding_profile WHERE singleton AND revision=%s RETURNING revision",
                    (expected,),
                )
            ).fetchone()
            if not changed:
                raise AppError("revision_conflict", status=409)
        else:
            raise AppError("unknown_model_operation", status=404)
        from .configuration_reload import record_pending_on

        await record_pending_on(conn, context, "models." + operation + ".delete",
                                body["client_request_id"], expected + 1)
        return CommandResult(
            body["client_request_id"],
            revision=expected + 1,
            data={"deleted": True, "catalog": await configuration(conn, services.settings)},
        )

    async def replay(conn, original):
        return replace(
            original,
            data={"deleted": True, "catalog": await configuration(conn, services.settings)},
        )

    return await services.commands.execute(
        context,
        "models." + operation + ".delete",
        body["client_request_id"],
        digest(body),
        apply,
        on_replay=replay,
    )
