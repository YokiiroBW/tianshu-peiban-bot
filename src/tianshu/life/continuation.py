"""Resume only the continuation derived from the last confirmed execution."""
from datetime import datetime

from tianshu.conversation.codec import signature
from tianshu.runtime.errors import AppError


READ_CAPABILITIES = frozenset({"content.discover", "content.read", "content.continue", "video.observe", "life.reading"})


def read_failure_status(capability_id, execution, effect, delivery, error_code):
    return (capability_id in READ_CAPABILITIES and effect == "none" and delivery == "not_requested"
        and (execution == "failed" or (execution == "cancelled" and error_code == "capability_timeout")))


def known_read_failure(capability_id, result):
    return read_failure_status(capability_id, result.execution, result.effect, result.delivery, result.error_code)


def recoverable_action(capability_id, result):
    """Use the handler's explicit recovery phase, never infer it from error text."""
    if (not result.action_id or not result.attempt_id or not result.receipt_id or not known_read_failure(capability_id, result)
            or result.result.get("outcome") not in {"failed", "wait"}):
        return None
    recovery = result.result.get("recovery")
    if (capability_id == "video.observe" and result.execution == "failed" and result.result.get("phase") == "model"
            and result.result.get("outcome") == "wait" and recovery == "resume_model"):
        return recovery
    if capability_id != "content.read":
        return None
    if recovery == "resume_commit" and result.result.get("phase") == "commit" and result.execution == "failed":
        return recovery
    material = result.result.get("material_read") or {}
    if (recovery == "resume_material_read" and result.result.get("phase") == "acquisition"
            and material.get("phase") == "acquisition" and material.get("method") in {"http", "browser"}
            and isinstance(material.get("url"), str) and material["url"]):
        return recovery
    return None


def resolved_read_failure(activity):
    data = activity["checkpoint_data"]
    return bool(activity.get("action_id") and data.get("resolved_action_id") == activity["action_id"]
        and read_failure_status(data.get("capability_id"), data.get("resolved_execution"),
            data.get("resolved_effect"), data.get("resolved_delivery"), data.get("resolved_error_code")))


def action_resolved(activity):
    return (not activity.get("action_id") or activity["checkpoint_data"].get("completed_action_id") == activity["action_id"]
        or resolved_read_failure(activity))


def failed_target(step, exploration):
    arguments = step.get("arguments") or {}
    key = step.get("content_key") or arguments.get("content_key")
    url = arguments.get("url")
    return any((key and key == item.get("content_key")) or (url and url == item.get("url"))
        or signature({"capability_id": step.get("capability_id"), "arguments": arguments}) == item.get("step_key")
        for item in exploration.get("failed_reads", ()))


def completed_target(step, exploration):
    arguments = step.get("arguments") or {}
    key = step.get("content_key") or arguments.get("content_key")
    return bool(key and key in exploration.get("completed_content_keys", ())
        and step.get("capability_id") == "content.read" and arguments.get("mode") != "continue")


def remaining_actions(exploration):
    seen = set(exploration.get("completed_content_keys", ()))
    remaining = []
    for item in exploration.get("next_actions", ()):
        key = item.get("content_key") or (item.get("arguments") or {}).get("content_key")
        if failed_target(item, exploration) or (key and key in seen):
            continue
        if key:
            seen.add(key)
        remaining.append(item)
    return remaining


def discovery_progress(previous, current, result):
    """Repeated pages with no new candidates are not progress, even if cursors change."""
    keys = sorted({item.get("content_key") for item in result.get("candidates", ()) if item.get("content_key")})
    if set(keys) - set(previous.get("seen_keys", ())):
        current.pop("no_progress_signature", None)
        current.pop("no_progress_stop", None)
    else:
        value = signature({"query": current.get("query"), "mode": current.get("mode"), "content_keys": keys})
        current["no_progress_stop"] = previous.get("no_progress_signature") == value
        current["no_progress_signature"] = value
    return current


def unread_coverage(result, checkpoint):
    from tianshu.types import Coverage
    key = checkpoint.get("arguments", {}).get("content_key") or signature({
        "capability_id": checkpoint.get("capability_id"), "arguments": checkpoint.get("arguments", {})})
    missing = tuple((result.coverage.missing if result.coverage else ()))
    return Coverage(False, missing=tuple(dict.fromkeys((*missing, "candidate_body_unread:"+key))))


