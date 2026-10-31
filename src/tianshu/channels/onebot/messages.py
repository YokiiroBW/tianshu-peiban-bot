"""Canonical ordered message parts over the existing NoneBot OneBot SDK types."""

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from tianshu.identity.types import IncomingEvent
from tianshu.conversation.types import InputPart
from ..contracts import ChannelError
from .registry import SEGMENTS, qq_id, message_id

MAX_SEGMENTS = 256
MAX_NATIVE_BYTES = 1024 * 1024
KNOWN = {x.name for x in SEGMENTS}


def segments(value):
    # CQ decoding belongs only at this actual channel boundary. Business text is
    # always constructed with MessageSegment.text and cannot become CQ commands.
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    if isinstance(value, str):
        message = Message(value)
    elif isinstance(value, list):
        message = Message([MessageSegment(x["type"], dict(x.get("data", {}))) for x in value])
    else:
        raise ChannelError("onebot_message_format", 400)
    rows = [{"type": segment.type, "data": dict(segment.data)} for segment in message]
    if (
        len(rows) > MAX_SEGMENTS
        or len(json.dumps(rows, ensure_ascii=False).encode()) > MAX_NATIVE_BYTES
    ):
        raise ChannelError("onebot_message_budget", 413)
    return rows


def input_parts(rows):
    result = []
    for segment in rows:
        kind = segment["type"]
        data = segment["data"]
        if kind == "text":
            result.append(InputPart("text", str(data.get("text", ""))))
        elif kind == "at":
            result.append(
                InputPart(
                    "mention_all" if str(data.get("qq")) == "all" else "mention",
                    metadata={"user_id": str(data.get("qq")), "channel": "onebot.v11"},
                )
            )
        elif kind in {"image", "record", "video", "file"}:
            result.append(
                InputPart(
                    {"record": "audio"}.get(kind, kind),
                    metadata={
                        "channel": "onebot.v11",
                        "segment": segment,
                        "original_pending": True,
                    },
                )
            )
        elif kind == "reply":
            result.append(
                InputPart(
                    "reply",
                    metadata={
                        "channel": "onebot.v11",
                        "event_id": message_id(data["id"]),
                        "segment": segment,
                    },
                )
            )
        elif kind in {"forward", "node"}:
            result.append(
                InputPart("forward", metadata={"channel": "onebot.v11", "segment": segment})
            )
        elif kind == "face":
            result.append(
                InputPart("reaction", metadata={"channel": "onebot.v11", "segment": segment})
            )
        else:
            result.append(
                InputPart(
                    "reference",
                    text=json.dumps(segment, ensure_ascii=False),
                    metadata={
                        "channel": "onebot.v11",
                        "segment_type": kind,
                        "known": kind in KNOWN,
                        "analysis_completed": False,
                    },
                )
            )
    return tuple(result)


