"""Bounded forward expansion keeps actual sources and each unavailable item."""

import asyncio
from collections.abc import Mapping
from dataclasses import replace

from tianshu.capabilities.contracts import canonical
from tianshu.conversation.types import InputPart
from tianshu.runtime.serialization import wire
from .contracts import ChannelError
from .onebot.messages import input_parts, segments
from .text import database_value


def _unavailable(part, reason, *, source=None):
    metadata = {**part.metadata, "channel": "onebot.v11", "lookup_unavailable": reason,
        "analysis_completed": False, "missing": [{"reason": reason}]}
    if source is not None:
        metadata["forward_source_ref"] = wire(source)
    return replace(part, metadata=metadata)


def _missing(output, reason, node_index, part_index=None):
    item = {"node_index": node_index, "reason": reason}
    if part_index is not None:
        item["part_index"] = part_index
    metadata = output[0].metadata
    output[0] = replace(output[0], metadata={**metadata, "missing": [*metadata.get("missing", ()), item]})


async def expand_forward(service, context, connection, part, call_api, event_id, *, depth=0,
    max_parts=128, path="root", event_revision=1):
    segment = part.metadata.get("segment", {})
    refs, source = [], None
    try:
        if depth > 2:
            raise ChannelError("forward_depth_budget", 413)
        if segment.get("type") == "forward":
            raw = await call_api("get_forward_msg", {"id": segment["data"]["id"]})
        elif segment.get("type") == "node":
            raw = {"messages": [segment["data"]]}
        else:
            return [part], refs
        native = canonical(raw).encode()
        if len(native) > 4 * 1024 * 1024:
            raise ChannelError("forward_byte_budget", 413)
        source = await service.sources.capture(context, locator={
            "kind": "channel_forward", "connection_id": connection["id"], "event_id": event_id,
            "forward_id": segment.get("data", {}).get("id"), "depth": depth, "path": path,
            "revision": event_revision,
            "evidence_unit": connection["namespace"]+":"+connection["id"]+":"+event_id,
        }, raw=native, mime_type="application/json")
        refs.append(source)
        if not isinstance(raw, Mapping):
            raise ChannelError("forward_response_format", 502)
        nodes = raw.get("messages", raw.get("message"))
        if not isinstance(nodes, list):
            raise ChannelError("forward_nodes_format", 502)
        if not nodes:
            raise ChannelError("forward_empty_body", 502)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return [_unavailable(part, getattr(error, "code", "forward_lookup_failed"), source=source)], refs

    output = [replace(part, metadata={**part.metadata, "channel": "onebot.v11",
        "forward_source_ref": wire(source), "node_count": len(nodes), "analysis_completed": False})]
    if len(nodes) > 128:
        output[0] = replace(output[0], metadata={**output[0].metadata, "omitted": "forward_node_budget"})
        _missing(output, "forward_node_budget", 128)
    for node_index, node in enumerate(nodes[:128]):
        if len(output) >= max_parts:
            output[0] = replace(output[0], metadata={**output[0].metadata, "omitted": "forward_part_budget"})
            _missing(output, "forward_part_budget", node_index)
            return output, refs
        try:
            if not isinstance(node, Mapping) or not isinstance(node.get("sender", {}), Mapping):
                raise ChannelError("forward_node_format", 502)
            sender = node.get("sender", {})
            author = str(sender.get("user_id", node.get("user_id", node.get("uin", ""))))
            name = database_value(sender.get("nickname", node.get("nickname", node.get("name"))))
            content = node.get("content", node.get("message"))
            if content is None:
                raise ChannelError("forward_node_body_unavailable", 502)
            children = input_parts(segments(content))
            if not children:
                raise ChannelError("forward_node_empty_body", 502)
        except Exception as error:
            reason = getattr(error, "code", "forward_node_format")
            _missing(output, reason, node_index)
            output.append(_unavailable(InputPart("reference", metadata={"forward_node_index": node_index}),
                reason, source=source))
            continue
        for child_index, child in enumerate(children):
            if len(output) >= max_parts:
                output[0] = replace(output[0], metadata={**output[0].metadata, "omitted": "forward_part_budget"})
                _missing(output, "forward_part_budget", node_index, child_index)
                return output, refs
            child = replace(child, kind="quote" if child.kind == "text" else child.kind, metadata={
                **child.metadata, "original_author_external_id": author, "original_author_display_name": name,
                "forward_node_index": node_index, "forward_source_ref": wire(source)})
            if child.metadata.get("original_pending"):
                try:
                    child, media_source = await service.media.receive(context, connection, child, call_api,
                        event_id=event_id, part_index=path+":"+str(node_index)+":"+str(child_index),
                        revision=event_revision)
                    if media_source:
                        refs.append(media_source)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    reason = getattr(error, "code", "channel_media_unavailable")
                    child = _unavailable(replace(child, metadata={**child.metadata,
                        "original_pending": False, "media_unavailable": True, "error_code": reason}), reason)
                if child.metadata.get("media_unavailable"):
                    _missing(output, child.metadata.get("error_code", "channel_media_unavailable"), node_index, child_index)
            if child.kind == "forward":
                nested, nested_refs = await expand_forward(service, context, connection, child, call_api,
                    event_id, depth=depth+1, path=path+":"+str(node_index)+":"+str(child_index),
                    event_revision=event_revision, max_parts=max_parts-len(output))
                output.extend(nested)
                refs.extend(nested_refs)
                if nested[0].metadata.get("missing"):
                    _missing(output, "forward_nested_incomplete", node_index, child_index)
            else:
                output.append(child)
    return output, refs
