"""Offline Chinese search normalization; editions and stored titles stay unchanged."""

import re
import unicodedata
from functools import lru_cache

from opencc import OpenCC

from ...contracts import require


@lru_cache(maxsize=3)
def converter(config):
    return OpenCC(config + ".json")


def chinese_language(value):
    return str(value).lower().replace("_", "-").split("-")[0] == "zh"


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = converter("t2s").convert(text)
    return " ".join(
        "".join(
            " " if unicodedata.category(char)[0] in {"P", "Z"} else char for char in text
        ).split()
    )


def search_names(query, aliases):
    require(isinstance(query, str) and 0 < len(query.strip()) <= 200, "invalid_input", 400)
    require(
        isinstance(aliases, list)
        and len(aliases) <= 2
        and all(isinstance(value, str) and 0 < len(value.strip()) <= 200 for value in aliases),
        "invalid_input",
        400,
    )
    return list(dict.fromkeys(value.strip() for value in [query, *aliases]))


def query_variants(names):
    values = []
    for name in names:
        value = unicodedata.normalize("NFKC", name)
        simple = converter("t2s").convert(value)
        values.extend((value, simple, converter("s2t").convert(simple)))
    return list(dict.fromkeys(values))


def match_score(names, item):
    title = normalize(item.get("title"))
    authors = " ".join(normalize(item.get(key)) for key in ("author", "artist"))
    fields = title + " " + authors
    compact = re.sub(r"\s+", "", fields)
    score = 0
    for name in names:
        query = normalize(name)
        terms = query.split()
        if not terms or not all(term in fields or term in compact for term in terms):
            continue
        if query.replace(" ", "") == title.replace(" ", ""):
            score = max(score, 3)
        elif query in title or query.replace(" ", "") in title.replace(" ", ""):
            score = max(score, 2)
        else:
            score = max(score, 1)
    return score


def source_priority(source, now):
    health = source.get("health") or {}
    recent = [
        r
        for stage, r in health.items()
        if stage != "connectivity" and now - r.get("checked_at", 0) <= 7 * 86400
    ]
    search = health.get("search")
    if search and now - search.get("checked_at", 0) <= 7 * 86400:
        return 0 if search.get("code") == "ready" else 2
    if any(r.get("code") == "ready" for r in recent):
        return 0
    return 2 if recent else 1
