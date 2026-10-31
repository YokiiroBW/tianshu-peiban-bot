"""Book records share the existing media repository and encrypted source storage."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ...contracts import digest, require
from .documents import clean_text, plain_text, title_key
from .engine import capabilities
from .organization import book_category
from .matching import author_key

METADATA_FIELDS = frozenset(
    {
        "title",
        "author",
        "description",
        "language",
        "tags",
        "category",
        "publisher",
        "published_at",
        "isbn",
        "series",
        "series_index",
        "cover_url",
    }
)


def public_url(value):
    parsed = urlsplit(value or "")
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))


def public_source(source):
    return {
        key: source.get(key)
        for key in (
            "source_id",
            "name",
            "group",
            "url",
            "enabled",
            "capabilities",
            "revision",
            "checked_at",
            "code",
            "feed_id",
            "upstream_removed",
        )
    }


def public_book(book):
    result = {
        key: book.get(key)
        for key in (
            "book_id",
            "metadata",
            "metadata_revision",
            "field_sources",
            "manual_fields",
            "source_id",
            "source_name",
            "source_type",
            "chapter_count",
            "downloaded",
            "latest_chapter",
            "updated_at",
            "revision",
            "artifact",
            "status",
            "group_key",
            "has_cover",
            "readability",
            "source_selection",
        )
    }
    result["metadata"] = {
        **book["metadata"],
        "category": book_category(book["metadata"], book.get("source_name", "")),
    }
    if book.get("source_selection"):
        selection = book["source_selection"]
        result["source_selection"] = {
            key: selection.get(key)
            for key in (
                "mode",
                "checked_at",
                "code",
                "preferred_book_id",
                "preferred_source_name",
                "added_chapters",
                "unmatched_chapters",
                "chapters_by_source",
                "repaired_chapters",
            )
        }
        result["source_selection"]["candidates"] = [
            {
                key: row.get(key)
                for key in ("book_id", "source_name", "code", "readable", "latest_number")
            }
            for row in selection.get("candidates", [])
        ]
        result["source_selection"]["last_repair"] = selection.get("last_repair")
    return result


class Catalog:
    def __init__(self, media):
        self.media, self.repo = media, media.repo

    def settings(self, db=None):
        row = (
            self.repo._document(db, "book_settings", "library")
            if db
            else self.repo.get("book_settings", "library")
        )
        return row or {
            "directory": "",
            "interval_seconds": 21600,
            "auto_metadata": True,
            "open_library": True,
            "opds_url": "",
            "revision": 0,
        }

    def public_settings(self):
        row = self.settings()
        return {
            **{
                key: row.get(key)
                for key in (
                    "directory",
                    "interval_seconds",
                    "auto_metadata",
                    "open_library",
                    "opds_url",
                    "opds_username",
                    "revision",
                )
            },
            "opds_has_credentials": bool(row.get("opds_secret")),
        }

    def save_settings(self, body, db):
        directory = body.get("directory", "")
        require(isinstance(directory, str) and len(directory) <= 4096, "invalid_input", 400)
        directory = directory.strip()
        require(not directory or Path(directory).is_absolute(), "book_directory_absolute", 400)
        require(
            type(body.get("interval_seconds")) is int
            and 3600 <= body["interval_seconds"] <= 2592000,
            "invalid_input",
            400,
        )
        require(
            type(body.get("auto_metadata")) is bool and type(body.get("open_library")) is bool,
            "invalid_input",
            400,
        )
        opds = str(body.get("opds_url", "")).strip()
        if opds:
            from .network import origin

            origin(opds)
        current = self.settings(db)
        username = body.get("opds_username", current.get("opds_username", ""))
        password = body.get("opds_password", "")
        require(
            isinstance(username, str)
            and len(username) <= 256
            and isinstance(password, str)
            and len(password) <= 4096,
            "invalid_input",
            400,
        )
        secret = (
            current.get("opds_secret")
            if opds == current.get("opds_url") and username == current.get("opds_username", "")
            else None
        )
        if password:
            secret = self.media.accounts.seal("book-opds", password)
        saved = self.repo.put(
            "book_settings",
            "library",
            {
                "directory": directory,
                "interval_seconds": body["interval_seconds"],
                "auto_metadata": body["auto_metadata"],
                "open_library": body["open_library"],
                "opds_url": opds,
                "opds_username": username,
                "opds_secret": secret,
            },
            body.get("expected_revision"),
            db=db,
        )
        return {"settings": {key: value for key, value in saved.items() if key != "opds_secret"}}

    def source(self, identity):
        source = self.repo.get("book_source", identity)
        require(source and source["enabled"], "book_source_disabled", 409)
        rules = json.loads(self.media.accounts.unseal(identity, source["secret"]))
        return source, rules

    def import_sources(self, entries, db, *, feed_id=None):
        if isinstance(entries, dict):
            entries = [entries]
        require(
            isinstance(entries, list) and len(entries) <= 20000, "book_source_input_invalid", 400
        )
        results = []
        source_ids = []
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                entry = dict(entry)
                for key in ("ruleSearch", "ruleBookInfo", "ruleToc", "ruleContent", "ruleExplore"):
                    if key in entry and entry[key] in (None, []):
                        entry[key] = {}
                if entry.get("bookSourceType", 0) not in (0, None):
                    results.append(
                        {"index": index, "state": "invalid", "code": "book_source_type_unsupported"}
                    )
                    continue
            valid = isinstance(entry, dict) and all(
                isinstance(entry.get(key), str) and entry[key].strip()
                for key in ("bookSourceName", "bookSourceUrl")
            )
            valid = (
                valid
                and entry.get("bookSourceType", 0) in (0, None)
                and all(
                    isinstance(entry.get(key, {}), dict)
                    for key in ("ruleSearch", "ruleBookInfo", "ruleToc", "ruleContent")
                )
            )
            valid = (
                valid
                and len(entry["bookSourceName"]) <= 256
                and len(entry["bookSourceUrl"]) <= 8192
                and len(str(entry.get("bookSourceGroup", ""))) <= 256
                and type(entry.get("enabled", True)) is bool
            )
            if not valid:
                results.append(
                    {"index": index, "state": "invalid", "code": "book_source_input_invalid"}
                )
                continue
            serialized = json.dumps(entry, ensure_ascii=False)
            if len(serialized.encode("utf-8")) > 256 * 1024:
                results.append({"index": index, "state": "invalid", "code": "book_rule_too_large"})
                continue
            identity = digest({"feed": feed_id, "url": entry["bookSourceUrl"]})[:24]
            previous = self.repo._document(db, "book_source", identity)
            fingerprint = digest(entry)
            if previous and previous["digest"] == fingerprint:
                current_capabilities = capabilities(entry)
                if (
                    previous.get("upstream_removed")
                    or previous.get("capabilities") != current_capabilities
                ):
                    self.repo.put(
                        "book_source",
                        identity,
                        {
                            **previous,
                            "upstream_removed": False,
                            "capabilities": current_capabilities,
                        },
                        db=db,
                    )
                source_ids.append(identity)
                results.append({"index": index, "state": "unchanged", "source_id": identity})
                continue
            source = {
                "source_id": identity,
                "name": clean_text(entry["bookSourceName"], 256),
                "group": clean_text(entry.get("bookSourceGroup", ""), 256),
                "url": public_url(entry["bookSourceUrl"]),
                "feed_id": feed_id,
                "enabled": previous["enabled"] if previous else bool(entry.get("enabled", True)),
                "secret": self.media.accounts.seal(identity, serialized),
                "digest": fingerprint,
                "last_good_secret": previous.get("last_good_secret") if previous else None,
                "last_good_digest": previous.get("last_good_digest") if previous else None,
                "capabilities": capabilities(entry),
                "checked_at": None,
                "code": "book_source_imported",
                "upstream_removed": False,
            }
            self.repo.put("book_source", identity, source, db=db)
            source_ids.append(identity)
            results.append(
                {
                    "index": index,
                    "state": "updated" if previous else "imported",
                    "source_id": identity,
                }
            )
        return {
            "results": results,
            "source_ids": source_ids,
            "imported": sum(
                item["state"] in {"imported", "updated", "unchanged"} for item in results
            ),
            "invalid": sum(item["state"] == "invalid" for item in results),
        }

    def source_result(self, source, *, code="ready", good=False):
        current = self.repo.get("book_source", source["source_id"])
        if not current or current["digest"] != source["digest"]:
            return
        changes = {**current, "checked_at": self.repo.clock(), "code": code}
        if good:
            changes.update(last_good_secret=current["secret"], last_good_digest=current["digest"])
        self.repo.fact("book_source", current["source_id"], changes, current["revision"])

    def register(self, source, item, identity=None):
        url = str(item.get("bookUrl") or item.get("tocUrl") or "")
        require(
            url and isinstance(item.get("name"), str) and item["name"].strip(),
            "book_source_result_invalid",
            502,
        )
        identity = identity or digest({"source": source["source_id"], "url": url})[:24]
        with self.repo.transaction() as db:
            previous = self.repo._document(db, "book", identity)
            source_identity = {
                "title": plain_text(item["name"]).strip(),
                "author": plain_text(item.get("author", "")).strip(),
            }
            established = (previous or {}).get("source_identity")
            if (
                not established
                and previous
                and not set(previous.get("manual_fields", [])) & {"title", "author"}
            ):
                established = previous["metadata"]
            if established:
                require(
                    title_key(established["title"]) == title_key(source_identity["title"]),
                    "book_identity_mismatch",
                    409,
                )
                require(
                    not (
                        author_key(established.get("author"))
                        and author_key(source_identity.get("author"))
                    )
                    or author_key(established.get("author"))
                    == author_key(source_identity.get("author")),
                    "book_identity_mismatch",
                    409,
                )
            metadata = {
                "title": plain_text(item["name"])[:512],
                "author": plain_text(item.get("author", ""))[:512],
                "description": plain_text(item.get("intro", ""))[:20000],
                "cover_url": str(item.get("coverUrl", "")),
                "language": "zh-CN",
                "tags": [
                    tag.strip()
                    for tag in re.split(r"[,，/|]", str(item.get("kind", "")))
                    if tag.strip()
                ][:32],
            }
            if previous:
                for key in previous.get("manual_fields", []):
                    metadata[key] = previous["metadata"].get(key)
                metadata = {
                    **previous["metadata"],
                    **{key: value for key, value in metadata.items() if value},
                }
            body = {
                **(previous or {}),
                "book_id": identity,
                "metadata": metadata,
                "metadata_revision": previous.get("metadata_revision", 1)
                + int(previous["metadata"] != metadata)
                if previous
                else 1,
                "source_id": source["source_id"],
                "source_name": source["name"],
                "source_type": "remote",
                "source_identity": source_identity
                if source_identity["author"]
                else established or source_identity,
                "source_url": url,
                "toc_url": item.get("tocUrl", previous.get("toc_url", "") if previous else ""),
                "variables": item.get("variables", {}),
                "latest_chapter": clean_text(item.get("lastChapter", ""), 1000),
                "chapter_count": previous.get("chapter_count", 0) if previous else 0,
                "downloaded": previous.get("downloaded", 0) if previous else 0,
                "manual_fields": previous.get("manual_fields", []) if previous else [],
                "field_sources": previous.get("field_sources", {})
                if previous
                else {key: source["name"] for key in metadata},
                "updated_at": self.repo.clock(),
                "status": "unknown",
                "artifact": previous.get("artifact") if previous else None,
                "group_key": digest(
                    {"title": title_key(metadata["title"]), "author": title_key(metadata["author"])}
                )[:24],
            }
            return self.repo.put("book", identity, body, db=db)

    def book(self, identity, db=None):
        require(
            isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{24}", identity),
            "invalid_input",
            400,
        )
        row = self.repo._document(db, "book", identity) if db else self.repo.get("book", identity)
        require(row, "not_found", 404)
        return row

    def save_directory(self, book, entries):
        require(isinstance(entries, list) and len(entries) <= 20000, "book_directory_invalid", 502)
        directory = []
        seen = set()
        for entry in entries:
            if not entry.get("name") or not entry.get("url") or entry.get("isVolume"):
                continue
            identity = digest({"source": book["source_id"], "url": entry["url"]})[:24]
            if identity in seen:
                continue
            seen.add(identity)
            directory.append(
                {
                    "chapter_id": identity,
                    "title": plain_text(entry["name"])[:512],
                    "url": entry["url"],
                    "order": len(directory),
                    "variables": entry.get("variables", {}),
                    "is_vip": bool(entry.get("isVip")),
                }
            )
        require(directory, "book_directory_empty", 502)
        with self.repo.transaction() as db:
            current = self.book(book["book_id"], db)
            require(current["source_id"] == book["source_id"], "version_conflict", 409)
            self.repo.put(
                "book_source_directory",
                book["book_id"],
                {
                    "book_id": book["book_id"],
                    "chapters": directory,
                    "checked_at": self.repo.clock(),
                },
                db=db,
            )
            if (current.get("source_selection") or {}).get("mode") == "automatic":
                return current, directory
            self.repo.put(
                "book_directory",
                book["book_id"],
                {
                    "book_id": book["book_id"],
                    "chapters": directory,
                    "checked_at": self.repo.clock(),
                },
                db=db,
            )
            current = self.repo.put(
                "book",
                book["book_id"],
                {
                    **current,
                    "chapter_count": len(directory),
                    "latest_chapter": directory[-1]["title"],
                    "updated_at": self.repo.clock(),
                },
                db=db,
            )
        return current, directory

    def source_directory(self, identity):
        row = self.repo.get("book_source_directory", identity)
        return row["chapters"] if row else self.directory(identity)

    def select_directory(self, identity, expected, chapters, selection):
        with self.repo.transaction() as db:
            book = self.book(identity, db)
            require(
                [c["chapter_id"] for c in self.directory(identity, db)] == expected, "busy", 409
            )
            self.repo.put(
                "book_directory",
                identity,
                {"book_id": identity, "chapters": chapters, "checked_at": self.repo.clock()},
                db=db,
            )
            previous = book.get("source_selection") or {}
            selection = {
                **selection,
                "chapters_by_source": previous.get("chapters_by_source", []),
                "repaired_chapters": previous.get("repaired_chapters", 0),
                "last_repair": previous.get("last_repair"),
            }
            return self.repo.put(
                "book",
                identity,
                {
                    **book,
                    "source_selection": selection,
                    "chapter_count": len(chapters),
                    "latest_chapter": chapters[-1]["title"],
                    "updated_at": self.repo.clock(),
                },
                db=db,
            )

    def logical_book(self, identity, db):
        book = self.book(identity, db)
        alias = self.repo._document(db, "book_source_alias", identity)
        if alias:
            canonical = self.repo._document(db, "book", alias["book_id"])
            if canonical and canonical.get("source_selection"):
                from .matching import same_work

                if same_work(book, canonical):
                    return canonical
        return book

    def record_repair(self, identity, chapter, origin, failure):
        with self.repo.transaction() as db:
            book = self.book(identity, db)
            selection = dict(book.get("source_selection") or {})
            if selection.get("preferred_book_id") == failure["book_id"]:
                selection.update(
                    preferred_book_id=origin["book_id"],
                    preferred_source_name=origin["source_name"],
                    code="book_source_switched",
                )
            selection["last_repair"] = {
                "chapter_title": chapter["title"],
                "from_source": failure["source_name"],
                "source_name": origin["source_name"],
                "code": failure["code"],
                "at": self.repo.clock(),
            }
            selection["candidates"] = [
                {**row, "readable": False, "code": failure["code"]}
                if row["book_id"] == failure["book_id"]
                else row
                for row in selection.get("candidates", [])
            ]
            self.repo.put("book", identity, {**book, "source_selection": selection}, db=db)

    def directory(self, identity, db=None):
        row = (
            self.repo._document(db, "book_directory", identity)
            if db
            else self.repo.get("book_directory", identity)
        )
        return (row or {}).get("chapters", [])

    def chapter(self, book_id, chapter_id):
        return self.repo.get("book_chapter", book_id + ":" + chapter_id)

    def progress(self, book_id, downloaded):
        with self.repo.transaction() as db:
            book = self.book(book_id, db)
            self.repo.put("book", book_id, {**book, "downloaded": downloaded}, db=db)

    def save_chapter(self, book_id, chapter, content):
        content = plain_text(clean_text(content, 2 * 1024 * 1024))
        require(content.strip(), "book_content_empty", 502)
        identity = book_id + ":" + chapter["chapter_id"]
        previous = self.repo.get("book_chapter", identity)
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        if previous and previous["content_hash"] == fingerprint:
            return previous
        return self.repo.put(
            "book_chapter",
            identity,
            {
                **chapter,
                "book_id": book_id,
                "content": content,
                "content_hash": fingerprint,
                "downloaded_at": self.repo.clock(),
            },
        )

    def apply_metadata(self, body, db):
        book = self.book(body["book_id"], db)
        require(
            book.get("metadata_revision", 1) == body.get("expected_revision"),
            "version_conflict",
            409,
        )
        patch = body.get("fields")
        require(isinstance(patch, dict) and set(patch) <= METADATA_FIELDS, "invalid_input", 400)
        fields = {}
        for key, value in patch.items():
            if key == "tags":
                require(
                    isinstance(value, list)
                    and len(value) <= 64
                    and all(isinstance(tag, str) and len(tag) <= 100 for tag in value),
                    "invalid_input",
                    400,
                )
                fields[key] = list(dict.fromkeys(clean_text(tag, 100).strip() for tag in value))
            else:
                require(isinstance(value, str), "invalid_input", 400)
                fields[key] = clean_text(value, 20000 if key == "description" else 2048).strip()
        require("title" not in fields or fields["title"], "invalid_input", 400)
        updated = self.repo.put(
            "book",
            book["book_id"],
            {
                **book,
                "metadata": {**book["metadata"], **fields},
                "metadata_revision": book.get("metadata_revision", 1) + 1,
                "manual_fields": sorted(set(book.get("manual_fields", [])) | set(fields)),
                "field_sources": {
                    **book.get("field_sources", {}),
                    **{key: body.get("source", "manual") for key in fields},
                },
                "updated_at": self.repo.clock(),
            },
            book["revision"],
            db=db,
        )
        if book.get("artifact"):
            self.enqueue(updated, db=db, refresh=False)
        return {"book": public_book(updated)}

    def enqueue(self, book, *, db, refresh=True):
        settings = self.settings(db)
        artifact = book.get("artifact")
        directory = settings["directory"] or (artifact["root"] if artifact else "")
        require(directory, "book_directory_required", 409)
        key = digest({"book": book["book_id"], "directory": directory})
        row = db.execute('SELECT body FROM jobs WHERE dedupe=%s', ("book:" + key,)).fetchone()
        existing = json.loads(row[0]) if row else None
        if existing:
            if existing["state"] in {
                "completed",
                "published",
                "cancelled",
                "failed",
                "unknown",
                "auth_required",
                "waiting_metadata",
            }:
                existing = self.repo.update_job(
                    existing["job_id"],
                    {
                        "state": "queued",
                        "stage": "queued",
                        "code": "queued",
                        "cancel_requested": False,
                        "refresh_directory": refresh,
                        "book_rule_snapshot": None,
                        "attempts": 0,
                    },
                    db=db,
                )
            return existing
        return self.repo.enqueue(
            {
                "media_kind": "book",
                "book_id": book["book_id"],
                "book_key": key,
                "provider": "legado" if book["source_type"] == "remote" else "local",
                "source_id": book["source_id"],
                "title": book["metadata"]["title"],
                "creator": book["metadata"].get("author", ""),
                "target_id": "book-library",
                "destination_directory": directory,
                "refresh_directory": refresh,
                "subscription_id": book["book_id"],
                "account_id": None,
                "quality": {"mode": "book"},
            },
            db=db,
        )

    def subscribe(self, body, db):
        book = self.logical_book(body["book_id"], db)
        previous = self.repo._document(db, "book_subscription", book["book_id"])
        interval = body.get("interval_seconds", self.settings(db)["interval_seconds"])
        require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
        subscription = self.repo.put(
            "book_subscription",
            book["book_id"],
            {
                **(previous or {}),
                "book_id": book["book_id"],
                "state": "active",
                "interval_seconds": interval,
                "next_scan_at": self.repo.clock() + interval,
                "last_scan_at": previous.get("last_scan_at") if previous else None,
                "code": "ready",
            },
            db=db,
        )
        job = self.enqueue(book, db=db)
        return {"subscription": subscription, "job_id": job["job_id"]}
