"""Walk Reader pagination in order, retaining each page's URL and source headers."""

from collections import deque
from urllib.parse import urldefrag, urlsplit

from .engine_http import scoped_headers


def page_key(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urldefrag(url)[0]


def read_pages(first, content, evaluator, next_rule, *, headers="", stop_urls=()):
    from LegadoParser2.RuleEval import getStrings
    from LegadoParser2.RuleUrl.Url import getContent, parseUrl

    stops = {key for value in stop_urls if (key := page_key(value))}
    scheduled = {page_key(first["url"]), page_key(first["finalurl"])}
    visited = set()
    pending = deque([(first, content)])
    while pending:
        page, body = pending.popleft()
        if body is None:
            body, _ = getContent(page)
        current = page_key(page["finalurl"])
        if current in visited or current in stops:
            continue
        visited.add(current)
        scheduled.add(current)
        evaluator.set("baseUrl", page["finalurl"])
        yield page, body
        # Directory parsing adds chapter links as it consumes each page.
        stops.update(key for value in stop_urls if (key := page_key(value)))
        if not next_rule:
            continue
        links = getStrings(body, next_rule, evaluator, rawContent=body)
        for link in links:
            if not isinstance(link, str) or not link.strip() or link.lstrip().startswith("#"):
                continue
            target = parseUrl(link, evaluator, page["finalurl"], headers=headers)
            target["headers"] = scoped_headers(
                target["headers"],
                evaluator.bS.get("bookSourceUrl") or first["finalurl"],
                target["url"],
            )
            key = page_key(target["url"])
            # Disabled pagination controls commonly use javascript:void(0).
            if key is None or key in scheduled or key in stops:
                continue
            target["url"] = key
            scheduled.add(key)
            pending.append((target, None))
