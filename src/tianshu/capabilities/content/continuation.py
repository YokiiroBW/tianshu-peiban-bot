"""Receipt-derived progress for the existing Life activity, never a second queue."""

from copy import deepcopy


def continuation_step(capability_id, original_args, result):
    """A completed discovery may select actual reading, with a new canonical Action."""
    value = (result or {}).get("reading", result or {}) if capability_id == "life.reading" else (result or {})
    following = value.get("continuation")
    if following and following.get("capability_id") in {
            "content.discover", "content.read", "content.continue", "video.observe", "life.reading"}:
        return deepcopy(following)
    arguments = continuation_arguments(capability_id, original_args, value)
    return {"capability_id": capability_id, "arguments": arguments} if arguments else None


def continuation_arguments(capability_id, original_args, result):
    """Return only a provider-proven continuation; absence means no automatic replay."""
    result = result or {}
    if capability_id == "life.reading":
        result = result.get("reading", result)
    following = result.get("continuation")
    if following and following.get("capability_id") == capability_id:
        return deepcopy(following["arguments"])
    if capability_id == "video.observe":
        consumption = result.get("consumption") or {}
        position = consumption.get("next_position")
        if not position or not consumption.get("analysis_completed"):
            return None
        media, source = consumption.get("media"), consumption.get("source")
        if not media or not source:
            return None
        args = {key: deepcopy(value) for key, value in original_args.items()
                if key not in {"url", "cid", "account_id", "quality", "start", "end", "reading_state"}}
        args.update(media=deepcopy(media), source=deepcopy(source),
                    start=position["start"], end=position["end"],
                    reading_state=deepcopy(result.get("reading_state") or {}))
        return args
    if capability_id in {"life.reading", "content.continue"}:
        reading = result.get("reading", result)
        if capability_id == "life.reading" and reading.get("state", "reading") != "reading":
            return None
        position = reading.get("next_position")
        if position is None:
            return None
        requested = deepcopy(original_args.get("requested") or {})
        if isinstance(position, dict):
            requested.update(position)
        else:
            requested["position"] = position
        args = deepcopy(original_args)
        args["requested"] = requested
        args.pop("client_request_id", None)
        if capability_id == "life.reading":
            args.update(operation="read", id=reading["id"])
            if reading.get("revision") is not None:
                args["expected_revision"] = reading["revision"]
        return args
    return None


def exploration_checkpoint(capability_id, arguments, result, previous=None):
    """Keep discovery, actual consumption and unresolved questions distinguishable."""
    old = deepcopy(previous or {})
    result = result or {}
    if capability_id == "life.reading":
        result = result.get("reading", result)
    progress = result.get("exploration") or {}
    candidates = result.get("candidates") or []
    seen = list(dict.fromkeys([*old.get("seen_keys", []), *arguments.get("exclude_keys", []),
                              *(item["content_key"] for item in candidates)]))
    consumed = list(old.get("consumed_sources", []))
    if result.get("read_basis") == "actual_source_body":
        consumed.extend(result.get("source_refs", []))
    consumption = result.get("consumption") or {}
    if consumption.get("analysis_completed") and consumption.get("source"):
        consumed.append(consumption["source"])
    from ..contracts import canonical

    consumed = list({canonical(item): item for item in consumed}.values())
    current = progress.get("current_item") or old.get("current_item")
    pending = result.get("next_actions", old.get("next_actions", []))
    consumed_key = result.get("content_key", arguments.get("content_key"))
    if consumed_key and result.get("read_basis") != "discovery_metadata":
        pending = [item for item in pending if item.get("content_key") != consumed_key]
    if consumption:
        current = {"source": consumption.get("source"), "media": consumption.get("media"),
                   "requested": consumption.get("requested"),
                   "processed_segments": consumption.get("processed_segments", []),
                   "next_position": consumption.get("next_position")}
    elif result.get("locator"):
        current = {"url": result["locator"]["url"], "source": result.get("source"),
                   "continuation": result.get("continuation"), "follow_up": result.get("follow_up")}
    elif result.get("follow_up"):
        # A forum check with no new replies has no new source/locator, but its
        # receipt still proves the exact cursor for a later planned check.
        current = {**(current or {}), "url": (current or {}).get("url", arguments.get("url")),
                   "continuation": result.get("continuation"), "follow_up": deepcopy(result["follow_up"])}
    next_step = continuation_step(capability_id, arguments, result)
    return {**old, "mode": result.get("activity_mode", arguments.get("mode", old.get("mode", "research"))),
            "intention": progress.get("intention", arguments.get("intention", old.get("intention"))),
            "query": progress.get("query", arguments.get("query", old.get("query"))),
            "source_cursors": ({item["source"]: item["next_cursor"]
                                for item in result.get("sources", []) if item.get("next_cursor")}
                               if "sources" in result else old.get("source_cursors", {})),
            "seen_keys": seen, "consumed_sources": consumed, "current_item": current,
            "unresolved_questions": consumption.get("unresolved_questions",
                progress.get("unresolved_questions", old.get("unresolved_questions", []))),
            "budget": progress.get("budget", arguments.get("budget", old.get("budget", {}))),
            "next_actions": deepcopy(pending),
            "discovery_continuation": deepcopy(result.get("discovery_continuation", old.get("discovery_continuation"))),
            "stop_reason": consumption.get("stop_reason") or progress.get("stop_reason")
                           or ("checkpoint" if next_step else "source_exhausted"),
            "continuation": next_step}
