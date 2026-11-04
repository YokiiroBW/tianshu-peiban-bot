"""Read original Episode text independently of later media supplements."""


def needs_original_text(row):
    return not row.get("summary") and ("media_unavailable" in (row.get("extraction_missing") or ())
                                      or row.get("_original_text_required") is True)


async def read_original_text(sources, context, refs, budget=None, *, maximum=24000):
    chunks, size, partial = [], 0, False
    for start in range(0, len(refs), 8):
        if size >= maximum:
            partial = True
            break
        selections = tuple((ref, {"max_characters": maximum - size}) for ref in refs[start:start + 8])
        if budget is None:
            values = await sources.read_many(context, selections)
        else:
            async with budget.timeout():
                values = await sources.read_many(context, selections)
        for value in values:
            content = value.get("content")
            if value.get("representation") != "text" or not isinstance(content, str) or not content:
                continue
            remaining = maximum - size
            chunks.append(content[:remaining])
            size += len(chunks[-1])
            partial |= len(content) > remaining or not value["coverage"].complete
    return "\n".join(chunks), partial
