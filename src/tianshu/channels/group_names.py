"""Display names for already authorized groups, using the existing channel read ledger."""

import asyncio
from dataclasses import replace
from uuid import uuid4

from tianshu.types import Action, OriginKind

from .contracts import ChannelError
from .text import database_text


async def group_scenes(service, context):
    async with service.runtime.database.transaction(read_only=True) as conn:
        scenes = await service.identity.archive_scene_on(conn, context)
    return tuple(scene for scene in scenes if scene["kind"] == "group")


def metadata_call(connection, target):
    if not target.startswith("group:"):
        raise ChannelError("channel_target_mismatch", 403)
    external_id = target.removeprefix("group:")
    if connection["adapter"] == "telegram":
        return "getChat", {"chat_id": external_id.split(":thread:", 1)[0]}
    return "get_group_info", {"group_id": external_id}


async def check_metadata_target(service, context, connection, name, params):
    """An internal metadata read cannot select an unrelated group or connection."""
    scenes = await group_scenes(service, context)
    for scene in scenes:
        if scene["connection_id"] == connection["id"]:
            expected_name, expected_params = metadata_call(connection, scene["target_ref"])
            if (name == expected_name and params.keys() == expected_params.keys()
                    and all(str(params[key]) == value for key, value in expected_params.items())):
                return
    raise ChannelError("channel_target_mismatch", 403)


async def group_names(service, context, conversation_ids, *, refresh=False):
    if not conversation_ids or len(conversation_ids) > 100 or any(
        not isinstance(value, str) or not 1 <= len(value) <= 256 for value in conversation_ids
    ):
        raise ChannelError("invalid_group_name_targets", 400)
    semaphore = asyncio.Semaphore(4)

    async def resolve(conversation_id):
        async with semaphore:
            current = await service.identity.authorize_conversation(context, conversation_id)
            scenes = await group_scenes(service, current)
            if not scenes:
                return conversation_id, None
            scene = scenes[0]
            connection = await service.repository.connection(scene["connection_id"], current)
            if not connection.get("enabled", True):
                return conversation_id, None
            target = scene["target_ref"]
            cached = await service.repository.observation(connection["id"], "group_name", target)
            body = cached["body"] if cached else {}
            if body.get("connection_revision") != connection["revision"]:
                body = {}
            previous = body.get("name")
            ttl = 15 if body.get("error_code") else 300
            if body and not refresh and 0 <= cached["age_seconds"] < ttl:
                return conversation_id, previous
            name, params = metadata_call(connection, target)
            key = "group-name:" + str(uuid4())
            read_context = replace(current, origin_kind=OriginKind.OBSERVATION)
            action = Action(key, key, "channels.read", current.actor_id, read_context,
                            {"connection_id": connection["id"], "api_name": name, "params": params},
                            OriginKind.OBSERVATION)
            error_code = None
            display_name = previous
            try:
                receipt = await service.actions.invoke(action, background=False)
                data = receipt.result.get("data")
                if receipt.execution != "succeeded" or not isinstance(data, dict):
                    raise ChannelError(receipt.error_code or "group_name_unavailable", 503)
                id_key, name_key = ("id", "title") if name == "getChat" else ("group_id", "group_name")
                if str(data.get(id_key)) != str(next(iter(params.values()))):
                    raise ChannelError("channel_target_mismatch", 403)
                supplied = data.get(name_key)
                if not isinstance(supplied, str) or not supplied.strip():
                    raise ChannelError("group_name_unavailable", 503)
                display_name = database_text(supplied).strip()[:256]
            except (ChannelError, TimeoutError) as error:
                error_code = getattr(error, "code", "group_name_timeout")
            await service.repository.observe(connection["id"], "group_name", target,
                {"name": display_name, "connection_revision": connection["revision"], "error_code": error_code})
            return conversation_id, display_name

    results = await asyncio.gather(*(resolve(value) for value in dict.fromkeys(conversation_ids)))
    return {"names": {key: name for key, name in results if name}}
