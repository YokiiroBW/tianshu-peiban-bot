"""Contact timing changes interruption frequency, never the relationship itself."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from .types import AttentionDecision


def quiet_until(now: datetime, timezone: str, start: str, end: str):
    local = now.astimezone(ZoneInfo(timezone))
    begin_h, begin_m = map(int, start.split(":"))
    end_h, end_m = map(int, end.split(":"))
    begin, finish = begin_h * 60 + begin_m, end_h * 60 + end_m
    minute = local.hour * 60 + local.minute
    inside = begin <= minute < finish if begin < finish else minute >= begin or minute < finish
    if begin == finish or not inside:
        return None
    until = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if until <= local:
        until += timedelta(days=1)
    return until.astimezone(UTC)


class AttentionPolicy:
    def decide(self, candidate, preferences, history, *, now=None):
        now = now or datetime.now(UTC)
        if candidate["expires_at"] <= now:
            return AttentionDecision("cancel", "window_expired")
        # Ordinary input and explicit promises are not ranked as unsolicited novelty.
        if candidate.get("kind") == "reply":
            return AttentionDecision("send", "human_input", target_ref=candidate["target_ref"])
        if candidate.get("delivery_kind") == "create_only":
            return AttentionDecision("archive", "creation_without_delivery")
        if candidate["due_at"] > now:
            return AttentionDecision("defer", "not_due", candidate["due_at"])
        if not preferences.get("enabled", True) and not candidate.get("explicit"):
            return AttentionDecision("archive", "contact_disabled")
        until = quiet_until(now, preferences["timezone"], preferences["quiet_start"], preferences["quiet_end"])
        if until:
            return AttentionDecision("defer", "quiet_hours", until)
        if preferences.get("availability") in {"busy", "sleeping"} and (preferences.get("available_until") or now) > now:
            return AttentionDecision("defer", "person_unavailable", preferences["available_until"])
        if candidate.get("explicit"):
            return AttentionDecision("send", "explicit_commitment", target_ref=candidate["target_ref"])
        local_day = now.astimezone(ZoneInfo(preferences["timezone"])).date()
        today = [item for item in history if item["delivered_at"] and item["delivered_at"].astimezone(ZoneInfo(preferences["timezone"])).date() == local_day]
        if len(today) >= preferences["daily_quota"]:
            tomorrow = (now.astimezone(ZoneInfo(preferences["timezone"])) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return AttentionDecision("defer", "daily_contact_budget", tomorrow.astimezone(UTC))
        unanswered = sum(1 for item in history if item["delivered_at"] and not item["replied_at"])
        if unanswered >= preferences["unanswered_limit"]:
            return AttentionDecision("archive", "waiting_for_a_natural_reply")
        if history and history[0]["delivered_at"]:
            next_time = history[0]["delivered_at"] + timedelta(seconds=preferences["cooldown_seconds"])
            if next_time > now:
                return AttentionDecision("defer", "contact_spacing", next_time)
        return AttentionDecision("send", "current_content_and_available_time", target_ref=candidate["target_ref"])