def failed_read_checkpoint(activity, result, result_revision, capability_id):
    """A terminal no-effect failure resolves control flow, never a successful read."""
    from tianshu.runtime.serialization import wire
    data = dict(activity["checkpoint_data"])
    exploration = dict(data.get("exploration") or {})
    arguments = data.get("arguments", {})
    step = {"capability_id": capability_id, "arguments": arguments}
    key = signature(step)
    coverage = unread_coverage(result, data)
    failed = {"action_id": result.action_id, "receipt_id": result.receipt_id, "result_revision": result_revision,
        "capability_id": capability_id, "content_key": arguments.get("content_key"), "url": arguments.get("url"),
        "step_key": key, "execution": result.execution, "effect": result.effect,
        "error_code": result.error_code, "missing": list(coverage.missing)}
    failures = list(exploration.get("failed_reads", ()))
    if not any(item.get("action_id") == result.action_id for item in failures):
        failures.append(failed)
    exploration["failed_reads"] = failures
    exploration["missing"] = list(dict.fromkeys([*exploration.get("missing", ()), *coverage.missing]))
    window = dict(data.get("execution_window") or {})
    if window and key not in window.get("seen_steps", ()):
        window.update(steps=window.get("steps", 0)+1, seen_steps=[*window.get("seen_steps", ()), key])
        data["execution_window"] = window
    exploration["next_actions"] = [item for item in remaining_actions(exploration)
        if signature({"capability_id": item["capability_id"], "arguments": item["arguments"]}) not in window.get("seen_steps", ())]
    pending = exploration["next_actions"]
    continuation = {name: pending[0][name] for name in ("capability_id", "arguments")} if pending else None
    discovery = exploration.get("discovery_continuation")
    if continuation is None and discovery and not failed_target(discovery, exploration):
        continuation = {name: discovery[name] for name in ("capability_id", "arguments")}
    exploration["continuation"] = continuation
    data.update(resolved_action_id=result.action_id, resolved_execution=result.execution,
        resolved_effect=result.effect, resolved_delivery=result.delivery, resolved_error_code=result.error_code,
        actual_receipt_id=result.receipt_id, last_execution=result.execution, error_code=result.error_code,
        continuation=continuation, next_position=None, consumption=None, exploration=exploration,
        coverage=wire(coverage), completion_basis="confirmed_read_failure_skipped",
        result_summary="读取未完成，已跳过该未读候选。", continuation_stop_reason=None)
    data.pop("continuation_result", None)
    data.pop("capability_recovery", None)
    return data


def execution_window(checkpoint, begins, ends):
    """Retain progress signatures when an activity is arranged again."""
    previous = checkpoint.get("execution_window") or {}
    return {**checkpoint, "execution_window": {"starts_at": begins.isoformat(),
        "ends_at": ends.isoformat(), "steps": previous.get("steps", 0),
        "seen_steps": list(previous.get("seen_steps", ()))}, "continuation_stop_reason": None}


def submitted_checkpoint(checkpoint):
    data = dict(checkpoint)
    window = dict(data["execution_window"])
    key = signature({"capability_id": data.get("capability_id"), "arguments": data.get("arguments", {})})
    window.update(steps=window.get("steps", 0) + 1, seen_steps=[*window.get("seen_steps", ()), key])
    data["execution_window"] = window
    return data


