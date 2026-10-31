"""OneBot v11 d4456ee: 23 segment entries, 38 public APIs and hidden quick operation.

Names/parameters follow the upstream standard. Provider support is a separate
observation; declaring a standard method does not claim the remote implements it.
"""

from dataclasses import dataclass
from typing import Mapping
from ..contracts import ChannelError

SPEC_COMMIT = "d4456ee706f9ada9c2dfde56a2bcfc69752600e4"
SPEC_URL = "https://github.com/botuniverse/onebot-11/tree/" + SPEC_COMMIT


@dataclass(frozen=True)
class SegmentSpec:
    name: str
    variant: str
    receive: bool
    send: bool
    required: tuple[str, ...] = ()


SEGMENTS = (
    SegmentSpec("text", "text", True, True, ("text",)),
    SegmentSpec("face", "face", True, True, ("id",)),
    SegmentSpec("image", "image", True, True, ("file",)),
    SegmentSpec("record", "record", True, True, ("file",)),
    SegmentSpec("video", "video", True, True, ("file",)),
    SegmentSpec("at", "at", True, True, ("qq",)),
    SegmentSpec("rps", "rps", True, True),
    SegmentSpec("dice", "dice", True, True),
    SegmentSpec("shake", "shake", True, True),
    SegmentSpec("poke", "poke", True, True, ("type", "id")),
    SegmentSpec("anonymous", "anonymous", False, True),
    SegmentSpec("share", "share", True, True, ("url", "title")),
    SegmentSpec("contact", "friend", True, True, ("type", "id")),
    SegmentSpec("contact", "group", True, True, ("type", "id")),
    SegmentSpec("location", "location", True, True, ("lat", "lon")),
    SegmentSpec("music", "provider", False, True, ("type", "id")),
    SegmentSpec("music", "custom", False, True, ("type", "url", "audio", "title")),
    SegmentSpec("reply", "reply", True, True, ("id",)),
    SegmentSpec("forward", "forward", True, False, ("id",)),
    SegmentSpec("node", "reference", False, True, ("id",)),
    SegmentSpec("node", "custom", False, True, ("user_id", "nickname", "content")),
    SegmentSpec("xml", "xml", True, True, ("data",)),
    SegmentSpec("json", "json", True, True, ("data",)),
)


@dataclass(frozen=True)
class ApiSpec:
    name: str
    required: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    effect: str = "read"
    sensitive: bool = False
    target: str | None = None
    public: bool = True


