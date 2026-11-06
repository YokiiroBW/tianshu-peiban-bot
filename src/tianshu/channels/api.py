"""Management uses trusted Web context; native transports authenticate independently."""

import hashlib
import hmac
import json
from uuid import uuid5, NAMESPACE_URL
from aiohttp import web
from tianshu.types import Action, SourceRef
from tianshu.runtime.api import require_context, command_body, CommandResult, json_response
from tianshu.runtime.serialization import wire
from tianshu.capabilities.contracts import canonical, digest
from .contracts import ChannelError
from .onebot.registry import matrix


async def bounded_body(request, maximum=4 * 1024 * 1024):
    result = bytearray()
    async for chunk in request.content.iter_chunked(65536):
        result.extend(chunk)
        if len(result) > maximum:
            raise ChannelError("channel_event_budget", 413)
    return bytes(result)


def bearer(header, token):
    return hmac.compare_digest(header or "", "Bearer " + token)


def register_routes(app, service):
    async def view(request):
        context = require_context(request, "external.manage")
        await service.identity.check_context(context)
        async with service.runtime.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute("SELECT body FROM channels.connections ORDER BY id")
            ).fetchall()
            observations = await (
                await conn.execute(
                    "SELECT connection_id,kind,name,body,observed_at FROM channels.observations ORDER BY observed_at DESC LIMIT 100"
                )
            ).fetchall()
            owner = await (
                await conn.execute(
                    "SELECT owner_id,epoch,expires_at,expires_at>now() AS active FROM channels.owners WHERE id='transport'"
                )
            ).fetchone()
            deliveries = await (
                await conn.execute(
                    "SELECT expression_id,action_id,conversation_id FROM channels.expressions WHERE principal_id=%s ORDER BY expression_id LIMIT 100",
                    (context.principal_id,),
                )
            ).fetchall()
        allowed = []
        actor_contexts = {}
        for row in rows:
            try:
                actor_id = row["body"]["actor_id"]
                if context.actor_id and actor_id != context.actor_id:
                    continue
                # Settings has a global context. Resolve each role through
                # Identity before applying the existing connection check;
                # a global actor=None is not a channel's role identity.
                if context.actor_id is None:
                    if actor_id not in actor_contexts:
                        actor_contexts[actor_id] = await service.identity.context(
                            context.principal_id, actor_id=actor_id,
                            permission="external.manage",
                        )
                    connection_context = actor_contexts[actor_id]
                else:
                    connection_context = context
                await service.identity.connection(connection_context, row["body"]["id"])
            except Exception as error:
                if (getattr(error, "status", None) in {403, 404}
                        or getattr(error, "code", None) == "actor_disabled"):
                    continue
                raise
            connection = dict(row["body"])
            seen = {
                item["name"]: item["observed_at"].isoformat()
                for item in observations
                if item["connection_id"] == connection["id"]
                and item["kind"] == "api"
                and item["body"].get("verified")
                and item["body"].get("connection_revision") == connection["revision"]
            }
            if connection["adapter"] != "telegram":
                connection["matrix"] = matrix({**connection, "observations": {"apis": seen}})
            else:
                from .telegram import BOT_API_METHODS

                connection["matrix"] = {
                    "standard": "telegram.bot-api",
                    "apis": [
                        {
                            "name": name,
                            "implemented": True,
                            "provider_supported": connection.get("api_support", {}).get(name),
                            "verified_at": seen.get(name),
                        }
                        for name in sorted(BOT_API_METHODS)
                    ],
                }
            allowed.append(connection)
        ids = {row["id"] for row in allowed}
        return json_response(
            {
                "connections": allowed,
                "matrix": matrix(),
                "owner": owner,
                "deliveries": deliveries,
                "observations": [row for row in observations if row["connection_id"] in ids],
                "adapters": [
                    {
                        "id": "onebot",
                        "implemented": True,
                        "transports": ["http", "reverse_http", "ws", "reverse_ws"],
                    },
                    {
                        "id": "telegram",
                        "implemented": True,
                        "transports": ["telegram_webhook", "telegram_poll"],
                    },
                    {
                        "id": "nonebot",
                        "implemented": True,
                        "transport": "bridge",
                        "sdk_version": "2.5.0",
                    },
                    {
                        "id": "astrbot",
                        "implemented": True,
                        "transport": "bridge",
                        "sdk_version": "4.27.3",
                    },
                ],
            }
        )

    async def command(request):
        context = require_context(request, "external.manage")
        body = await command_body(request)
        operation = request.match_info["operation"]
        name = {
            "connections/save": "configure",
            "connections/probe": "probe",
            "actions/invoke": "api",
        }.get(operation)
        if name is None:
            raise ChannelError("channel_operation", 404)
        body = {
            key: value
            for key, value in body.items()
            if key not in {"actor_id", "person_id", "conversation_id", "current_input_refs"}
        }
        identifier = str(
            uuid5(
                NAMESPACE_URL,
                canonical([context.principal_id, "channels." + name, body["client_request_id"]]),
            )
        )
        arguments = {
            key: value
            for key, value in body.items()
            if key
            not in {
                "client_request_id",
                "actor_id",
                "conversation_id",
                "source_refs",
                "current_input_refs",
            }
        }
        sealed = await service.configuration.seal_command(context, identifier, arguments)
        action = Action(
            identifier,
            body["client_request_id"],
            "channels." + name,
            context.actor_id,
            context,
            sealed,
            context.origin_kind,
            source_refs=tuple(SourceRef(**x) for x in body.get("source_refs", [])),
        )
        receipt = await service.actions.invoke(action, background=False)
        data = dict(receipt.result)
        sensitive = data.get("sensitive_result_ref")
        if sensitive:
            row = await service.repository.call(sensitive)
            if row["action_id"] != receipt.action_id:
                raise ChannelError("channel_result_identity", 409)
            data["data"] = service.repository.outcome(row, include_data=True).data
        response = json_response(
            CommandResult(
                body["client_request_id"],
                revision=data.get("revision"),
                action_id=receipt.action_id,
                attempt_id=receipt.attempt_id,
                receipt=receipt,
                data=data,
            )
        )
        if sensitive:
            response.headers["Cache-Control"] = "no-store, private"
        return response

    async def onebot_events(request):
        connection = await service.repository.connection(request.match_info["connection_id"])
        if connection["transport"] not in {"reverse_http", "http"}:
            raise ChannelError("channel_transport_mismatch", 409)
        token = await service.repository.token(connection)
        raw = await bounded_body(request)
        signature = request.headers.get("X-Signature")
        valid = signature is not None and hmac.compare_digest(
            signature, "sha1=" + hmac.new(token.encode(), raw, hashlib.sha1).hexdigest()
        )
        if not valid and not bearer(request.headers.get("Authorization"), token):
            raise ChannelError("channel_authentication", 401)
        try:
            body = json.loads(raw)
        except ValueError:
            raise ChannelError("channel_invalid_json", 400) from None
        await service.ensure_owner()
        result = await service.accept_event(connection["id"], body)
        # An empty quick-operation response never causes an implicit second reply.
        return json_response(result)

    async def reverse_ws(request):
        connection = await service.repository.connection(request.match_info["connection_id"])
        if connection["transport"] != "reverse_ws":
            raise ChannelError("channel_transport_mismatch", 409)
        token = await service.repository.token(connection)
        if not bearer(request.headers.get("Authorization"), token):
            raise ChannelError("channel_authentication", 401)
        if request.headers.get("X-Self-ID") != connection["self_external_id"]:
            raise ChannelError("channel_account_mismatch", 403)
        role = request.headers.get("X-Client-Role", "Universal")
        if role not in {"Universal", "API", "Event"}:
            raise ChannelError("onebot_ws_role", 400)
        await service.ensure_owner()
        transport = service.transports.get(connection["id"])
        if transport is None:
            raise ChannelError("channel_transport_unavailable", 503)
        socket = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await socket.prepare(request)
        await transport.attach(socket, role)
        return socket

    async def telegram_webhook(request):
        connection = await service.repository.connection(request.match_info["connection_id"])
        if connection["transport"] != "telegram_webhook":
            raise ChannelError("channel_transport_mismatch", 409)
        reference = connection.get("webhook_secret_ref")
        expected = (
            await service.runtime.credentials.resolve(reference)
            if reference
            else await service.repository.token(connection)
        )
        if not hmac.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), expected
        ):
            raise ChannelError("channel_authentication", 401)
        await service.ensure_owner()
        result = await service.accept_event(
            connection["id"], json.loads(await bounded_body(request))
        )
        return json_response(result)

    async def bridge_events(request):
        connection = await service.repository.connection(request.match_info["connection_id"])
        if connection["transport"] != "bridge":
            raise ChannelError("channel_transport_mismatch", 409)
        token = await service.repository.token(connection)
        if not bearer(request.headers.get("Authorization"), token):
            raise ChannelError("channel_authentication", 401)
        body = json.loads(await bounded_body(request))
        if body.get("protocol") != "tianshu.channel-bridge/2" or body.get(
            "host_instance_id"
        ) != connection.get("bridge_instance_id"):
            raise ChannelError("bridge_identity_mismatch", 403)
        ownership = body.get("ownership")
        if (
            not isinstance(ownership, dict)
            or (not ownership.get("dispatch_completed") and ownership.get("reply_owner") != "none")
            or ownership.get("reply_owner") not in {"tianshu", "external", "none"}
        ):
            raise ChannelError("bridge_dispatch_evidence_required", 409)
        await service.ensure_owner()
        result = await service.accept_event(connection["id"], body["event"], ownership=ownership)
        return json_response(result)

    async def bridge_receipt(request):
        connection = await service.repository.connection(request.match_info["connection_id"])
        if connection["transport"] != "bridge":
            raise ChannelError("channel_transport_mismatch", 409)
        token = await service.repository.token(connection)
        if not bearer(request.headers.get("Authorization"), token):
            raise ChannelError("channel_authentication", 401)
        body = json.loads(await bounded_body(request))
        if body.get("protocol") != "tianshu.channel-bridge/2" or body.get(
            "host_instance_id"
        ) != connection.get("bridge_instance_id"):
            raise ChannelError("bridge_identity_mismatch", 403)
        row = await service.repository.call(body["call_id"])
        if row["connection_id"] != connection["id"]:
            raise ChannelError("bridge_identity_mismatch", 403)
        from .transports import onebot_result
        from .contracts import TransportResult

        result = (
            onebot_result(body["response"])
            if body.get("state") == "confirmed"
            else TransportResult(
                "rejected" if body.get("state") == "rejected" else "unknown",
                error_code=(body.get("response") or {}).get("error_code", "sdk_unknown"),
                raw=body.get("response") or {},
            )
        )
        await service.ensure_owner()
        await service.late_response(connection["id"], body["call_id"], result)
        return json_response({"accepted": True})

    async def internal(request):
        reference = service.settings.get("owner_token_ref")
        if not reference:
            raise ChannelError("channel_owner_not_configured", 503)
        expected = await service.runtime.credentials.resolve(reference)
        if not bearer(request.headers.get("Authorization"), expected):
            raise ChannelError("channel_authentication", 401)
        body = json.loads(await bounded_body(request, 4096))
        if set(body) != {"call_id"} or not isinstance(body["call_id"], str):
            raise ChannelError("channel_internal_request", 400)
        await service.ensure_owner()
        if request.match_info["operation"] == "cancel":
            await service.cancel_call(body["call_id"])
            result = service.repository.outcome(await service.repository.call(body["call_id"]))
        elif request.match_info["operation"] == "reconcile":
            result = await service.reconcile_call(body["call_id"])
        else:
            result = await service.perform_call(body["call_id"])
        return json_response(result)

    app.router.add_get("/api/v1/channels/view", view)
    app.router.add_post(
        "/api/v1/channels/{operation:connections/save|connections/probe|actions/invoke}", command
    )
    app.router.add_post("/channels/onebot/v11/{connection_id}/events", onebot_events)
    app.router.add_get("/channels/onebot/v11/{connection_id}/ws", reverse_ws)
    app.router.add_post("/channels/telegram/{connection_id}/webhook", telegram_webhook)
    app.router.add_post("/channels/bridge/{connection_id}/events", bridge_events)
    app.router.add_post("/channels/bridge/{connection_id}/receipts", bridge_receipt)
    app.router.add_post("/internal/channels/{operation:execute|cancel|reconcile}", internal)
