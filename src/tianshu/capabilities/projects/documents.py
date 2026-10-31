"""Incremental project imports and current-version original/semantic-unit reading."""

import base64
from dataclasses import replace
import hashlib
import mimetypes
import math
from uuid import NAMESPACE_URL, uuid5
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import Coverage, MediaRef, SourceRef
from ..contracts import Fault, canonical, digest, require
from .file_sources import read_file
from .primitives import text, integer, refs
from .semantic_units import blocks
from . import media_codec


def identity(project_id, kind, locator):
    return str(uuid5(NAMESPACE_URL, canonical([project_id, kind, locator])))


def file_original(project, locator):
    raw, fallback = read_file(project, locator, media_codec.MAX_BYTES)
    mime = mimetypes.guess_type(locator)[0] or fallback
    if mime in {
        "text/x-python",
        "text/javascript",
        "application/javascript",
        "application/sql",
        "text/css",
    }:
        mime = "text/plain"
    if locator.lower().endswith((".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c", ".h", ".cpp")):
        mime = "text/plain"
    return raw, mime


class Documents:
    def __init__(self, store, local, artifacts):
        self.store, self.local, self.artifacts = store, local, artifacts
        self.content = None

    async def current(self, context, project, document_id, *, version=None):
        async with self.store.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT d.*,v.version,v.sha256,v.mime_type,v.source_refs,v.original_ref,v.text_ref,v.metadata,v.units,v.state AS version_state FROM capability_projects.documents d JOIN capability_projects.document_versions v ON v.document_id=d.document_id AND v.version=COALESCE(%s,d.current_version) WHERE d.project_id=%s AND d.document_id=%s",
                    (version, project["project_id"], document_id),
                )
            ).fetchone()
        require(
            row and row["state"] == "active" and row["version_state"] == "active",
            "document_unavailable",
            404,
        )
        await self.store.readable(context, row["source_refs"])
        return row

    async def freshness(self, context, project, row):
        if row["kind"] == "file":
            raw, _ = await self.local(file_original, project, row["locator"])
            actual = hashlib.sha256(raw).hexdigest()
        elif row["kind"] == "url":
            require(row["locator"] in project["allowed_urls"], "project_url_not_registered", 403)
            raw, _, _, _ = await self.content.fetch(row["locator"], maximum=media_codec.MAX_BYTES)
            actual = hashlib.sha256(raw).hexdigest()
        else:
            # Uploaded sources are immutable authoritative snapshots, not live files.
            actual = row["sha256"]
        await self.store.readable(context, row["source_refs"])
        require(
            actual == row["sha256"] and row["version"] == row["current_version"],
            "document_changed",
            409,
        )
        return {
            "state": "current",
            "sha256": actual,
            "basis": "immutable_source" if row["kind"] == "source" else "actual_original_hash",
        }

    async def source_bytes(self, context, source):
        value = await self.store.sources.read(context, source, {"position": 0, "max_characters": 1})
        require(value.get("media"), "document_original_unavailable", 404)
        media = MediaRef(**value["media"])
        require(
            media.size_bytes is not None and media.size_bytes <= media_codec.MAX_BYTES,
            "source_too_large",
            413,
        )
        async with self.store.sources.open_media(context, media) as stream:
            raw = await self.local(stream.read, media_codec.MAX_BYTES + 1)
        require(len(raw) <= media_codec.MAX_BYTES, "source_too_large", 413)
        await self.store.readable(context, (source,))
        return raw, media

    async def acquire(self, action, attempt, project, body):
        kind = body.get("kind", "file")
        locator = text(body.get("locator"), 4096)
        if kind == "file":
            raw, mime = await self.local(file_original, project, locator)
            original = await self.store.sources.capture(
                action.context,
                locator={
                    "kind": "project_file",
                    "project_id": project["project_id"],
                    "path": locator,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                raw=raw,
                mime_type=mime,
            )
        elif kind == "url":
            require(locator in project["allowed_urls"], "project_url_not_registered", 403)
            raw, mime, resolved, headers = await self.content.fetch(
                locator, maximum=media_codec.MAX_BYTES
            )
            original = await self.store.sources.capture(
                action.context,
                locator={
                    "kind": "project_url",
                    "project_id": project["project_id"],
                    "url": locator,
                    "resolved": resolved,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                raw=raw,
                mime_type=mime,
            )
        else:
            require(kind == "source", "invalid_document_kind", 400)
            original = SourceRef(**body["source_ref"])
            raw, media = await self.source_bytes(action.context, original)
            locator = original.source_id + ":" + str(original.revision)
            mime = media.mime_type
        prepared = await self.local(
            media_codec.prepare, raw, mime, filename=body.get("filename", locator)
        )
        if prepared["text"]:
            if mime in {"text/plain", "text/markdown"} and raw.decode("utf-8") == prepared["text"]:
                text_ref = original
            else:
                text_ref = await self.store.sources.capture(
                    action.context,
                    locator={
                        "kind": "project_text",
                        "project_id": project["project_id"],
                        "original_source": wire(original),
                        "support_groups": [[wire(original)]],
                        "representation": "normalized_text",
                    },
                    raw=prepared["text"].encode(),
                    mime_type="text/plain",
                )
        else:
            text_ref = None
        source_refs = unique_refs((original,), (text_ref,) if text_ref else ())
        return {
            "kind": kind,
            "locator": locator,
            "prepared": prepared,
            "original": original,
            "text_ref": text_ref,
            "source_refs": source_refs,
        }

    @staticmethod
    def units(prepared, groups=None):
        unit, total = (
            prepared["provenance"]["coverage_unit"],
            prepared["provenance"]["coverage_total"],
        )
        if prepared["text"] and unit == "characters":
            selected = blocks(prepared["text"], groups)
            lines = prepared["text"].splitlines(keepends=True)
            starts = [0]
            for line in lines:
                starts.append(starts[-1] + len(line))
            return [
                {
                    "unit_id": str(index),
                    "spans": [
                        {"unit": "characters", "start": starts[start - 1], "end": starts[end]}
                        for start, end in item["spans"]
                    ],
                    "sha256": digest([prepared["digest"], item["spans"], item["text"]]),
                }
                for index, item in enumerate(selected)
            ]
        size = 1 if unit == "pages" else 30 if unit == "seconds" else total
        if total is None:
            return [
                {
                    "unit_id": "0",
                    "spans": [{"unit": unit, "start": 0, "end": 30}],
                    "sha256": digest([prepared["digest"], unit, 0, 30]),
                }
            ]
        require(
            0 < total <= (media_codec.MAX_BYTES if unit == "bytes" else 1000000),
            "source_too_large",
            413,
        )
        count = max(1, math.ceil(total / size)) if size else 1
        require(count <= 10000, "too_many_read_units", 413)
        return [
            {
                "unit_id": str(index),
                "spans": [
                    {"unit": unit, "start": index * size, "end": min(total, (index + 1) * size)}
                ],
                "sha256": digest(
                    [prepared["digest"], unit, index * size, min(total, (index + 1) * size)]
                ),
            }
            for index in range(count)
        ]

    async def import_document(self, action, attempt, body=None, *, project=None):
        body = body or dict(action.arguments)
        project = project or await self.store.project(
            action.context, text(body.get("project_id"), 128)
        )
        acquired = await self.acquire(action, attempt, project, body)
        prepared = acquired["prepared"]
        if body.get("expected_hash"):
            require(prepared["digest"] == body["expected_hash"], "directory_file_changed", 409)
        identifier = identity(project["project_id"], acquired["kind"], acquired["locator"])
        units = self.units(prepared, body.get("groups"))
        async with self.store.database.transaction() as conn:
            await self.store.writable(
                conn, action.context, unique_refs(action.source_refs, acquired["source_refs"])
            )
            await self.store.lock_project(conn, action.context, project)
            old = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.documents WHERE document_id=%s FOR UPDATE",
                    (identifier,),
                )
            ).fetchone()
            if old:
                previous = await (
                    await conn.execute(
                        "SELECT * FROM capability_projects.document_versions WHERE document_id=%s AND version=%s",
                        (identifier, old["current_version"]),
                    )
                ).fetchone()
                if previous["action_id"] == action.action_id or (
                    previous["sha256"] == prepared["digest"]
                    and old["state"] == "active"
                    and previous["state"] == "active"
                ):
                    return {
                        "document_id": identifier,
                        "version": old["current_version"],
                        "sha256": previous["sha256"],
                        "source_refs": previous["source_refs"],
                        "state": "unchanged",
                    }
            expected = integer(body.get("expected_version", 0), 0)
            require(
                (old["current_version"] if old else 0) == expected, "document_version_changed", 409
            )
            version = expected + 1
            await conn.execute(
                "INSERT INTO capability_projects.documents VALUES(%s,%s,%s,%s,%s,'active',%s) ON CONFLICT(document_id) DO UPDATE SET current_version=excluded.current_version,state='active',last_action_id=excluded.last_action_id",
                (
                    identifier,
                    project["project_id"],
                    acquired["locator"],
                    acquired["kind"],
                    version,
                    action.action_id,
                ),
            )
            await conn.execute(
                "INSERT INTO capability_projects.document_versions(document_id,version,sha256,mime_type,source_refs,original_ref,text_ref,metadata,units,action_id) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)",
                (
                    identifier,
                    version,
                    prepared["digest"],
                    prepared["media"],
                    canonical(wire(acquired["source_refs"])),
                    canonical(wire(acquired["original"])),
                    canonical(wire(acquired["text_ref"])),
                    canonical(prepared["provenance"]),
                    canonical(units),
                    action.action_id,
                ),
            )
            await self.store.dependency(
                conn, "document", identifier + ":" + str(version), acquired["source_refs"]
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "document_id": identifier,
            "version": version,
            "sha256": prepared["digest"],
            "source_refs": wire(acquired["source_refs"]),
            "state": "imported",
        }

    async def list(self, context, body):
        project = await self.store.project(context, body["project_id"])
        limit = integer(body.get("limit", 32), 1, 100)
        after = self.store.position(context, project, "documents", body.get("cursor"))
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT d.document_id,d.locator,d.kind,d.current_version AS version,d.state,v.sha256,v.source_refs,v.metadata FROM capability_projects.documents d JOIN capability_projects.document_versions v ON v.document_id=d.document_id AND v.version=d.current_version WHERE d.project_id=%s AND (%s::text IS NULL OR d.document_id>%s) ORDER BY d.document_id LIMIT %s",
                    (project["project_id"], after, after, limit + 1),
                )
            ).fetchall()
        allowed = await self.store.sources.check_many(
            context, tuple(refs(row["source_refs"]) for row in rows[:limit])
        )
        items = [
            {**row, "source_validation": "not_checked"}
            for row, ok in zip(rows[:limit], allowed)
            if ok and row["state"] == "active"
        ]
        cursor = (
            self.store.cursor(context, project, "documents", rows[limit - 1]["document_id"])
            if len(rows) > limit
            else None
        )
        return self.store.page(
            items,
            len(rows) > limit,
            cursor,
            missing=("source_unavailable",) if len(items) < min(limit, len(rows)) else (),
        )

    async def evidence(self, context, project, reference):
        row = await self.current(
            context, project, reference["document_id"], version=integer(reference["version"], 1)
        )
        require(row["version"] == row["current_version"], "stale_read_unit", 409)
        unit = next(
            (item for item in row["units"] if item["unit_id"] == reference["unit_id"]), None
        )
        require(unit and unit["sha256"] == reference["sha256"], "stale_read_unit", 409)
        await self.freshness(context, project, row)
        source = SourceRef(
            **(
                row["text_ref"]
                if row["metadata"]["coverage_unit"] == "characters"
                else row["original_ref"]
            )
        )
        selected = tuple(
            SourceRef(source.source_id, source.revision, span) for span in unit["spans"]
        )
        await self.store.readable(context, selected)
        return {"reference": reference, "source_refs": wire(selected), "spans": unit["spans"]}

    async def read(self, context, body):
        project = await self.store.project(context, body["project_id"])
        row = await self.current(context, project, body["document_id"], version=body.get("version"))
        fresh = await self.freshness(context, project, row)
        if body.get("expected_hash"):
            require(body["expected_hash"] == row["sha256"], "document_changed", 409)
        binding = [row["document_id"], row["version"], row["sha256"]]
        position = (
            self.store.position(context, project, "read", body.get("cursor"), binding=binding) or 0
        )
        unit = row["units"][position] if position < len(row["units"]) else None
        require(unit is not None, "read_unit_unavailable", 416)
        reference = {
            "document_id": row["document_id"],
            "version": row["version"],
            "unit_id": unit["unit_id"],
            "sha256": unit["sha256"],
        }
        result = {
            "reference": reference,
            "freshness": fresh,
            "spans": unit["spans"],
            "artifacts": [],
            "text": "",
            "source_refs": [],
        }
        if row["text_ref"] and row["metadata"]["coverage_unit"] == "characters":
            source = SourceRef(**row["text_ref"])
            remaining = integer(body.get("max_characters", 16000), 1, 100000)
            requested_position = body.get("position")
            for span_index, span in enumerate(unit["spans"]):
                if requested_position is not None and requested_position >= span["end"]:
                    continue
                selected = SourceRef(source.source_id, source.revision, span)
                start = max(span["start"], requested_position or span["start"])
                obtained = await self.store.sources.read(
                    context, selected, {"position": start, "max_characters": remaining}
                )
                content = obtained["content"] or ""
                result["text"] += content
                remaining -= len(content)
                result["source_refs"].append(
                    wire(
                        SourceRef(
                            source.source_id,
                            source.revision,
                            {"unit": "characters", "start": start, "end": start + len(content)},
                        )
                    )
                )
                if obtained["next_position"] is not None:
                    result["next_position"] = obtained["next_position"]
                    break
                if remaining == 0 and span_index + 1 < len(unit["spans"]):
                    result["next_position"] = unit["spans"][span_index + 1]["start"]
                    break

        else:
            source = SourceRef(**row["original_ref"])
            raw, _ = await self.source_bytes(context, source)
            span = unit["spans"][0]
            rendered = await self.local(
                media_codec.read,
                {
                    "raw": raw,
                    "media_type": row["mime_type"],
                    "hash": row["sha256"],
                    "text": "",
                    "provenance": canonical(row["metadata"]),
                },
                span,
                min(body.get("budget_bytes", 524288), 1500000),
            )
            result["text"] = rendered["text"]
            result["consumption"] = {
                "coverage": rendered["coverage"],
                "complete": rendered["complete"],
                "missing": rendered["gaps"],
                "analysis_completed": False,
            }
            selected = SourceRef(source.source_id, source.revision, span)
            result["source_refs"] = [wire(selected)]
            for item in rendered["representations"]:
                artifact = await self.artifacts.put(
                    context,
                    base64.b64decode(item["data_base64"]),
                    item["media_type"],
                    source_refs=(selected,),
                    metadata={key: value for key, value in item.items() if key != "data_base64"},
                )
                result["artifacts"].append(wire(artifact))
        next_index = position + 1
        result["next_cursor"] = (
            self.store.cursor(context, project, "read", next_index, binding=binding)
            if next_index < len(row["units"]) and result.get("next_position") is None
            else None
        )
        result["coverage"] = wire(
            Coverage(
                not result["next_cursor"] and result.get("next_position") is None,
                missing=("more_units",)
                if result["next_cursor"]
                else ("more_text",)
                if result.get("next_position") is not None
                else (),
            )
        )
        return result

    async def original(self, context, body):
        project = await self.store.project(context, body["project_id"])
        row = await self.current(context, project, body["document_id"], version=body.get("version"))
        fresh = await self.freshness(context, project, row)
        source = SourceRef(**row["original_ref"])
        original = await self.store.sources.read(
            context, source, {"position": 0, "max_characters": 1}
        )
        return {
            "source_ref": wire(source),
            "media": original.get("media"),
            "freshness": fresh,
            "source_refs": [wire(source)],
        }

    async def remove(self, action, body=None):
        body = body or action.arguments
        project = await self.store.project(action.context, body["project_id"])
        async with self.store.database.transaction() as conn:
            await self.store.writable(conn, action.context, action.source_refs)
            await self.store.lock_project(conn, action.context, project)
            row = await (
                await conn.execute(
                    "SELECT * FROM capability_projects.documents WHERE project_id=%s AND document_id=%s FOR UPDATE",
                    (project["project_id"], body["document_id"]),
                )
            ).fetchone()
            require(
                row and row["current_version"] == body["expected_version"],
                "document_version_changed",
                409,
            )
            await conn.execute(
                "UPDATE capability_projects.documents SET state='removed',last_action_id=%s WHERE document_id=%s",
                (action.action_id, row["document_id"]),
            )
            await conn.execute(
                "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=%s",
                (project["project_id"],),
            )
        return {
            "document_id": row["document_id"],
            "version": row["current_version"],
            "state": "removed",
        }