APIS = (
    ApiSpec(
        "send_private_msg",
        ("user_id", "message"),
        ("user_id", "group_id"),
        "send",
        target="private",
    ),
    ApiSpec("send_group_msg", ("group_id", "message"), ("group_id",), "send", target="group"),
    ApiSpec("send_msg", ("message",), ("user_id", "group_id"), "send"),
    ApiSpec("delete_msg", ("message_id",), effect="moderate"),
    ApiSpec("get_msg", ("message_id",)),
    ApiSpec("get_forward_msg", ("id",)),
    ApiSpec("send_like", ("user_id",), ("user_id",), "moderate"),
    ApiSpec(
        "set_group_kick",
        ("group_id", "user_id"),
        ("group_id", "user_id"),
        "moderate",
        target="group",
    ),
    ApiSpec(
        "set_group_ban",
        ("group_id", "user_id"),
        ("group_id", "user_id"),
        "moderate",
        target="group",
    ),
    ApiSpec("set_group_anonymous_ban", ("group_id",), ("group_id",), "moderate", target="group"),
    ApiSpec("set_group_whole_ban", ("group_id",), ("group_id",), "moderate", target="group"),
    ApiSpec(
        "set_group_admin",
        ("group_id", "user_id"),
        ("group_id", "user_id"),
        "moderate",
        target="group",
    ),
    ApiSpec("set_group_anonymous", ("group_id",), ("group_id",), "moderate", target="group"),
    ApiSpec(
        "set_group_card",
        ("group_id", "user_id"),
        ("group_id", "user_id"),
        "moderate",
        target="group",
    ),
    ApiSpec(
        "set_group_name", ("group_id", "group_name"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec("set_group_leave", ("group_id",), ("group_id",), "moderate", target="group"),
    ApiSpec(
        "set_group_special_title",
        ("group_id", "user_id"),
        ("group_id", "user_id"),
        "moderate",
        target="group",
    ),
    ApiSpec("set_friend_add_request", ("flag",), effect="moderate"),
    ApiSpec("set_group_add_request", ("flag", "sub_type"), effect="moderate"),
    ApiSpec("get_login_info"),
    ApiSpec("get_stranger_info", ("user_id",), ("user_id",)),
    ApiSpec("get_friend_list"),
    ApiSpec("get_group_info", ("group_id",), ("group_id",)),
    ApiSpec("get_group_list"),
    ApiSpec("get_group_member_info", ("group_id", "user_id"), ("group_id", "user_id")),
    ApiSpec("get_group_member_list", ("group_id",), ("group_id",)),
    ApiSpec("get_group_honor_info", ("group_id", "type"), ("group_id",)),
    ApiSpec("get_cookies", sensitive=True),
    ApiSpec("get_csrf_token", sensitive=True),
    ApiSpec("get_credentials", sensitive=True),
    ApiSpec("get_record", ("file", "out_format")),
    ApiSpec("get_image", ("file",)),
    ApiSpec("can_send_image"),
    ApiSpec("can_send_record"),
    ApiSpec("get_status"),
    ApiSpec("get_version_info"),
    ApiSpec("set_restart", effect="control"),
    ApiSpec("clean_cache", effect="control"),
)
QUICK_OPERATION = ApiSpec(
    ".handle_quick_operation", ("context", "operation"), effect="moderate", public=False
)
# Explicit provider APIs. SnowLuma must declare actual extensions; it never inherits
# every NapCat method simply because both use the v11 envelope.
NAPCAT_APIS = (
    ApiSpec(
        "_send_group_notice", ("group_id", "content"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec("_get_group_notice", ("group_id",), ("group_id",)),
    ApiSpec(
        "_del_group_notice", ("group_id", "notice_id"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec("get_group_file_system_info", ("group_id",), ("group_id",)),
    ApiSpec("get_group_root_files", ("group_id",), ("group_id",)),
    ApiSpec("get_group_files_by_folder", ("group_id", "folder_id"), ("group_id",)),
    ApiSpec("get_group_file_url", ("group_id", "file_id", "busid"), ("group_id",)),
    ApiSpec(
        "upload_group_file", ("group_id", "file", "name"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec(
        "delete_group_file",
        ("group_id", "file_id", "busid"),
        ("group_id",),
        "moderate",
        target="group",
    ),
    ApiSpec(
        "create_group_file_folder", ("group_id", "name"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec(
        "delete_group_folder", ("group_id", "folder_id"), ("group_id",), "moderate", target="group"
    ),
    ApiSpec("get_group_at_all_remain", ("group_id",), ("group_id",)),
    ApiSpec(
        "send_group_forward_msg", ("group_id", "messages"), ("group_id",), "send", target="group"
    ),
    ApiSpec(
        "send_private_forward_msg", ("user_id", "messages"), ("user_id",), "send", target="private"
    ),
    ApiSpec(
        "upload_private_file", ("user_id", "file", "name"), ("user_id",), "send", target="private"
    ),
)
BY_NAME = {item.name: item for item in (*APIS, QUICK_OPERATION, *NAPCAT_APIS)}


def qq_id(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).isdigit()
        or int(value) <= 0
    ):
        raise ChannelError("invalid_qq_id", 400)
    return str(value)


def message_id(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).lstrip("-").isdigit()
    ):
        raise ChannelError("invalid_message_id", 400)
    return str(value)


def validate_call(name, params, connection):
    base = name[:-6] if name.endswith("_async") else name
    spec = BY_NAME.get(base)
    if spec is None:
        raise ChannelError("channel_api_not_implemented", 400)
    if (
        base in {x.name for x in NAPCAT_APIS}
        and connection["provider"] != "napcat"
        and base not in connection.get("extension_apis", [])
    ):
        raise ChannelError("provider_extension_not_declared", 409)
    if connection.get("api_support", {}).get(base) is False:
        raise ChannelError("provider_api_unsupported", 409)
    if not isinstance(params, Mapping) or any(key not in params for key in spec.required):
        raise ChannelError("channel_api_parameters", 400)
    clean = dict(params)
    for key in spec.ids:
        if clean.get(key) is not None:
            clean[key] = qq_id(clean[key])
    if "message_id" in clean:
        clean["message_id"] = message_id(clean["message_id"])
    if base == "send_msg" and not any(key in clean for key in ("user_id", "group_id")):
        raise ChannelError("channel_target_required", 400)
    if base == "set_group_anonymous_ban" and not any(
        key in clean for key in ("anonymous", "anonymous_flag", "flag")
    ):
        raise ChannelError("anonymous_identity_required", 400)
    return spec, clean


def matrix(connection=None):
    connection = connection or {}
    support = connection.get("api_support", {})
    observations = connection.get("observations", {})
    return {
        "standard": "onebot.v11",
        "spec_commit": SPEC_COMMIT,
        "spec_url": SPEC_URL,
        "segments": [
            {
                "type": x.name,
                "variant": x.variant,
                "receive": x.receive,
                "send": x.send,
                "implemented": True,
                "provider_supported": connection.get("segment_support", {}).get(x.name),
                "verified_at": observations.get("segments", {}).get(x.name),
            }
            for x in SEGMENTS
        ],
        "apis": [
            {
                "name": x.name,
                "standard": x in APIS,
                "public": x.public,
                "effect": x.effect,
                "sensitive": x.sensitive,
                "required": x.required,
                "implemented": True,
                "provider_supported": support.get(x.name),
                "verified_at": observations.get("apis", {}).get(x.name),
            }
            for x in (*APIS, QUICK_OPERATION, *NAPCAT_APIS)
        ],
        "transports": ["http", "reverse_http", "ws", "reverse_ws"],
    }
