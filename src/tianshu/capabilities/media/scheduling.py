"""Shared due-scan loop; each source family runs independently in Media's lifecycle."""

import asyncio

from ..contracts import Fault


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
