"""Provider wire validation, including the few cross-field upstream constraints."""

import re
from copy import deepcopy
from functools import lru_cache

from jsonschema import Draft202012Validator

from ..contracts import ChannelError


@lru_cache(maxsize=256)
def validator(name):
    from .catalog import ACTIONS

    return Draft202012Validator(ACTIONS[name]["input_schema"])


def validate_parameters(spec, params):
    clean = deepcopy(params)
    for key, field in spec.input_schema.get("properties", {}).items():
        if (
            (
                field.get("x-role") in {"group_id", "user_id"}
                or key in {"group_id", "user_id", "uin", "target_uin", "message_id"}
                and field.get("type") == "integer"
            )
            and isinstance(clean.get(key), str)
            and clean[key].removeprefix("-").isdigit()
        ):
            clean[key] = int(clean[key])
    if next(validator(spec.name).iter_errors(clean), None) is not None:
        raise ChannelError("channel_api_parameters", 400)
    if not cross_fields(spec.name, clean):
        raise ChannelError("channel_api_parameters", 400)
    return clean


def cross_fields(name, value):
    if name == "get_qzone_feed_detail":
        return re.fullmatch(r"[a-fA-F0-9]{24}", value["tid"]) is not None
    if name == "_del_group_notice":
        return bool(value.get("fid") or value.get("notice_id"))
    if name in {
        "send_msg",
        "send_forward_msg",
        "send_flash_msg",
        "send_tuwen_ark",
        "send_ark_share",
        "share_peer",
    }:
        return bool(value.get("group_id") or value.get("user_id"))
    if name.startswith("download_file_") and name.endswith("stream"):
        return bool(value.get("file") or value.get("file_id"))
    if name in {"send_qzone_msg", "set_qzone_msg_right"}:
        right = value.get("ugc_right", 1)
        return right in {1, 4, 16, 64, 128} and (
            right not in {16, 128} or bool(value.get("target_uins"))
        )
    if name == "set_group_admin_permissions":
        kick = value.get("kick_member")
        return (
            any(key in value for key in ("recall_member_message", "kick_member"))
            and (
                kick is not True or value.get("kick_member_per_day") in {0, 5, 10, 20, 50, 100, 200}
            )
            and ("kick_member_per_day" not in value or kick is True)
        )
    if name == "set_group_member_permissions":
        return any(
            key in value
            for key in (
                "allow_member_upload_album",
                "allow_member_temporary_session",
                "allow_member_create_group",
            )
        )
    if name == "set_qzone_profile":
        return any(key in value for key in ("name", "description"))
    if name == "set_friends_category":
        return ("categoryId" in value) != ("categoryName" in value)
    if name == "_send_group_notice":
        kind = value.get("type")
        return (kind is None or kind in {1, 20}) and (
            "send_to_new_members" not in value
            or kind is None
            or value["send_to_new_members"] == (kind == 20)
        )
    if name == "comment_qzone":
        pair = ("comment_id" in value) == ("comment_uin" in value)
        reply = ("reply_uin" in value) == ("reply_name" in value)
        return (
            pair
            and reply
            and ("reply_uin" not in value or "comment_id" in value)
            and not (value.get("images") and "comment_id" in value)
            and all(
                not isinstance(value.get(key), str) or bool(value[key].strip())
                for key in ("comment_id", "reply_name", "content")
            )
        )
    return True
