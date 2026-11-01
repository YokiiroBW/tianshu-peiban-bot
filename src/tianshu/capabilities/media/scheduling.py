"""Shared due-scan loop; each source family runs independently in Media's lifecycle."""

import asyncio
from datetime import UTC, datetime

from ..contracts import Fault


SCAN_TASKS = {"subscription": "subscriptions", "book_subscription": "book_subscriptions",
              "comic_subscription": "comic_subscriptions", "music_playlist": "music_playlists",
              "book_feed": "book_sources"}


def wake_document(repository, connection, kind, body):
    """Repository changes and the existing durable wakeup commit together."""
    from .background import wake_sync

    if repository.runtime is None:
        return
    if kind in SCAN_TASKS:
        if body.get("state") == "active":
            wake_sync(repository.runtime, connection, SCAN_TASKS[kind],
                datetime.fromtimestamp(max(repository.clock(), body.get("next_scan_at", 0)), UTC))
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
        wake_sync(repository.runtime, connection, name,
            datetime.fromtimestamp(max(repository.clock(), body.get(field, 0)), UTC))
        return
    from ..execution_context import CURRENT_ACTION

    # Internal observations do not wake their entire family on each revision.
    # New files and actual user/actor commands wake their affected consumers.
    names = (("music_lyrics", "music_maintenance", "music_navidrome") if kind == "music_library_file"
             else ("book_audio",) if kind == "book_audio_plan"
             else ("music_imports", "music_navidrome", "music_maintenance", "music_lyrics")
             if kind.startswith("music_") and CURRENT_ACTION.get() else ())
    for name in names:
        wake_sync(repository.runtime, connection, name, datetime.fromtimestamp(repository.clock(), UTC))


async def scan_due(media, kind, identity_field, scan):
    for row in await media.local(media.repo.list, kind):
        if row["state"] == "active" and row["next_scan_at"] <= media.repo.clock():
            try:
                from tianshu.types import Action,OriginKind,SourceRef
                from ..serialization import context_from
                from ..contracts import digest
                if not row.get("context"):raise Fault("subscription_context_required",503)
                context=context_from(row["context"])
                domain={"subscription":"media","book_subscription":"books","comic_subscription":"comics","music_playlist":"music","book_feed":"books"}[kind]
                key="media-scan:"+digest([kind,row[identity_field],row["revision"],row["next_scan_at"]])
                field={"subscription":"subscription_id","book_subscription":"book_id","comic_subscription":"comic_id","music_playlist":"playlist_id","book_feed":"feed_id"}[kind]
                operation="feeds/refresh" if kind=="book_feed" else "playlists/scan" if domain=="music" else "subscriptions/scan"
                body={field:row[identity_field]}
                if kind=="subscription":body.update(recompute=False,client_request_id=key)
                await media.capabilities.actions.invoke(Action(key,key,domain+"."+operation.replace("/","."),context.actor_id,context,body,OriginKind.TIMER,source_refs=tuple(SourceRef(**x) for x in row.get("source_refs",[]))))
            except Fault:
                pass


async def pump_scans(media, kind, identity_field, scan):
    await scan_due(media, kind, identity_field, scan)
