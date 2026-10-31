"""Recording identities and the single music-library file index in Media's ledger."""

import json
import re
import unicodedata
import uuid

from ...contracts import digest, require
from .quality import normalized_quality
from .lyrics_storage import schedule as schedule_lyrics

ENRICHED_FIELDS = (
    "title",
    "artists",
    "album",
    "album_id",
    "cover_url",
    "duration_ms",
    "disc",
    "track_number",
)


def normalized(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def artists(track):
    # Some providers serialize a list of credits as one slash-delimited string.
    # Normalize only the comparison key; preserve the supplied display/tag names.
    return sorted(
        {
            normalized(name)
            for credit in track.get("artists", [])
            for name in re.split(r"\s*/\s*|、", credit)
            if normalized(name)
        }
    )


def search_title(track):
    return re.sub(
        r"\.(?:mp3|flac|m4a|ogg|opus|ape|wav)$",
        "",
        str(track.get("title") or "").strip(),
        flags=re.I,
    )


def lookup_key(track):
    title, names = normalized(search_title(track)), artists(track)
    return digest({"title": title, "artists": names}) if title and names else None


def album_identity(track):
    value = unicodedata.normalize("NFKC", str(track.get("album") or ""))
    # Catalogs disagree about whether the advisory belongs in the album title.
    # Keep the advisory separately so explicit and clean editions cannot match.
    match = re.search(r"\s*[\[(](explicit|clean)(?:\s+(?:version|edition))?[\])]\s*$", value, re.I)
    return (
        (normalized(value[: match.start()]), match[1].casefold())
        if match
        else (normalized(value), None)
    )


def recording_mismatch(left, right):
    """Explain a rejected metadata candidate without conflating it with entitlement."""
    if not lookup_key(left) or lookup_key(left) != lookup_key(right):
        return "title_or_artists"
    durations = [value.get("duration_ms") for value in (left, right)]
    if not all(isinstance(d, (int, float)) and d > 0 for d in durations):
        return "missing_duration"
    if abs(durations[0] - durations[1]) > min(2000, min(durations) * 0.008):
        return "duration"
    version_words = r"live|remaster(?:ed)?|remix|acoustic|instrumental|karaoke|现场|翻唱|重制|伴奏|纯音乐|演唱会"
    versions = [
        sorted(
            re.findall(version_words, (t.get("title", "") + " " + t.get("album", "")).casefold())
        )
        for t in (left, right)
    ]
    if versions[0] != versions[1]:
        return "version"
    left_album, left_edition = album_identity(left)
    right_album, right_edition = album_identity(right)
    if left_edition and right_edition and left_edition != right_edition:
        return "edition"
    a, b = normalized(left.get("isrc")), normalized(right.get("isrc"))
    if a and b:
        return None if a == b else "isrc"
    for key in ("disc", "track_number"):
        a, b = str(left.get(key) or "").lstrip("0"), str(right.get(key) or "").lstrip("0")
        if a and b and a != b:
            return "disc_or_track"
    return None if left_album and left_album == right_album else "album"


def same_recording(left, right):
    """Only a unique, corroborated metadata match is eligible before hearing the audio."""
    return recording_mismatch(left, right) is None


def source_key(provider, identity, private_scope=""):
    return digest({"provider": provider, "id": identity, "private_scope": private_scope})


def _resolve(identity, document):
    seen = set()
    while identity:
        require(identity not in seen and len(seen) < 32, "music_identity_conflict", 409)
        seen.add(identity)
        row = document(identity)
        require(row is not None, "music_recording_missing", 409)
        if not row.get("merged_into"):
            return identity
        identity = row["merged_into"]
    return None


def _file(artifact):
    return {**artifact, "quality": normalized_quality(artifact["quality"])} if artifact else None


class CatalogReads:
    """A batch projection used only within one transaction's catalogue read phase.

    No state survives the operation, and callers do not use it across identity/file mutations.
    Alias traversal has the same missing/cycle/depth checks as individual catalogue reads.
    """

    def __init__(self, catalog, db, *, recordings=(), sources=(), with_files=True):
        repo = catalog.repo
        self.sources = repo._documents(db, "music_source", sources)
        roots = set(recordings) | {row["recording_id"] for row in self.sources.values()}
        documents, frontier = {}, roots
        for _ in range(32):
            if not frontier:
                break
            rows = repo._documents(db, "music_recording", frontier)
            documents.update((identity, rows.get(identity)) for identity in frontier)
            frontier = {
                row["merged_into"]
                for row in rows.values()
                if row.get("merged_into") and row["merged_into"] not in documents
            }
        self.resolved = {identity: _resolve(identity, documents.get) for identity in roots}
        canonical = set(self.resolved.values())
        self.resolved.update((identity, identity) for identity in canonical)
        self.files = {
            identity: _file(row)
            for identity, row in (
                repo._documents(db, "music_library_file", canonical) if with_files else {}
            ).items()
        }

    def resolve(self, identity):
        return self.resolved[identity]

    def source(self, key):
        row = self.sources.get(key)
        return {**row, "recording_id": self.resolve(row["recording_id"])} if row else None

    def file(self, identity):
        return self.files.get(self.resolve(identity))


class Catalog:
    def __init__(self, repo):
        self.repo = repo

    def root_id(self, db):
        current = self.repo._document(db, "music_catalog", "library")
        if current is None:
            current = self.repo.put(
                "music_catalog", "library", {"library_id": str(uuid.uuid4())}, db=db
            )
        return current["library_id"]

    def resolve(self, identity, db):
        return _resolve(identity, lambda key: self.repo._document(db, "music_recording", key))

    def reads(self, db, *, recordings=(), sources=(), with_files=True):
        return CatalogReads(self, db, recordings=recordings, sources=sources, with_files=with_files)

    def register(self, provider, track, db, *, private_scope=""):
        self.root_id(db)
        key = source_key(provider, track["source_id"], private_scope)
        existing = self.repo._document(db, "music_source", key)
        previous = (existing or {}).get("metadata", {})
        if previous.get("metadata_enriched") and not track.get("metadata_enriched"):
            # Cloud playlists keep returning their original sparse filenames.
            # A later snapshot must not erase metadata confirmed for this same hash.
            track = {
                **track,
                **{k: previous[k] for k in ENRICHED_FIELDS if previous.get(k)},
                "provider_data": {
                    **previous.get("provider_data", {}),
                    **track.get("provider_data", {}),
                },
                "metadata_enriched": True,
            }
        match = lookup_key(track)
        indexed = self.repo._document(db, "music_match_index", match) if match else None
        candidates, method = [], "source"
        if existing:
            identity = self.resolve(existing["recording_id"], db)
            previous_identity = identity
            method = existing.get("match_method", "source")
            if method != "user" and not self.file(identity, db=db):
                targets = {self.resolve(i, db) for i in (indexed or {}).get("recordings", [])}
                exact = [
                    i
                    for i in targets
                    if i != identity
                    and same_recording(
                        track, self.repo._document(db, "music_recording", i)["metadata"]
                    )
                ]
                # Reconsider old unmatched sources after normalization/enrichment,
                # without merging away an archive or an explicit user binding.
                if len(exact) == 1:
                    identity = self.merge(identity, exact[0], db)
                    method = "metadata_strict"
            recording = self.repo._document(db, "music_recording", identity)
            if (
                track.get("metadata_enriched")
                and identity == previous_identity
                and recording["metadata"].get("source_id") == track["source_id"]
            ):
                self.repo.put("music_recording", identity, {**recording, "metadata": track}, db=db)
        else:
            candidates = sorted(
                {self.resolve(i, db) for i in (indexed or {}).get("recordings", [])}
            )
            exact = [
                i
                for i in candidates
                if same_recording(track, self.repo._document(db, "music_recording", i)["metadata"])
            ]
            if len(exact) == 1:
                identity, method = exact[0], "metadata_strict"
            else:
                identity, method = str(uuid.uuid4()), "new"
                self.repo.put(
                    "music_recording",
                    identity,
                    {"recording_id": identity, "metadata": track, "created_at": self.repo.clock()},
                    db=db,
                )
        # Re-registering an existing source also adds its current normalized lookup key.
        # Old filename-shaped metadata keeps the same recording and explicit bindings.
        if match and identity not in (indexed or {}).get("recordings", []):
            self.repo.put(
                "music_match_index",
                match,
                {"recordings": sorted({*(indexed or {}).get("recordings", []), identity})},
                db=db,
            )
        self.repo.put(
            "music_source",
            key,
            {
                "source_key": key,
                "provider": provider,
                "source_id": track["source_id"],
                "private_scope": private_scope,
                "recording_id": identity,
                "match_method": method,
                "metadata": track,
                "candidates": [i for i in candidates if i != identity],
            },
            db=db,
        )
        index = self.repo._document(db, "music_source_index", identity) or {"keys": []}
        if key not in index["keys"]:
            self.repo.put("music_source_index", identity, {"keys": [*index["keys"], key]}, db=db)
        return {"recording_id": identity, "source_key": key, "match_method": method}

    def recording(self, identity):
        with self.repo.transaction() as db:
            return self.repo._document(db, "music_recording", self.resolve(identity, db))

    def sources(self, identity, provider):
        with self.repo.transaction() as db:
            target = self.resolve(identity, db)
            index = self.repo._document(db, "music_source_index", target) or {"keys": []}
            reads = self.reads(db, sources=index["keys"], with_files=False)
            rows = [reads.source(key) for key in index["keys"]]
            return [
                row["metadata"]
                for row in rows
                if row and row["provider"] == provider and row["recording_id"] == target
            ]

    def file(self, identity, *, db=None):
        if db is None:
            with self.repo.transaction() as transaction:
                return self.file(identity, db=transaction)
        artifact = self.repo._document(db, "music_library_file", self.resolve(identity, db))
        return _file(artifact)

    def content_file(self, content_id, directory):
        with self.repo.transaction() as db:
            index = self.repo._document(db, "music_content_index", content_id)
            if index:
                artifact = self.file(index["recording_id"], db=db)
                if (
                    artifact
                    and artifact["directory"] == directory
                    and artifact.get("content_id") == content_id
                ):
                    return artifact
        return None

    def store_file(self, identity, artifact, db):
        identity = self.resolve(identity, db)
        old = self.file(identity, db=db)
        result = self.repo.put(
            "music_library_file",
            identity,
            {
                **artifact,
                "recording_id": identity,
                "quality": normalized_quality(artifact["quality"]),
            },
            db=db,
        )
        if artifact.get("content_id"):
            self.repo.put(
                "music_content_index", artifact["content_id"], {"recording_id": identity}, db=db
            )
        schedule_lyrics(self.repo, identity, artifact, db)
        if (
            old
            and old.get("managed")
            and old["directory"] == artifact["directory"]
            and old["files"] != artifact["files"]
        ):
            self.repo.put(
                "music_retired_file",
                digest({"recording": old["recording_id"], "files": old["files"]}),
                {**old, "replacement_recording_id": identity, "state": "pending"},
                db=db,
            )
        return result

    def merge(self, source, target, db):
        source, target = self.resolve(source, db), self.resolve(target, db)
        if source != target:
            left = self.repo._document(db, "music_source_index", source) or {"keys": []}
            right = self.repo._document(db, "music_source_index", target) or {"keys": []}
            self.repo.put(
                "music_source_index",
                target,
                {"keys": sorted(set(left["keys"] + right["keys"]))},
                db=db,
            )
            row = self.repo._document(db, "music_recording", source)
            self.repo.put("music_recording", source, {**row, "merged_into": target}, db=db)
            # Queue ownership must use the same canonical identity as files.
            # Preserve leases/generations, so an in-flight owner can still settle
            # its original job while another worker sees the merged identity.
            db.execute(
                "UPDATE jobs SET body=jsonb_set(body::jsonb,'{recording_id}',to_jsonb(%s::text))::text WHERE (body::jsonb #>> '{media_kind}')='audio' AND (body::jsonb #>> '{recording_id}')=%s",
                (target, source),
            )
        return target

    def source(self, key, *, db=None):
        if db is None:
            with self.repo.transaction() as transaction:
                return self.source(key, db=transaction)
        row = self.repo._document(db, "music_source", key)
        if row:
            return {**row, "recording_id": self.resolve(row["recording_id"], db)}
        return None

    def link(self, body, db):
        source = self.repo._document(db, "music_source", body["source_key"])
        require(source is not None, "not_found", 404)
        target = self.resolve(body["recording_id"], db)
        result = self.repo.put(
            "music_source",
            source["source_key"],
            {**source, "recording_id": target, "match_method": "user", "candidates": []},
            body["expected_revision"],
            db=db,
        )
        index = self.repo._document(db, "music_source_index", target) or {"keys": []}
        self.repo.put(
            "music_source_index",
            target,
            {"keys": sorted({*index["keys"], source["source_key"]})},
            db=db,
        )
        return {"source": result}

    def view(self, offset=0, limit=100):
        require(type(offset) is int and offset >= 0, "invalid_input", 400)
        with self.repo.transaction() as db:
            rows = db.execute(
                "SELECT body FROM documents WHERE kind='music_recording' AND (body::jsonb #>> '{merged_into}') IS NULL ORDER BY id LIMIT %s OFFSET %s",
                (limit + 1, offset),
            ).fetchall()
            page = [json.loads(raw[0]) for raw in rows[:limit]]
            reads = self.reads(db, recordings=[row["recording_id"] for row in page])
            items = []
            for row in page:
                artifact = reads.file(row["recording_id"])
                items.append(
                    {**row, "file": artifact if artifact and not artifact.get("missing") else None}
                )
            counts = dict(
                db.execute(
                    "SELECT kind,count(*) FROM documents WHERE kind IN ('music_recording','music_library_file','music_source','music_file_observation') GROUP BY kind"
                )
            )
            counts["music_recording"] = db.execute(
                "SELECT count(*) FROM documents WHERE kind='music_recording' AND (body::jsonb #>> '{merged_into}') IS NULL"
            ).fetchone()[0]
            counts["music_library_file"] = db.execute(
                "SELECT count(*) FROM documents f JOIN documents r ON r.id=f.id AND r.kind='music_recording' WHERE f.kind='music_library_file' AND (r.body::jsonb #>> '{merged_into}') IS NULL AND NOT coalesce((f.body::jsonb #>> '{missing}')::boolean,false)"
            ).fetchone()[0]
            return {"items": items, "offset": offset, "more": len(rows) > limit, "counts": counts}
