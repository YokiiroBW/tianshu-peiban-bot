"""Public in-process channel API, shared by application modules and plugin ports.

All execution enters the existing Actions and Channels ledgers. Callers supply a
stable request_id for a logical operation, including each individual upload chunk.
"""

from uuid import NAMESPACE_URL, uuid5

from tianshu.capabilities.contracts import canonical
from tianshu.types import Action

from .contracts import ChannelError
from .onebot.registry import matrix, validate_call


class ChannelNativeApi:
    def __init__(self, channels):
        self.channels = channels

    async def describe(self, context, connection_id, *, api_name=None, category=None):
        connection = await self.channels.repository.connection(connection_id, context)
        if connection["adapter"] == "telegram":
            from .telegram import BOT_API_METHODS

            catalog = {
                "standard": "telegram.bot-api",
                "apis": [{"name": name, "implemented": True} for name in sorted(BOT_API_METHODS)],
            }
        else:
            catalog = matrix(connection)
        if api_name is not None:
            from .onebot.registry import resolve_api

            spec = resolve_api(api_name, connection)
            canonical_name = spec.name if spec else api_name
            catalog["apis"] = [row for row in catalog["apis"] if row["name"] == canonical_name]
        if category is not None:
            catalog["apis"] = [row for row in catalog["apis"] if row.get("category") == category]
        return {
            **catalog,
            "connection_id": connection_id,
            "provider": connection["provider"],
            "connection_revision": connection["revision"],
            "configured": connection.get("configured", False),
        }

    async def invoke(
        self,
        context,
        connection_id,
        api_name,
        params,
        *,
        request_id,
        source_refs=(),
        origin_kind=None,
        behavior_refs=(),
        behavior_purpose=None,
    ):
        if not isinstance(request_id, str) or not request_id:
            raise ChannelError("channel_request_id_required", 400)
        connection = await self.channels.repository.connection(connection_id, context)
        if connection["adapter"] != "telegram":
            validate_call(api_name, params, connection)
        identifier = str(
            uuid5(
                NAMESPACE_URL,
                canonical(
                    [
                        "channels.native",
                        context.principal_id,
                        context.actor_id,
                        connection_id,
                        request_id,
                    ]
                ),
            )
        )
        arguments = await self.channels.configuration.seal_command(
            context,
            identifier,
            {
                "connection_id": connection_id,
                "api_name": api_name,
                "params": params,
            },
        )
        action = Action(
            identifier,
            request_id,
            "channels.api",
            context.actor_id,
            context,
            arguments,
            origin_kind or context.origin_kind,
            source_refs=tuple(source_refs),
        )
        return await self.channels.actions.invoke(
            action,
            background=False,
            behavior_refs=tuple(behavior_refs),
            behavior_purpose=behavior_purpose,
        )

    async def get(self, context, action_id):
        document = await self.channels.actions.get(action_id, context)
        if document["action"]["capability_id"] != "channels.api":
            raise ChannelError("channel_action_required", 404)
        return document
