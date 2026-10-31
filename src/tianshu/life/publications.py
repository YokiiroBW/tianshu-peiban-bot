"""Published reader projections contain only explicitly disclosed revision material."""
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage
from tianshu.conversation.codec import source_refs


def reference(row):
    return source_refs((row["disclosure_ref"],)) if row.get("disclosure_ref") else ()


def diary(row):
    return {"id": row["id"], "actor_id": row["actor_id"], "day": row["day"],
            "state": "published", "revision": row["published_revision"],
            "published_revision": row["published_revision"], "source_refs": reference(row)}


def chapter(row):
    metadata = row["publication_metadata"]
    return {"id": row["id"], "work_id": row["work_id"], "position": metadata["position"],
            "title": metadata["title"], "state": "published", "revision": row["published_revision"],
            "published_revision": row["published_revision"], "source_refs": reference(row)}


def version(row):
    return {"revision": row["published_revision"], "content": row["content"],
            "created_at": row["published_at"], "source_refs": reference(row)}


async def page(authority, context, rows, limit, projection, *, cursor_key="id"):
    selected = rows[:limit]
    allowed = await authority.check_many(context, tuple(reference(row) for row in selected))
    items = tuple(projection(row) for row, valid in zip(selected, allowed, strict=True)
                  if valid and row.get("disclosure_ref"))
    more = len(rows)>limit
    return Page(items, str(selected[-1][cursor_key]) if more else None,
                Coverage(not more and len(items)==len(selected), None,
                         ("publication_unavailable",) if len(items)!=len(selected) else ()))


async def readable(authority, context, row):
    if not row or not row.get("disclosure_ref"):
        raise AppError("publication_not_found", status=404)
    await authority.check(context, reference(row))
