"""Shared due-scan loop; each source family runs independently in Media's lifecycle."""

from datetime import UTC, datetime

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import Action, OriginKind, SourceRef
from ..contracts import Fault, digest
from ..serialization import context_from


SCAN_TASKS = {"subscription": "subscriptions", "book_subscription": "book_subscriptions",
              "comic_subscription": "comic_subscriptions", "music_playlist": "music_playlists",
              "book_feed": "book_sources"}
TRACKING_STATES = ("dispatching", "submitted", "pending", "retry_wait")


def unresolved_schedule(saved):
    return bool(saved.get("action_key") and saved.get("state") in TRACKING_STATES)


def scan_deadline(row):
    """An unresolved original Action owns its observation deadline, not a new scan."""
    saved = row.get("schedule") or {}
    if row.get("state") == "removed" or saved.get("state") == "blocked":
        return None
    if unresolved_schedule(saved):
        return saved.get("next_check_at") or 0
    if row.get("state") != "active":
        return None
    return max(row.get("next_scan_at", 0),
               (saved.get("next_check_at") or 0) if saved.get("state") == "retry_wait" else 0)


def wake_document(repository, connection, kind, body):
    """Repository changes and the existing durable wakeup commit together."""
    from .background import wake_sync

    if repository.runtime is None:
        return

    def due_at(value):
        # Immediate repository changes use the same DB clock as the queue/lease;
        # authored future deadlines retain their absolute time.
        return datetime.fromtimestamp(value, UTC) if value > repository.clock() else None

    if kind in SCAN_TASKS:
        deadline = scan_deadline(body)
        if deadline is not None:
            wake_sync(repository.runtime, connection, SCAN_TASKS[kind],
                due_at(deadline))
        return
    timed = {
        "book_audio_job": ("book_audio", "next_at"),
        "comic_extension_policy": ("comic_extensions", "next_check_at"),
        "music_import_scan": ("music_imports", "next_scan_at"),
        "music_navidrome_playlist": ("music_navidrome", "next_sync_at"),
        "music_quality_check": ("music_maintenance", "next_check_at"),
        "music_lyrics": ("music_lyrics", "next_check_at"),
    }
    if kind in timed:
        if kind == "book_audio_job" and body.get("state") not in {"queued", "waiting"}:
            return
        name, field = timed[kind]
        wake_sync(repository.runtime, connection, name, due_at(body.get(field, 0)))
        return
    from ..execution_context import CURRENT_ACTION

    # Internal observations do not wake their entire family on each revision.
    # New files and actual user/actor commands wake their affected consumers.
    names = (("music_lyrics", "music_maintenance", "music_navidrome") if kind == "music_library_file"
             else ("book_audio",) if kind == "book_audio_plan"
             else ("music_imports", "music_navidrome", "music_maintenance", "music_lyrics")
             if kind.startswith("music_") and CURRENT_ACTION.get() else ())
    for name in names:
        wake_sync(repository.runtime, connection, name)


def author_schedule(kind, body, action):
    """An explicit author edit/resume can replace a blocked timer's old grant."""
    if action is None or action.origin_kind not in {OriginKind.HUMAN, OriginKind.ACTOR}:
        return body
    domain = {"subscription": "media", "book_subscription": "books",
              "comic_subscription": "comics", "music_playlist": "music", "book_feed": "books"}[kind]
    operation = action.capability_id.removeprefix(domain + ".")
    if not action.capability_id.startswith(domain + ".") or operation not in {
        "subscriptions.save", "subscriptions.control", "feeds.save", "feeds.control",
        "warehouse.subscribe", "playlists.subscribe", "playlists.control",
    }:
        return body
    refs = unique_refs(tuple(SourceRef(**value) for value in body.get("source_refs", [])), action.source_refs)
    return {**body, "context": wire(action.context), "source_refs": wire(refs),
            "schedule": {}}


def action_key(kind, identity, row):
    saved = row.get("schedule") or {}
    if unresolved_schedule(saved):
        return saved["action_key"]
    identity_parts = [kind, identity, row["revision"], row["next_scan_at"]]
    if saved.get("retry_generation", 0):
        identity_parts.append(saved["retry_generation"])
    return "media-scan:" + digest(identity_parts)