def normalize_event(connection, raw, *, reply_owner="tianshu", archived=False):
    if not isinstance(raw, Mapping):
        raise ChannelError("onebot_event_format", 400)
    if qq_id(raw.get("self_id")) != connection["self_external_id"]:
        raise ChannelError("channel_account_mismatch", 403)
    occurred = datetime.fromtimestamp(int(raw["time"]), UTC)
    post = raw.get("post_type")
    if post not in {"message", "message_sent", "notice", "request", "meta_event"}:
        raise ChannelError("onebot_post_type", 400)
    producer = connection["namespace"] + ":" + connection["id"]
    if post in {"message", "message_sent"}:
        sender = qq_id(raw["user_id"])
        nested = raw.get("sender", {}).get("user_id")
        if nested is not None and qq_id(nested) != sender:
            raise ChannelError("onebot_conflicting_sender", 400)
        kind = raw.get("message_type")
        if kind not in {"private", "group"}:
            raise ChannelError("onebot_message_type", 400)
        target = "group:" + qq_id(raw["group_id"]) if kind == "group" else "private:" + sender
        rows = segments(raw.get("original_message", raw.get("message", [])))
        parts = input_parts(rows)
        mentioned = tuple(
            qq_id(row["data"]["qq"])
            for row in rows
            if row["type"] == "at" and str(row["data"].get("qq")) != "all"
        )
        if any(row["type"] == "at" and str(row["data"].get("qq")) == "all" for row in rows):
            mentioned = tuple(dict.fromkeys((*mentioned, connection["self_external_id"])))
        reply = next(
            (message_id(row["data"]["id"]) for row in rows if row["type"] == "reply"), None
        )
        event_id = "message:" + kind + ":" + message_id(raw["message_id"])
        # Anonymous display metadata is preserved, but cannot identify a person.
        if raw.get("anonymous"):
            sender = "anonymous:" + str(raw["anonymous"].get("id", raw["message_id"]))
        text = "".join(part.text or "" for part in parts)
        command = text.split()[0] if text.startswith("/") and text.strip() else None
        event = IncomingEvent(
            producer,
            event_id,
            int(raw.get("revision", 1)),
            occurred,
            target,
            kind,
            sender,
            tuple({"kind": p.kind, "text": p.text, "metadata": dict(p.metadata)} for p in parts),
            mentioned,
            reply_to_event_id="message:" + kind + ":" + reply if reply else None,
            original_author_external_id=str(raw.get("sender", {}).get("user_id", raw["user_id"])),
            command=command,
            reply_owner=reply_owner,
            archived=archived or post == "message_sent" or sender == connection["self_external_id"],
        )
        return event, parts
    # Protocol notices and lifecycle/heartbeat events remain operational records.
    # Their payload never becomes a fabricated first-person human statement.
    subtype = str(
        raw.get("notice_type", raw.get("request_type", raw.get("meta_event_type", "unknown")))
    )
    stable = hashlib.sha256(
        json.dumps(dict(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target = (
        "group:" + qq_id(raw["group_id"])
        if raw.get("group_id")
        else "private:" + qq_id(raw.get("user_id", raw["self_id"]))
    )
    sender = qq_id(raw.get("operator_id", raw.get("user_id", raw["self_id"])))
    event = IncomingEvent(
        producer,
        post + ":" + subtype + ":" + stable,
        1,
        occurred,
        target,
        "group" if raw.get("group_id") else "private",
        sender,
        (
            {
                "kind": "reference",
                "metadata": {"channel": "onebot.v11", "event": dict(raw), "operational": True},
            },
        ),
        reply_owner="none",
        archived=True,
    )
    return event, ()


def validate_outbound(rows, connection):
    if not isinstance(rows, list) or len(rows) > MAX_SEGMENTS:
        raise ChannelError("onebot_message_budget", 413)
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or row.get("type") not in KNOWN
            or not isinstance(row.get("data"), Mapping)
        ):
            raise ChannelError("onebot_segment_unsupported", 400)
        kind = row["type"]
        data = row["data"]
        candidates = [s for s in SEGMENTS if s.name == kind and s.send]
        if not candidates:
            raise ChannelError("onebot_receive_only_segment", 400)
        if not any(all(key in data for key in spec.required) for spec in candidates):
            raise ChannelError("onebot_segment_parameters", 400)
        if connection.get("segment_support", {}).get(kind) is False:
            raise ChannelError("provider_segment_unsupported", 409)
        if kind == "at":
            if str(data.get("qq")) == "all":
                if not connection.get("allow_at_all"):
                    raise ChannelError("channel_at_all_not_configured", 409)
            else:
                qq_id(data["qq"])
        if kind == "reply":
            message_id(data["id"])
        if kind == "contact" and data.get("type") not in {"friend", "group"}:
            raise ChannelError("onebot_contact_type", 400)
        if kind == "node" and "content" in data:
            if isinstance(data["content"], list):
                validate_outbound(data["content"], connection)
            elif not isinstance(data["content"], str):
                raise ChannelError("onebot_node_content", 400)
    return rows


def mentions_all(value):
    if isinstance(value, Mapping):
        if value.get("type") == "at" and str(value.get("data", {}).get("qq")) == "all":
            return True
        return any(mentions_all(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(mentions_all(item) for item in value)
    return False
