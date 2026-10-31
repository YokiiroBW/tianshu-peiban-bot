"""Select readable versions and repair a stable book from verified chapter mappings."""

import asyncio
from collections import Counter

from ...contracts import Fault, digest, require
from .matching import ChapterIndex, chapter_label, content_matches, same_work
from .readability import check_content


class SourceSelection:
    def __init__(self, books):
        self.books, self.media, self.repo = books, books.media, books.repo
        self.locks = {}
        self.cooldowns = {}

    async def discover(self, book):
        known = await self.media.local(self.repo.list, "book")
        found = {
            row["book_id"]: row
            for row in known
            if row["source_type"] == "remote" and same_work(book, row)
        }
        sources = await self.media.local(self.repo.list, "book_source")
        enabled = {
            row["source_id"]: row
            for row in sources
            if row["enabled"] and row["capabilities"]["content"]
        }
        represented = {row["source_id"] for row in found.values()}
        missing = [
            row
            for row in enabled.values()
            if row["source_id"] not in represented and row["capabilities"]["search"]
        ]
        query = (book.get("source_identity") or book["metadata"])["title"]

        async def search(source):
            try:
                rows, _ = await self.books.sources.find(source, query)
                for row in rows:
                    if same_work(book, row):
                        found[row["book_id"]] = row
            except Fault:
                pass

        await asyncio.gather(*(search(source) for source in missing[:40]))
        return [
            row
            for row in found.values()
            if row["source_id"] in enabled and row["book_id"] != book["book_id"]
        ][:8]

    async def unit_content(
        self, book, unit, *, pinned=None, cached_only=False, reference=False, refresh=False
    ):
        texts = []
        for part in unit.parts:
            if cached_only:
                cached = await self.media.local(
                    self.books.catalog.chapter, book["book_id"], part["chapter_id"]
                )
                require(cached, "book_content_unavailable", 409)
                check_content(cached["content"])
                require(
                    reference
                    or (cached.get("origin") or {}).get("source_id", book["source_id"])
                    == book["source_id"],
                    "book_content_unavailable",
                    409,
                )
                texts.append(cached["content"])
            else:
                result = await self.books.source_preview(
                    book["book_id"], part["chapter_id"], pinned=pinned, refresh=refresh
                )
                texts.append(result["chapter"]["content"])
        # Repeated halves are a broken rule, not a complete split chapter.
        require(
            len(texts) == 1 or len({digest(text) for text in texts}) == len(texts),
            "book_chapter_mapping_ambiguous",
            409,
        )
        return "\n\n".join(texts)

    async def assess(self, anchor, base, candidate, *, refresh, primary_error=None):
        error = primary_error if candidate["book_id"] == anchor["book_id"] else None
        if candidate["book_id"] != anchor["book_id"]:
            require(same_work(anchor, candidate), "book_identity_mismatch", 409)
            try:
                candidate, native = await self.books.source_detail(
                    candidate["book_id"], refresh=refresh
                )
            except Fault as failure:
                if failure.code == "book_identity_mismatch":
                    raise
                error = failure.code
                native = await self.media.local(
                    self.books.catalog.source_directory, candidate["book_id"]
                )
        else:
            native = await self.media.local(
                self.books.catalog.source_directory, candidate["book_id"]
            )
        index = ChapterIndex(native)
        require(index.units, "book_chapter_mapping_ambiguous", 409)
        anchors = base.anchors(index)
        source = await self.media.local(self.repo.get, "book_source", candidate["source_id"])
        rule_digest = source["digest"] if source else ""
        previous = next(
            (
                row
                for row in (anchor.get("source_selection") or {}).get("candidates", [])
                if row["book_id"] == candidate["book_id"] and row.get("rule_digest") == rule_digest
            ),
            None,
        )
        if previous:
            retained = [
                base.units[tuple(key)]
                for key in previous.get("anchor_keys", [])
                if tuple(key) in base.units and tuple(key) in index.units
            ]
            if len(retained) >= min(3, len(anchors)):
                anchors = retained
        matches = 0
        structural = len(set(base.units) & set(index.units))
        if candidate["book_id"] != anchor["book_id"]:
            require(base.compatible(index), "book_chapter_mapping_ambiguous", 409)
            for unit in anchors:
                reference = None
                try:
                    reference = await self.unit_content(
                        anchor, unit, cached_only=True, reference=True
                    )
                except Fault:
                    if not primary_error:
                        try:
                            native_reference = ChapterIndex(
                                await self.media.local(
                                    self.books.catalog.source_directory, anchor["book_id"]
                                )
                            ).units.get(unit.signature)
                            require(native_reference, "book_content_unavailable", 409)
                            reference = await self.unit_content(anchor, native_reference)
                        except Fault:
                            primary_error = "book_content_unavailable"
                content = await self.unit_content(candidate, index.units[unit.signature])
                if reference:
                    require(content_matches(reference, content), "book_identity_mismatch", 409)
                    matches += 1
            structural_evidence = (
                structural >= 5
                and len({u.key for u in anchors}) == 3
                and structural / max(1, min(len(base.units), len(index.units))) >= 0.85
            )
            require(
                matches >= min(2, len(anchors)) or structural_evidence,
                "book_identity_unverified",
                409,
            )
        latest = max(index.by_number)
        tail = index.by_number[latest]
        readable = False
        if len(tail) == 1:
            try:
                await self.unit_content(candidate, tail[0])
                readable = error is None
            except Fault as failure:
                error = failure.code
        current_source = await self.media.local(
            self.repo.get, "book_source", candidate["source_id"]
        )
        require(current_source and current_source["digest"] == rule_digest, "busy", 409)
        if self.cooldowns.get((anchor["book_id"], candidate["source_id"]), 0) > self.repo.clock():
            readable = False
            error = error or "book_source_unavailable"
        return {
            "book": candidate,
            "index": index,
            "readable": readable,
            "latest": latest,
            "code": error or "ready",
            "matched_chapters": structural,
            "body_anchors": matches,
            "rule_digest": rule_digest,
            "anchor_keys": [unit.signature for unit in anchors],
        }

    @staticmethod
    def origin(candidate, unit):
        book = candidate["book"]
        return {
            "book_id": book["book_id"],
            "source_id": book["source_id"],
            "source_name": book["source_name"],
            "rule_digest": candidate["rule_digest"],
            "chapter_ids": [part["chapter_id"] for part in unit.parts],
        }

    def compose(self, book, directory, base, candidates):
        last = max(base.by_number)
        for candidate in candidates:
            index = candidate["index"]
            boundary = next(
                (index.units[key] for key in base.units if key[0] == last and key in index.units),
                None,
            )
            candidate["extension"] = (
                index.extension(last, boundary.parts[-1]["order"])
                if boundary and candidate["readable"]
                else []
            )
            candidate["progress"] = min(candidate["latest"], last + len(candidate["extension"]))
        preferred = (book.get("source_selection") or {}).get("preferred_book_id", book["book_id"])
        candidates.sort(
            key=lambda c: (
                c["readable"],
                c["progress"],
                c["book"]["book_id"] == preferred,
                c["body_anchors"],
            ),
            reverse=True,
        )
        best = candidates[0] if candidates else None
        maximum = max([last] + [last + len(c["extension"]) for c in candidates])
        additions, ambiguous = {}, 0
        minimum = min(
            [min(base.by_number)]
            + [1 for c in candidates if c["readable"] and 1 in c["index"].by_number]
        )
        for number in range(minimum, maximum + 1):
            if number in base.by_number:
                continue
            choices = [
                c["index"].by_number[number][0]
                for c in candidates
                if len(c["index"].by_number.get(number, [])) == 1
                and (number <= last or any(u.number == number for u in c["extension"]))
            ]
            signatures = {u.signature for u in choices}
            if len(signatures) != 1:
                ambiguous += 1
                if number > last:
                    break
                continue
            unit = choices[0]
            additions[number] = {
                "chapter_id": digest({"book": book["book_id"], "chapter": unit.signature})[:24],
                "title": unit.title,
                "url": unit.parts[0]["url"],
                "variables": {},
                "is_vip": False,
            }
        merged, pending = [], sorted(additions)
        for chapter in directory:
            label = chapter_label(chapter["title"])
            while label and pending and pending[0] < label[0]:
                merged.append(additions[pending.pop(0)])
            merged.append(dict(chapter))
        merged.extend(additions[number] for number in pending)
        require(len(merged) <= 20000, "book_directory_invalid", 502)
        for order, chapter in enumerate(merged):
            chapter["order"] = order
            label = chapter_label(chapter["title"])
            if label:
                chapter["origins"] = [
                    self.origin(c, c["index"].units[label[:2]])
                    for c in candidates
                    if label[:2] in c["index"].units
                ]
        return merged, best, ambiguous

    async def prepare(self, identity, *, refresh=False):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            book = await self.media.local(self.books.catalog.book, identity)
            directory = await self.media.local(self.books.catalog.directory, identity)
            if book["source_type"] != "remote":
                return book, directory
            previous = book.get("source_selection") or {}
            sources = await self.media.local(self.repo.list, "book_source")
            digests = {s["source_id"]: s["digest"] for s in sources if s["enabled"]}
            if (
                not refresh
                and previous.get("mode") == "automatic"
                and previous.get("rule_digests") == digests
            ):
                return book, directory
            primary_error = None
            try:
                book, _ = await self.books.source_detail(identity, refresh=refresh)
            except Fault as error:
                primary_error = error.code
            directory = await self.media.local(self.books.catalog.directory, identity)
            require(directory, primary_error or "book_directory_empty", 409)
            base = ChapterIndex(directory)
            expected = [chapter["chapter_id"] for chapter in directory]
            # Existing volume resets or incomplete multipart chapters keep their
            # original order; they cannot safely become a single numbered book.
            simple = (
                base.units
                and not base.ambiguous
                and all(len(units) == 1 for units in base.by_number.values())
                and [unit.number for unit in base.units.values()]
                == sorted(unit.number for unit in base.units.values())
            )
            normalized = False
            if (
                simple
                and same_work(book, book)
                and not book.get("artifact")
                and not book.get("downloaded")
                and any(len(unit.parts) > 1 for unit in base.units.values())
            ):
                first_parts = {
                    unit.parts[0]["chapter_id"]: unit
                    for unit in base.units.values()
                    if len(unit.parts) > 1
                }
                remaining_parts = {
                    part["chapter_id"] for unit in first_parts.values() for part in unit.parts[1:]
                }
                normalized_directory = []
                for chapter in directory:
                    if chapter["chapter_id"] in remaining_parts:
                        continue
                    unit = first_parts.get(chapter["chapter_id"])
                    normalized_directory.append(
                        {
                            **chapter,
                            **(
                                {
                                    "chapter_id": digest(
                                        {"book": identity, "chapter": unit.signature}
                                    )[:24],
                                    "title": unit.title,
                                }
                                if unit
                                else {}
                            ),
                            "order": len(normalized_directory),
                        }
                    )
                directory = normalized_directory
                base = ChapterIndex(directory)
                normalized = True
            if (
                not same_work(book, book)
                or not simple
                or any(len(unit.parts) > 1 for unit in base.units.values())
            ):
                if primary_error:
                    raise Fault(primary_error, 502)
                return await self.single(book, directory, "book_source_matching_unavailable")
            found = await self.discover(book)
            if not found and not normalized and previous.get("mode") != "automatic":
                if primary_error:
                    raise Fault(primary_error, 502)
                return await self.single(book, directory, "book_source_single")
            candidates, observations = [], []
            for candidate in [book, *found]:
                try:
                    require(
                        not (
                            candidate["book_id"] == identity
                            and primary_error == "book_identity_mismatch"
                        ),
                        "book_identity_mismatch",
                        409,
                    )
                    assessed = await self.assess(
                        book, base, candidate, refresh=refresh, primary_error=primary_error
                    )
                    candidates.append(assessed)
                    observations.append(
                        {
                            "book_id": candidate["book_id"],
                            "source_name": candidate["source_name"],
                            "code": assessed["code"],
                            "readable": assessed["readable"],
                            "latest_number": assessed["latest"],
                            "matched_chapters": assessed["matched_chapters"],
                            "body_anchors": assessed["body_anchors"],
                            "anchor_keys": assessed["anchor_keys"],
                            "rule_digest": assessed["rule_digest"],
                        }
                    )
                except Fault as error:
                    observations.append(
                        {
                            "book_id": candidate["book_id"],
                            "source_name": candidate["source_name"],
                            "code": error.code,
                            "readable": False,
                        }
                    )
            if not candidates and any(row["code"] == "busy" for row in observations):
                raise Fault("busy", 409)
            merged, best, ambiguous = self.compose(book, directory, base, candidates)
            selected = {
                "mode": "automatic",
                "checked_at": self.repo.clock(),
                "preferred_book_id": best["book"]["book_id"] if best else identity,
                "preferred_source_name": best["book"]["source_name"]
                if best
                else book["source_name"],
                "code": "book_chapter_mapping_ambiguous"
                if ambiguous
                else "book_source_ahead"
                if len(merged) > len(directory)
                else "book_source_selected"
                if best and best["readable"]
                else best["code"]
                if best
                else primary_error or "book_identity_unverified",
                "candidates": observations,
                "added_chapters": len(merged) - len(directory),
                "unmatched_chapters": ambiguous,
                "rule_digests": digests,
            }
            book = await self.media.local(
                self.books.catalog.select_directory, identity, expected, merged, selected
            )
            for candidate in candidates:
                donor = candidate["book"]
                donor_subscription = await self.media.local(
                    self.repo.get, "book_subscription", donor["book_id"]
                )
                if (
                    donor["book_id"] != identity
                    and not donor.get("artifact")
                    and (not donor_subscription or donor_subscription.get("state") == "removed")
                ):
                    await self.media.local(
                        self.repo.put, "book_source_alias", donor["book_id"], {"book_id": identity}
                    )
            return book, merged

    async def single(self, book, directory, code):
        selection = {
            "mode": "single",
            "checked_at": self.repo.clock(),
            "code": code,
            "preferred_book_id": book["book_id"],
            "preferred_source_name": book["source_name"],
            "candidates": [],
            "added_chapters": 0,
            "unmatched_chapters": 0,
        }
        book = await self.media.local(
            self.books.catalog.select_directory,
            book["book_id"],
            [chapter["chapter_id"] for chapter in directory],
            directory,
            selection,
        )
        return book, directory

    async def rules(self, book, directory):
        ids = {book["source_id"]} | {
            origin["source_id"] for chapter in directory for origin in chapter.get("origins", [])
        }
        result = {}
        for identity in ids:
            try:
                result[identity] = await self.media.local(self.books.catalog.source, identity)
            except Fault:
                continue
        return result

    async def content(self, book, chapter, *, pinned=None, refresh=False):
        origins = chapter.get("origins", [])
        require(origins, "book_chapter_mapping_ambiguous", 409)
        last = Fault("book_content_unavailable", 502)
        failure = None
        for cached_only in (False,) if refresh else (True, False):
            for origin in origins:
                if (
                    not cached_only
                    and self.cooldowns.get((book["book_id"], origin["source_id"]), 0)
                    > self.repo.clock()
                ):
                    continue
                try:
                    donor = await self.media.local(self.books.catalog.book, origin["book_id"])
                    require(same_work(book, donor), "book_identity_mismatch", 409)
                    native = await self.media.local(
                        self.books.catalog.source_directory, donor["book_id"]
                    )
                    lookup = {part["chapter_id"]: part for part in native}
                    parts = tuple(lookup[key] for key in origin["chapter_ids"] if key in lookup)
                    require(
                        len(parts) == len(origin["chapter_ids"]),
                        "book_chapter_mapping_ambiguous",
                        409,
                    )
                    index = ChapterIndex(parts)
                    signature = chapter_label(chapter["title"])[:2]
                    unit = index.units.get(signature)
                    require(unit, "book_chapter_mapping_ambiguous", 409)
                    snapshot = (
                        pinned.get(origin["source_id"])
                        if isinstance(pinned, dict)
                        else pinned
                        if donor["source_id"] == book["source_id"]
                        else None
                    )
                    if not cached_only:
                        current = await self.media.local(
                            self.repo.get, "book_source", origin["source_id"]
                        )
                        require(current and current["enabled"], "book_source_disabled", 409)
                        require(
                            (snapshot[0] if snapshot else current)["digest"]
                            == origin["rule_digest"],
                            "busy",
                            409,
                        )
                    text = await self.unit_content(
                        donor, unit, pinned=snapshot, cached_only=cached_only, refresh=refresh
                    )
                    provenance = {
                        k: origin[k] for k in ("book_id", "source_id", "source_name", "chapter_ids")
                    }
                    saved = await self.media.local(
                        self.books.catalog.save_chapter,
                        book["book_id"],
                        {**chapter, "origin": provenance},
                        text,
                    )
                    if failure and failure["source_id"] != origin["source_id"]:
                        await self.media.local(
                            self.books.catalog.record_repair,
                            book["book_id"],
                            chapter,
                            origin,
                            failure,
                        )
                    return self.books.preview_result(saved, cached=cached_only)
                except Fault as error:
                    last = error
                    if not cached_only:
                        failure = {**origin, "code": error.code}
                    if not cached_only and error.code in {
                        "book_source_unavailable",
                        "book_source_rate_limited",
                        "book_source_auth_required",
                        "book_rule_timeout",
                    }:
                        self.cooldowns[(book["book_id"], origin["source_id"])] = (
                            self.repo.clock() + 120
                        )
        raise last

    @staticmethod
    def provenance(book, chapters):
        counts = Counter(
            (chapter.get("origin") or {}).get("source_name", book["source_name"])
            for chapter in chapters
        )
        return {
            "chapters_by_source": [
                {"source_name": name, "chapters": count} for name, count in counts.items()
            ],
            "repaired_chapters": sum(
                count for name, count in counts.items() if name != book["source_name"]
            ),
        }
