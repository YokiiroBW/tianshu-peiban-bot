"""Comic identities, source editions and chapter jobs share the media repository."""

import json
import re

from ...contracts import digest, require
from ..repository import VISIBLE
from .packages import safe_name
from .versions import chapter_evidence, choose_version, comic_evidence, eligible


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{24}", value), "invalid_input", 400)
    return value


def public_comic(row):
    return {
        key: row.get(key)
        for key in (
            "comic_id",
            "metadata",
            "source_id",
            "source_name",
            "source_type",
            "library_kind",
            "chapter_count",
            "downloaded",
            "revision",
            "updated_at",
            "group_key",
            "has_cover",
            "language_evidence",
        )
    }


class Catalog:
    def __init__(self, comics):
        self.comics, self.media, self.repo = comics, comics.media, comics.repo

    def chapter_jobs(self, comic, chapters):
        from ..runtime import public_job

        current = {c["chapter_id"]: c["version_key"] for c in chapters}
        result = {}
        with self.repo.transaction() as db:
            rows = db.execute(
                'SELECT body FROM jobs WHERE '
                + VISIBLE
                + " AND (body::jsonb #>> '{comic_id}')=%s"
                + " AND (body::jsonb #>> '{comic_library_kind}')=%s ORDER BY created DESC,id DESC",
                (comic["comic_id"], comic["library_kind"]),
            ).fetchall()
        fields = (
            "job_id",
            "state",
            "stage",
            "code",
            "comic_pages_done",
            "comic_pages_total",
            "can_retry",
        )
        for stored in rows:
            row = json.loads(stored[0])
            chapter_id = row.get("comic_chapter_id")
            if chapter_id in result or chapter_id not in current:
                continue
            if row.get("comic_chapter", {}).get("version_key") != current[chapter_id]:
                continue
            public = public_job(row)
            result[chapter_id] = {key: public.get(key) for key in fields}
        return result

    def comic(self, value, db=None):
        identity(value)
        row = (
            self.repo._document(db, "comic", value)
            if db is not None
            else self.repo.get("comic", value)
        )
        require(row, "not_found", 404)
        return row

    def chapters(self, value, db=None):
        row = (
            self.repo._document(db, "comic_directory", value)
            if db is not None
            else self.repo.get("comic_directory", value)
        )
        return row.get("chapters", []) if row else []

    def save_sources(self, rows, engine_id):
        with self.repo.transaction() as db:
            result = []
            for row in rows:
                source_id = str(row["id"])
                key = engine_id + ":" + source_id
                previous = self.repo._document(db, "comic_source", key)
                record = {
                    **(previous or {}),
                    "source_id": source_id,
                    "source_key": key,
                    "engine_id": engine_id,
                    "name": row.get("displayName") or row["name"],
                    "language": row.get("lang", ""),
                    "content_warning": row.get("contentWarning", "UNKNOWN"),
                    "enabled": previous.get("enabled", True) if previous else True,
                    "available": True,
                    "code": previous.get("code", "comic_source_unchecked")
                    if previous
                    else "comic_source_unchecked",
                    "inventory_at": self.repo.clock(),
                }
                saved = (
                    self.repo.fact("comic_source", key, record, previous["revision"], db=db)
                    if previous
                    else self.repo.put("comic_source", key, record, db=db)
                )
                result.append(saved)
            present = {r["source_id"] for r in result}
            for stored in db.execute(
                "SELECT body FROM documents WHERE kind='comic_source'"
            ).fetchall():
                import json

                old = json.loads(stored[0])
                if old["engine_id"] == engine_id and old["source_id"] not in present:
                    self.repo.fact(
                        "comic_source",
                        old["source_key"],
                        {**old, "available": False},
                        old["revision"],
                        db=db,
                    )
            return result

    def register(self, source, item):
        require(
            isinstance(item.get("url"), str) and item.get("title") and type(item.get("id")) is int,
            "comic_engine_invalid_response",
            503,
        )
        comic_id = digest(
            {"engine": source["engine_id"], "source": source["source_id"], "url": item["url"]}
        )[:24]
        with self.repo.transaction() as db:
            previous = self.repo._document(db, "comic", comic_id)
            metadata = {
                "title": str(item["title"])[:500],
                "author": str(item.get("author") or "")[:500],
                "artist": str(item.get("artist") or "")[:500],
                "description": str(item.get("description") or "")[:20000],
                "language": source.get("language", ""),
                "tags": [str(t)[:100] for t in (item.get("genre") or [])[:100]],
                "direction": "rtl",
            }
            if previous:
                metadata.update(
                    {key: previous["metadata"][key] for key in previous.get("manual_fields", [])}
                )
            value = {
                **(previous or {}),
                "comic_id": comic_id,
                "engine_id": source["engine_id"],
                "engine_manga_id": item["id"],
                "source_id": source["source_id"],
                "source_name": source["name"],
                "source_type": "remote",
                "source_url": item["url"],
                "thumbnail_url": item.get("thumbnailUrl") or "",
                "has_cover": bool(item.get("thumbnailUrl")),
                "metadata": metadata,
                "language_evidence": comic_evidence(source, item, previous),
                "library_kind": previous.get("library_kind")
                if previous
                else "general"
                if source.get("content_warning") == "SAFE"
                else "private",
                "chapter_count": previous.get("chapter_count", 0) if previous else 0,
                "downloaded": previous.get("downloaded", 0) if previous else 0,
                "manual_fields": previous.get("manual_fields", []) if previous else [],
                "updated_at": self.repo.clock(),
                "group_key": digest(
                    {"title": metadata["title"].casefold(), "author": metadata["author"].casefold()}
                )[:24],
            }
            return self.repo.put("comic", comic_id, value, db=db)

    def save_directory(self, comic_id, rows):
        require(0 < len(rows) <= 20000, "comic_directory_empty", 409)
        # Suwayomi exposes oldest-first ranks, unlike the extension's raw list.
        # Stable chapter identity and existing file names never use a fresh rank.
        rows = sorted(rows, key=lambda r: (int(r.get("sourceOrder", 0)), int(r["id"])))
        with self.repo.transaction() as db:
            comic = self.comic(comic_id, db)
            old = {c["chapter_id"]: c for c in self.chapters(comic_id, db)}
            chapters, seen = [], set()
            for index, row in enumerate(rows):
                require(
                    type(row.get("id")) is int and isinstance(row.get("url"), str),
                    "comic_engine_invalid_response",
                    503,
                )
                chapter_id = digest(
                    {"comic": comic_id, "url": row["url"], "scanlator": row.get("scanlator") or ""}
                )[:24]
                if chapter_id in seen:
                    continue
                seen.add(chapter_id)
                number = row.get("chapterNumber")
                chapter = {
                    "chapter_id": chapter_id,
                    "engine_chapter_id": row["id"],
                    "title": str(row.get("name") or "未命名章节")[:500],
                    "number": str(number) if number is not None and number >= 0 else "",
                    "order": index + 1,
                    "file_order": old.get(chapter_id, {}).get("file_order", index + 1),
                    "scanlator": str(row.get("scanlator") or "")[:500],
                    "source_url": row["url"],
                    "upload_date": str(row.get("uploadDate") or ""),
                    "page_count": max(0, row.get("pageCount") or 0),
                    "language_evidence": chapter_evidence(comic, row),
                }
                chapter["version_key"] = digest(
                    {key: chapter[key] for key in ("chapter_id", "upload_date")}
                )[:24]
                chapters.append(chapter)
            # Hidden source chapters keep their catalogue identity and existing reader link.
            chapters.extend(
                {**c, "source_missing": True} for key, c in old.items() if key not in seen
            )
            self.repo.put(
                "comic_directory",
                comic_id,
                {"comic_id": comic_id, "chapters": chapters, "checked_at": self.repo.clock()},
                db=db,
            )
            comic = self.repo.put(
                "comic",
                comic_id,
                {**comic, "chapter_count": len(chapters), "updated_at": self.repo.clock()},
                db=db,
            )
            return comic, chapters

    def local_import(self, relative, title, chapters, library_kind):
        require(library_kind in {"general", "private"}, "invalid_input", 400)
        settings = self.comics.settings.read()
        root = settings["import_directory"]
        comic_id = digest({"root": root, "relative": relative})[:24]
        with self.repo.transaction() as db:
            old = self.repo._document(db, "comic", comic_id)
            row = {
                **(old or {}),
                "comic_id": comic_id,
                "metadata": old["metadata"]
                if old
                else {
                    "title": title,
                    "author": "",
                    "artist": "",
                    "description": "",
                    "language": "zh",
                    "direction": "rtl",
                    "tags": [],
                },
                "source_type": "local",
                "source_id": "local",
                "source_name": "本地图片与漫画包",
                "import_directory": root,
                "import_socket": settings.get("import_socket", ""),
                "import_relative": relative,
                "library_kind": old.get("library_kind", library_kind) if old else library_kind,
                "chapter_count": len(chapters),
                "downloaded": old.get("downloaded", 0) if old else 0,
                "has_cover": False,
                "updated_at": self.repo.clock(),
                "group_key": comic_id,
            }
            for index, chapter in enumerate(chapters):
                chapter.update(
                    chapter_id=digest({"comic": comic_id, "path": chapter["relative_path"]})[:24],
                    order=index + 1,
                    file_order=index + 1,
                    number=str(index + 1),
                    scanlator="",
                    version_key=chapter["input_digest"][:24],
                )
            row = self.repo.put("comic", comic_id, row, db=db)
            self.repo.put(
                "comic_directory",
                comic_id,
                {"comic_id": comic_id, "chapters": chapters, "checked_at": self.repo.clock()},
                db=db,
            )
            return row, chapters

    def enqueue(self, comic, chapters, *, db, version_policy=None):
        target = self.comics.settings.target(comic["library_kind"], db)
        jobs = []
        for chapter in chapters:
            metadata = dict(comic["metadata"])
            if version_policy and metadata.get("language") in {"all", "", "unknown"}:
                metadata["language"] = version_policy["language"]
            edition = safe_name(chapter.get("scanlator") or "原来源版本", 80)
            relative = (
                safe_name(comic["metadata"]["title"])
                + " ["
                + comic["comic_id"][:8]
                + "]/"
                + safe_name(metadata.get("language") or "未知语言", 20)
                + "-"
                + edition
            )
            filename = (
                f"{chapter['file_order']:06d} "
                + safe_name(chapter["title"], 100)
                + " ["
                + chapter["chapter_id"][:8]
                + "].cbz"
            )
            key = digest(
                {
                    "chapter": chapter["chapter_id"],
                    "version": chapter["version_key"],
                    "metadata": metadata,
                    "target": target,
                }
            )
            payload = {
                "media_kind": "comic",
                "comic_id": comic["comic_id"],
                "comic_chapter_id": chapter["chapter_id"],
                "comic_chapter": chapter,
                "comic_metadata": metadata,
                "comic_library_kind": comic["library_kind"],
                "comic_target": target,
                "comic_relative_path": relative + "/" + filename,
                "comic_pages_done": 0,
                "comic_pages_total": chapter.get("page_count", 0),
                "comic_artifact": None,
                "title": comic["metadata"]["title"] + " · " + chapter["title"],
                "creator": comic["metadata"].get("author", ""),
                "provider": "suwayomi" if comic["source_type"] == "remote" else "local",
                "source_id": comic["source_id"],
                "subscription_id": comic["comic_id"],
                "target_id": "comic-" + comic["library_kind"],
                "destination_directory": target["komga_root"],
                "account_id": None,
                "quality": {"mode": "comic-original"},
            }
            jobs.append(self.repo.insert_job(payload, "comic:" + key, db))
        return jobs

    def subscribe(self, comic_id, body, db):
        comic = self.comic(comic_id, db)
        require(comic["source_type"] == "remote", "comic_local_subscription_unsupported", 409)
        previous = self.repo._document(db, "comic_subscription", comic_id)
        if previous and previous.get("state") == "removed":
            # A new subscription chooses a new version/range, retaining archived content.
            previous = None
        interval = body.get("interval_seconds", self.comics.settings.read(db)["interval_seconds"])
        require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
        mode = body.get("mode", "all")
        require(mode in {"all", "new", "latest"}, "invalid_input", 400)
        latest = body.get("latest_count", 5)
        require(type(latest) is int and 1 <= latest <= 500, "invalid_input", 400)
        scanlator = body.get("scanlator", "")
        require(isinstance(scanlator, str) and len(scanlator) <= 500, "invalid_input", 400)
        all_chapters = self.chapters(comic_id, db)
        policy = choose_version(comic, all_chapters, body, previous)
        selection = {"version_policy": policy, "scanlator": scanlator}
        chapters = eligible(comic, all_chapters, selection)
        current = {
            "comic_id": comic_id,
            "state": "active",
            "interval_seconds": interval,
            "scanlator": scanlator,
            "seen": previous.get("seen", [])
            if previous
            else [c["version_key"] for c in chapters]
            if mode == "new"
            else [],
            "next_scan_at": self.repo.clock(),
            "last_scan_at": previous.get("last_scan_at") if previous else None,
            "code": "queued",
        }
        if policy:
            current["version_policy"] = policy
            current["scanlator"] = policy["scanlator"]
        selected = [] if mode == "new" else chapters[-latest:] if mode == "latest" else chapters
        jobs = self.enqueue(comic, selected, db=db, version_policy=policy) if selected else []
        current["seen"] = list(
            dict.fromkeys([*current["seen"], *(c["version_key"] for c in chapters)])
        )
        saved = self.repo.put(
            "comic_subscription",
            comic_id,
            {**(previous or {}), **current},
            body.get("expected_revision"),
            db=db,
        )
        return {"subscription": saved, "job_ids": [j["job_id"] for j in jobs]}