def automatic_step(activity, now):
    """A known receipt may continue inside its arrangement, never choose a new one."""
    data = activity["checkpoint_data"]
    if (activity["state"] != "dormant" or activity["waiting_reason"] != "continuation_available"
            or activity.get("resume_condition") != "continue_from_checkpoint"
            or (data.get("last_execution") != "succeeded" and not resolved_read_failure(activity))):
        return None, "continuation_requires_arrangement"
    if not action_resolved(activity):
        return None, "activity_action_unresolved"
    if resolved_read_failure(activity) and not data.get("continuation"):
        return None, "continuation_requires_arrangement"
    window = data.get("execution_window")
    # Old checkpoints without an accepted execution window go through the normal
    # planner or the existing explicit resume command; recovery never invents time.
    if not window:
        return None, "continuation_requires_arrangement"
    if now < datetime.fromisoformat(window["starts_at"]):
        return None, "activity_window_not_started"
    from .eligibility import background_work
    deadline = data.get("business_deadline_at")
    if deadline and now >= (datetime.fromisoformat(deadline) if isinstance(deadline, str) else deadline):
        return None, "activity_deadline_expired"
    if not background_work(data) and now >= datetime.fromisoformat(window["ends_at"]):
        return None, "activity_window_ended"
    consumption = data.get("consumption") or {}
    if consumption.get("stop_reason") not in {None, "checkpoint", "segment_limit", "window_limit"}:
        return None, "continuation_stop_condition"
    try:
        step = resume_step(activity)
    except AppError as error:
        return None, error.code
    if step["capability_id"] not in READ_CAPABILITIES:
        return None, "continuation_requires_arrangement"
    if step["capability_id"] == "content.discover" and (data.get("exploration") or {}).get("no_progress_stop"):
        return None, "continuation_no_progress"
    if signature(step) in window.get("seen_steps", ()):
        return None, "continuation_repeated_step"
    return step, None


def options(checkpoint):
    exploration=checkpoint.get('exploration') or {}
    values=[{'key':item.get('candidate_key') or item.get('content_key'),
        'capability_id':item['capability_id'],'arguments':item['arguments'],'min_after_minutes':0}
        for item in remaining_actions(exploration)]
    if not values and exploration.get('discovery_continuation') and not failed_target(exploration['discovery_continuation'], exploration):
        values.append({'key':'discovery_page',**exploration['discovery_continuation'],'min_after_minutes':0})
    follow_up=(exploration.get('current_item') or {}).get('follow_up')
    if follow_up and not failed_target(follow_up, exploration):
        values.append({'key':'follow_up',**follow_up,'min_after_minutes':30})
    return values


def resume_step(activity, selected_key=None):
    checkpoint = activity["checkpoint_data"]
    if not action_resolved(activity):
        raise AppError("activity_action_unresolved", status=409)
    continuation = checkpoint.get("continuation")
    exploration=checkpoint.get("exploration") or {}
    if selected_key:
        if selected_key=='discovery_page' and not remaining_actions(exploration):
            continuation=exploration.get('discovery_continuation')
        elif selected_key=='follow_up':
            continuation=(exploration.get('current_item') or {}).get('follow_up')
        else:
            options = remaining_actions(exploration)
            continuation = next((item for item in options if item.get("candidate_key") == selected_key or item.get("content_key") == selected_key), None)
        if continuation is None or failed_target(continuation, exploration):
            raise AppError("activity_continuation_invalid", status=409)
    elif continuation and (failed_target(continuation, exploration) or completed_target(continuation, exploration)):
        pending = remaining_actions(exploration)
        continuation = pending[0] if pending else None
    if not selected_key and not continuation and not remaining_actions(exploration):
        continuation=exploration.get('discovery_continuation')
        if continuation and failed_target(continuation, exploration):
            continuation = None
    if continuation:
        if not isinstance(continuation.get("capability_id"), str) or not isinstance(continuation.get("arguments"), dict):
            raise AppError("activity_continuation_invalid", status=409)
        return {"capability_id": continuation["capability_id"], "arguments": dict(continuation["arguments"])}
    from tianshu.capabilities.content.continuation import continuation_step
    step = continuation_step(checkpoint.get("capability_id"), checkpoint.get("arguments", {}),
        {"next_position": checkpoint.get("next_position"), "consumption": checkpoint.get("consumption"),
         **checkpoint.get("continuation_result", {})})
    if step is None:
        raise AppError("activity_continuation_invalid", status=409)
    return step


def resumed_checkpoint(activity, selected_key=None):
    data=dict(activity["checkpoint_data"])
    step=resume_step(activity,selected_key)
    data.update(step)
    data.pop("continuation",None)
    exploration=dict(data.get("exploration") or {})
    target=signature(step)
    exploration["next_actions"]=[item for item in remaining_actions(exploration)
        if signature({"capability_id":item["capability_id"],"arguments":item["arguments"]})!=target]
    if selected_key=='discovery_page' or (exploration.get('discovery_continuation') and signature(exploration['discovery_continuation'])==target):
        exploration['discovery_continuation']=None
    if selected_key=='follow_up':
        exploration['current_item']={**exploration.get('current_item',{}),'follow_up':None}
    data["exploration"]=exploration
    return data