async def scan_due(media, kind, identity_field, scan):
    """Refresh authors, never in-flight Actions; each source owns its retry/stop state."""
    rows = await media.local(media.repo.list, kind)
    counts = {"dispatching": 0, "submitted": 0, "pending": 0, "completed": 0,
              "retry_wait": 0, "blocked": 0}
    codes = set()
    for row in rows:
        saved = row.get("schedule") or {}
        deadline = scan_deadline(row)
        if deadline is None or deadline > media.repo.clock():
            if saved.get("state") in counts:
                counts[saved["state"]] += 1
                if saved.get("code"):
                    codes.add(saved["code"])
            continue
        identity = row[identity_field]
        key = action_key(kind, identity, row)
        begin = not unresolved_schedule(saved)
        admitted = bool(saved.get("action_admitted", saved.get("state") in {"submitted", "pending"})) if not begin else False
        domain = {"subscription": "media", "book_subscription": "books",
                  "comic_subscription": "comics", "music_playlist": "music",
                  "book_feed": "books"}[kind]
        operation = ("feeds.refresh" if kind == "book_feed" else "playlists.scan"
                     if kind == "music_playlist" else "subscriptions.scan")
        capability = domain + "." + operation
        state, code, failures = "submitted", None, saved.get("failures", 0)
        retry_generation = saved.get("retry_generation", 0)
        context = None
        blocked, keep_key = False, True
        terminal_failure = False

        def observation(state, code=None, *, next_check_at, failures=failures):
            return {"state": state, "code": code, "failures": failures,
                "action_key": key if keep_key else None, "action_admitted": admitted,
                "retry_generation": retry_generation,
                "configuration_revision": saved.get("configuration_revision", row["revision"])
                    if not begin else row["revision"],
                "observed_at": media.repo.clock(), "next_check_at": next_check_at}

        try:
            if not row.get("context"):
                raise Fault("subscription_context_required", 403)
            previous = context_from(row["context"])
            permissions = media.capabilities.catalog.handler(capability).definition.permissions
            # The current scan definitions need no named permission. Passing None
            # preserves that contract; Actions still checks the complete definition.
            context = await media.capabilities.authority.refresh_scheduled_context(
                previous, permission=next(iter(permissions), None), require_actor=False)
            refs = tuple(SourceRef(**value) for value in row.get("source_refs", []))
            await media.capabilities.authority.check(context, refs)
            if not admitted:
                # Reserve the original key before its worker can finish and revise
                # the business document. A restart finds this key before admission.
                reserved = observation("dispatching", next_check_at=media.repo.clock() + 60)
                if not await media.local(media.repo.record_schedule, kind, identity, row, reserved,
                                         begin=begin, context=wire(context)):
                    continue
                row = {**row, "schedule": reserved, "context": wire(context)}
                saved, begin = reserved, False
            existing = await media.capabilities.actions.find(capability, key, context)
            if existing is None:
                if admitted:
                    raise Fault("scheduled_action_missing", 409)
                if row["state"] != "active":
                    raise Fault("scheduled_subscription_inactive", 409)
                body = {identity_field: identity}
                if kind == "subscription":
                    body.update(recompute=False, client_request_id=key)
                receipt = await media.capabilities.actions.invoke(Action(
                    key, key, capability, context.actor_id, context, body, OriginKind.TIMER,
                    source_refs=refs), background=True)
                admitted = True
                execution, code, effect = receipt.execution, receipt.error_code, receipt.effect
            else:
                admitted = True
                # The original context/attempt stays immutable even after an epoch edit.
                execution = existing["receipt"]["execution"]
                code = existing["receipt"].get("error_code")
                effect = existing["receipt"].get("effect")
                state = "pending"
            if execution in {"failed", "succeeded"} and effect == "unknown":
                state, blocked = "blocked", True
                code = code or "scheduled_effect_unknown"
            elif execution == "succeeded":
                state, failures = "completed", 0
            elif execution in {"unknown", "cancelled"}:
                state, blocked = "blocked", True
                code = code or "scheduled_action_" + execution
            elif execution == "failed":
                terminal_failure = True
                # Business due/revision may be unchanged by a failed scan. Only
                # this known terminal result grants the next read a new identity.
                retry_generation += 1
                raise Fault(code or "scheduled_action_failed", 503)
        except Exception as error:
            code = getattr(error, "code", "media_schedule_failed")
            blocked = (isinstance(error, AppError) and error.status in {401, 403, 410}
                       or code in {"actor_disabled", "subscription_context_required", "idempotency_conflict",
                                   "source_revoked", "scheduled_action_missing", "scheduled_subscription_inactive"})
            state = "blocked" if blocked else "retry_wait"
            failures += 1
            # Preserve a possibly admitted execution through transport/epoch
            # races. Only a known terminal failure permits a new scheduled read.
            keep_key = not terminal_failure
        delay = min(3600, 60 * 2 ** min(max(failures - 1, 0), 6)) if state == "retry_wait" else 60
        result = observation(state, code, failures=failures,
            next_check_at=None if state in {"completed", "blocked"} else media.repo.clock() + delay)
        if not await media.local(media.repo.record_schedule, kind, identity, row, result,
                                 begin=begin, blocked=blocked,
                                 context=wire(context) if context is not None else None):
            continue
        counts[state] += 1
        if code:
            codes.add(code)
    degraded = counts["blocked"] or counts["retry_wait"]
    return {"schedule_observation": {
        "state": "partial" if degraded and sum(counts[name] for name in ("dispatching", "submitted", "pending", "completed"))
                 else "blocked" if counts["blocked"] else "retry_wait" if counts["retry_wait"] else "scheduled",
        "code": sorted(codes)[0] if codes else None,
        "scan": {"kind": kind, **counts, "codes": sorted(codes)},
    }}


async def pump_scans(media, kind, identity_field, scan):
    return await scan_due(media, kind, identity_field, scan)
