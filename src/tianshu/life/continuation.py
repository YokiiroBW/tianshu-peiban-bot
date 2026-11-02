"""Resume only the continuation derived from the last confirmed execution."""
from datetime import datetime

from tianshu.conversation.codec import signature
from tianshu.runtime.errors import AppError


READ_CAPABILITIES = frozenset({"content.discover", "content.read", "content.continue", "video.observe", "life.reading"})
MAX_WINDOW_STEPS = 12


def execution_window(checkpoint, begins, ends):
    """A newly accepted arrangement owns one finite run, including discovery."""
    return {**checkpoint, "execution_window": {"starts_at": begins.isoformat(),
        "ends_at": ends.isoformat(), "steps": 0, "seen_steps": []}, "continuation_stop_reason": None}


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
            or data.get("last_execution") != "succeeded"):
        return None, "continuation_requires_arrangement"
    if activity.get("action_id") and data.get("completed_action_id") != activity["action_id"]:
        return None, "activity_action_unresolved"
    window = data.get("execution_window")
    # Old checkpoints without an accepted execution window go through the normal
    # planner or the existing explicit resume command; recovery never invents time.
    if not window:
        return None, "continuation_requires_arrangement"
    if now < datetime.fromisoformat(window["starts_at"]):
        return None, "activity_window_not_started"
    if now >= datetime.fromisoformat(window["ends_at"]):
        return None, "activity_window_ended"
    if window.get("steps", 0) >= MAX_WINDOW_STEPS:
        return None, "continuation_step_limit"
    consumption = data.get("consumption") or {}
    if consumption.get("stop_reason") not in {None, "checkpoint", "segment_limit", "window_limit"}:
        return None, "continuation_stop_condition"
    try:
        step = resume_step(activity)
    except AppError as error:
        return None, error.code
    if step["capability_id"] not in READ_CAPABILITIES:
        return None, "continuation_requires_arrangement"
    if signature(step) in window.get("seen_steps", ()):
        return None, "continuation_repeated_step"
    return step, None


def options(checkpoint):
    exploration=checkpoint.get('exploration') or {}
    values=[{'key':item.get('candidate_key') or item.get('content_key'),
        'capability_id':item['capability_id'],'arguments':item['arguments'],'min_after_minutes':0}
        for item in exploration.get('next_actions',())]
    if not values and exploration.get('discovery_continuation'):
        values.append({'key':'discovery_page',**exploration['discovery_continuation'],'min_after_minutes':0})
    follow_up=(exploration.get('current_item') or {}).get('follow_up')
    if follow_up:
        values.append({'key':'follow_up',**follow_up,'min_after_minutes':30})
    return values


def resume_step(activity, selected_key=None):
    checkpoint = activity["checkpoint_data"]
    if activity["action_id"] and checkpoint.get("completed_action_id") != activity["action_id"]:
        raise AppError("activity_action_unresolved", status=409)
    continuation = checkpoint.get("continuation")
    exploration=checkpoint.get("exploration") or {}
    if selected_key:
        if selected_key=='discovery_page' and not exploration.get('next_actions'):
            continuation=exploration.get('discovery_continuation')
        elif selected_key=='follow_up':
            continuation=(exploration.get('current_item') or {}).get('follow_up')
        else:
            options = exploration.get("next_actions", ())
            continuation = next((item for item in options if item.get("candidate_key") == selected_key or item.get("content_key") == selected_key), None)
        if continuation is None:
            raise AppError("activity_continuation_invalid", status=409)
    elif not continuation and not exploration.get('next_actions'):
        continuation=exploration.get('discovery_continuation')
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
    exploration["next_actions"]=[item for item in exploration.get("next_actions",())
        if signature({"capability_id":item["capability_id"],"arguments":item["arguments"]})!=target]
    if selected_key=='discovery_page' or (exploration.get('discovery_continuation') and signature(exploration['discovery_continuation'])==target):
        exploration['discovery_continuation']=None
    if selected_key=='follow_up':
        exploration['current_item']={**exploration.get('current_item',{}),'follow_up':None}
    data["exploration"]=exploration
    return data
