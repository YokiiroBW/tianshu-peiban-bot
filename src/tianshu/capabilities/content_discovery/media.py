"""Read-only pages from existing Media owners, narrowed by trusted instance configuration."""

from ..contracts import Fault, require
from ..media.identity import canonical_part_url, canonical_video_url, item_key, media_key
from .candidates import candidate


class MediaCatalog:
    def __init__(self, media):
        self.media = media

    def read(self, source, config, position, limit, acquired):
        if source == "bilibili":
            return self.bilibili(config, position, limit, acquired)
        if source == "music" and getattr(self.media, "music", None) is None:
            return [], "not_configured", None
        flag = {"music": "include_music", "books": "include_books", "comics": "include_comics"}[
            source
        ]
        if not config.get(flag):
            return [], "not_configured", None
        kind = {"music": "music_source", "books": "book", "comics": "comic"}[source]
        rows, after = self.media.repo.document_page(
            kind, limit=limit, after_id=position.get("after")
        )
        results, failed = [], False
        for row in rows:
            try:
                item = getattr(self, source)(row, config, acquired)
                if item is not None:
                    results.append(item)
            except (Fault, ValueError, KeyError, TypeError, OSError):
                failed = True
        return results, "partial" if failed else "ok", {"after": after} if after else None

    def bilibili(self, config, position, limit, acquired):
        subscriptions = config.get("subscription_ids", [])
        index = position.get("subscription", 0)
        require(type(index) is int and 0 <= index <= len(subscriptions), "invalid_input", 400)
        if index < len(subscriptions):
            identifier = subscriptions[index]
            row = self.media.repo.get("subscription", identifier)
            if not row or row.get("state") == "removed":
                return [], "partial", self.next_subscription(config, index)
            if position.get("phase") == "metadata":
                rows, following = self.video_page(
                    position, limit, acquired, subscription=identifier
                )
                return rows, "ok", following or self.next_subscription(config, index)
            members, after = self.media.repo.member_page(
                identifier, limit=limit, after=position.get("after")
            )
            # One bounded job page enriches this page; never scan jobs per member.
            jobs, _ = self.media.repo.jobs(limit=128)
            enriched = {}
            for job in jobs:
                if job.get("subscription_id") == identifier:
                    for item in self.video_job(job, acquired):
                        enriched.setdefault(item["content_key"], item)
            result = []
            for member in members:
                bvid, cid = member["bvid"], member["cid"]
                key = media_key(bvid, cid) if cid else item_key(bvid)
                result.append(
                    enriched.get(key)
                    or candidate(
                        "bilibili",
                        key,
                        kind="video",
                        title=None,
                        owner_id=key,
                        url=canonical_video_url(bvid) if cid is None else None,
                        acquired=acquired,
                    )
                )
            continuation = (
                {"subscription": index, "after": after}
                if after
                else {"subscription": index, "phase": "metadata"}
            )
            return result, "ok", continuation
        if not config.get("include_video_library"):
            return [], "not_configured" if not subscriptions else "ok", None
        rows, following = self.video_page({**position, "subscription": index}, limit, acquired)
        return rows, "ok", following

    def video_page(self, position, limit, acquired, *, subscription=None):
        jobs, more = self.media.repo.jobs(limit=1, after=position.get("job_after"))
        if not jobs:
            return [], None
        job = jobs[0]
        rows = (
            self.video_job(job, acquired)
            if subscription is None or job.get("subscription_id") == subscription
            else []
        )
        offset = position.get("part_offset", 0) if position.get("job_id") == job["job_id"] else 0
        require(type(offset) is int and 0 <= offset <= 128, "invalid_input", 400)
        if len(rows) > offset + limit:
            continuation = {
                "subscription": position.get("subscription", 0),
                "phase": position.get("phase", "library"),
                "job_after": position.get("job_after"),
                "job_id": job["job_id"],
                "part_offset": offset + limit,
            }
        else:
            continuation = (
                {
                    "subscription": position.get("subscription", 0),
                    "phase": position.get("phase", "library"),
                    "job_after": [int(job["created_at"]), job["job_id"]],
                }
                if more
                else None
            )
        return rows[offset : offset + limit], continuation

    @staticmethod
    def next_subscription(config, index):
        if index + 1 < len(config.get("subscription_ids", [])) or config.get(
            "include_video_library"
        ):
            return {"subscription": index + 1}
        return None

    @staticmethod
    def video_job(row, acquired):
        if not row.get("bvid") or not row.get("snapshot"):
            return []
        snapshot, result = row["snapshot"], []
        for part in snapshot.get("parts", []):
            cid = part["cid"]
            if cid not in row.get("selected_cids", [row.get("cid")]):
                continue
            key = media_key(row["bvid"], cid)
            result.append(
                candidate(
                    "bilibili",
                    key,
                    kind="video",
                    title=part.get("title") or snapshot.get("title"),
                    authors=[creator.get("name") for creator in snapshot.get("creators", [])],
                    summary=snapshot.get("description", ""),
                    owner_id=key,
                    url=canonical_part_url(row["bvid"], part["index"]),
                    observed=snapshot.get("captured_at"),
                    published=snapshot.get("published_at"),
                    acquired=acquired,
                )
            )
        return result

    def music(self, source, config, acquired):
        # Empty scope from a historical import is not a public-access grant.
        if source.get("private_scope", "") not in config.get("music_scopes", []):
            return None
        recording = self.media.music.library.catalog.recording(source["recording_id"])
        require(recording, "not_found", 404)
        metadata = source["metadata"]
        identifier = recording["recording_id"]
        return candidate(
            "music",
            "music:recording:" + identifier,
            kind="audio",
            title=metadata.get("title"),
            authors=metadata.get("artists", []),
            owner_id=identifier,
            source_key=source["source_key"],
            version=source.get("revision"),
            observed=source.get("updated_at"),
            acquired=acquired,
        )

    def books(self, row, config, acquired):
        row = self.media.books.catalog.book(row["book_id"])
        metadata = row["metadata"]
        return candidate(
            "books",
            "book:" + row["book_id"],
            kind="book",
            title=metadata.get("title"),
            authors=[metadata.get("author")],
            summary=metadata.get("description", ""),
            owner_id=row["book_id"],
            version=row.get("revision"),
            observed=row.get("updated_at"),
            acquired=acquired,
        )

    def comics(self, row, config, acquired):
        row = self.media.comics.catalog.comic(row["comic_id"])
        if row.get("library_kind") not in config.get("comic_libraries", ["general"]):
            return None
        metadata = row["metadata"]
        return candidate(
            "comics",
            "comic:" + row["comic_id"],
            kind="comic",
            title=metadata.get("title"),
            authors=[metadata.get("author")],
            summary=metadata.get("description", ""),
            owner_id=row["comic_id"],
            version=row.get("revision"),
            observed=row.get("updated_at"),
            acquired=acquired,
        )
