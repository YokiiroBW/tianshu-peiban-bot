"""Pinned public contracts and explicit execution semantics for SnowLuma."""

import json
from importlib.resources import files

CATALOG = json.loads(files(__package__).joinpath("catalog.json").read_text(encoding="utf-8"))
REVISION = CATALOG["revision"]
ACTIONS = {row["name"]: row for row in CATALOG["actions"]}
ALIASES = {alias: row["name"] for row in ACTIONS.values() for alias in row["aliases"]}

# These handlers are placeholders in the pinned provider, including two that return ok.
UNAVAILABLE = frozenset(
    {
        "_get_model_show",
        "_set_model_show",
        ".get_word_slices",
        "create_collection",
        "get_recent_contact",
        "nc_get_packet_status",
        "set_group_anonymous",
        "set_group_anonymous_ban",
        "set_restart",
    }
)
SENSITIVE = frozenset(
    {
        "get_cookies",
        "get_credentials",
        "get_csrf_token",
        "get_clientkey",
        "request_decrypt_key",
        "send_packet",
    }
)
CONTROL = frozenset({"bot_exit", "clean_cache", "clean_stream_temp_file", "send_packet"})
MESSAGE_APIS = frozenset({"send_msg", "send_group_msg", "send_private_msg"})
FORWARD_APIS = frozenset({"send_forward_msg", "send_group_forward_msg", "send_private_forward_msg"})
MESSAGE_RECEIPT_APIS = (
    MESSAGE_APIS
    | FORWARD_APIS
    | frozenset(
        {
            "send_custom_face",
            "send_group_ai_record",
            "forward_friend_single_msg",
            "forward_group_single_msg",
        }
    )
)
# These actions publish to a chat and must share Expression's sole delivery ledger.
DELIVERY_APIS = (
    MESSAGE_APIS
    | FORWARD_APIS
    | frozenset(
        {
            "send_custom_face",
            "send_flash_msg",
            "send_group_ai_record",
            "send_tuwen_ark",
            "forward_friend_single_msg",
            "forward_group_single_msg",
            "upload_group_file",
            "upload_private_file",
        }
    )
)


def action(name):
    return ACTIONS.get(ALIASES.get(name, name))


def implemented(name):
    row = action(name)
    return row is not None and row["name"] not in UNAVAILABLE


def effect(row):
    name = row["name"]
    return (
        "read"
        if row["read_only"]
        else "send"
        if name in DELIVERY_APIS
        else "control"
        if name in CONTROL
        else "moderate"
    )
