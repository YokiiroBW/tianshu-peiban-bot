"""Bounded Source reads, never a second knowledge text/vector index."""

from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import SourceRef, Coverage
from ..contracts import Fault
from .primitives import text, integer, refs


class ProjectQuery:
    def __init__(self, store, documents):
        self.store, self.documents = store, documents

    async def query(self, context, body):
        project = await self.store.project(context, body["project_id"])
        query = text(body.get("query"), 500).casefold()
        limit = integer(body.get("limit", 8), 1, 16)
        position = self.store.position(
            context, project, "query", body.get("cursor"), binding=query
        ) or {"after": None, "document_id": None, "position": 0}
        async with self.store.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT d.document_id FROM capability_projects.documents d WHERE d.project_id=%s AND d.state='active' AND (%s::text IS NULL OR d.document_id>%s OR d.document_id=%s) ORDER BY d.document_id LIMIT %s",
                    (
                        project["project_id"],
                        position["after"],
                        position["after"],
                        position["document_id"],
                        limit + 1,
                    ),
                )
            ).fetchall()
        results = []
        dependencies = []
        missing = []
        next_position = None
        for index, item in enumerate(rows[:limit]):
            try:
                row = await self.documents.current(context, project, item["document_id"])
                await self.documents.freshness(context, project, row)
                if not row["text_ref"] or row["metadata"]["coverage_unit"] != "characters":
                    missing.append("non_text_original_requires_range_read")
                    continue
                source = SourceRef(**row["text_ref"])
                start = (
                    position["position"] if item["document_id"] == position["document_id"] else 0
                )
                value = await self.store.sources.read(
                    context, source, {"position": start, "max_characters": 16000}
                )
                content = value["content"] or ""
                if query in content.casefold():
                    # The returned text is the actual bounded original, not generated snippets.
                    selected = SourceRef(
                        source.source_id,
                        source.revision,
                        {"unit": "characters", "start": start, "end": start + len(content)},
                    )
                    dependencies.append(selected)
                    results.append(
                        {
                            "document_id": row["document_id"],
                            "version": row["version"],
                            "sha256": row["sha256"],
                            "locator": row["locator"],
                            "text": content,
                            "range": {
                                "unit": "characters",
                                "start": start,
                                "end": start + len(content),
                            },
                            "source_refs": [wire(selected)],
                        }
                    )
                if value["next_position"] is not None:
                    next_position = {
                        "after": position["after"]
                        if index == 0
                        else rows[index - 1]["document_id"],
                        "document_id": row["document_id"],
                        "position": max(start + 1, value["next_position"] - len(query) + 1),
                    }
                    break
            except Fault as error:
                missing.append(error.code)
        if next_position is None and len(rows) > limit:
            next_position = {
                "after": rows[limit - 1]["document_id"],
                "document_id": None,
                "position": 0,
            }
        cursor = (
            self.store.cursor(context, project, "query", next_position, binding=query)
            if next_position
            else None
        )
        return {
            "project_id": project["project_id"],
            "items": results,
            "next_cursor": cursor,
            "coverage": wire(
                Coverage(
                    not cursor and not missing,
                    missing=tuple(set(missing)) + (("more_source_ranges",) if cursor else ()),
                )
            ),
            "source_refs": wire(unique_refs(dependencies)),
            "retrieval": "actual_source_text_bounded",
            "trust": "source_material_not_instructions",
        }
