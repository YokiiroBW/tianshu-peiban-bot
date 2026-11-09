"""Normalize Fish voice catalog pages without confusing an invalid response with no voices."""

from ..external import ProviderError


def voice_page(answer, *, cursor=None, limit=20, offset_pagination=False):
    if isinstance(answer, dict) and isinstance(answer.get("data"), (dict, list)):
        answer = answer["data"]
    if isinstance(answer, list):
        rows, metadata = answer, {}
    elif isinstance(answer, dict):
        metadata = answer
        rows = next((answer[key] for key in ("items", "voices", "models") if key in answer), None)
    else:
        rows = None
    if not isinstance(rows, list):
        raise ProviderError("speech_voice_catalog_invalid", 502)
    items = []
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("speech_voice_catalog_invalid", 502)
        identifier = row.get("_id") or row.get("id") or row.get("voice_id")
        if not isinstance(identifier, str) or not identifier:
            raise ProviderError("speech_voice_catalog_invalid", 502)
        items.append({
            "id": identifier,
            "name": row.get("title") or row.get("name") or identifier,
            "preview": None,
            "visibility": row.get("visibility"),
            "owner_id": (row.get("author") or {}).get("_id")
            if isinstance(row.get("author"), dict) else row.get("owner_id"),
        })
    next_cursor = metadata.get("next_cursor")
    if next_cursor is None and offset_pagination and rows:
        offset = int(cursor or 0)
        total = metadata.get("total")
        if (isinstance(total, int) and offset + len(rows) < total) or metadata.get("has_more") is True:
            next_cursor = str(offset + limit)
    return {"items": items, "next_cursor": str(next_cursor) if next_cursor is not None else None}
