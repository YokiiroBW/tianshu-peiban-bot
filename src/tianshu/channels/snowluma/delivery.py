"""Adapt provider-native chat publishing to the existing ordered expression."""

from copy import deepcopy

from ..contracts import ChannelError
from ..onebot.messages import segments

GROUP_APIS = frozenset(
    {
        "send_group_msg",
        "send_group_forward_msg",
        "send_group_ai_record",
        "upload_group_file",
        "forward_group_single_msg",
    }
)
PRIVATE_APIS = frozenset(
    {
        "send_private_msg",
        "send_private_forward_msg",
        "upload_private_file",
        "forward_friend_single_msg",
    }
)


def parameters(spec, params, target):
    destination, identifier = target["target_ref"].split(":", 1)
    if (
        destination == "group"
        and spec.name in PRIVATE_APIS
        or destination == "private"
        and spec.name in GROUP_APIS
    ):
        raise ChannelError("channel_target_mismatch", 403)
    key = "group_id" if destination == "group" else "user_id"
    if str(params.get(key)) != identifier:
        raise ChannelError("channel_target_mismatch", 403)
    if destination == "private" and spec.name not in PRIVATE_APIS and params.get("group_id"):
        raise ChannelError("channel_target_mismatch", 403)
    if (
        spec.name in {"send_msg", "send_forward_msg"}
        and params.get("message_type")
        and params["message_type"] != destination
    ):
        raise ChannelError("channel_target_mismatch", 403)
    clean = deepcopy(params)

    def content(value, depth=0):
        if depth > 4:
            raise ChannelError("outbound_forward_depth_budget", 413)
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list):
            value = [row if "type" in row else {"type": "node", "data": row} for row in value]
            value = [
                {**row, "data": {"data": row["data"]}}
                if row.get("type") == "json" and isinstance(row.get("data"), str)
                else row
                for row in value
            ]
        result = segments(value)
        for row in result:
            data = row["data"]
            if row["type"] == "node" and isinstance(data.get("content"), (str, list)):
                data["content"] = content(data["content"], depth + 1)
        return result

    for field in ("message", "messages"):
        if field in clean:
            if field == "message" and clean.get("auto_escape") and isinstance(clean[field], str):
                clean[field] = [{"type": "text", "data": {"text": clean[field]}}]
            else:
                clean[field] = content(clean[field])
    return clean
