"""Insert a clear filter into LogQL stream selectors."""

from urllib.parse import parse_qsl, urlencode, urlsplit


def constrain(query, expression):
    """Locate LogQL selectors while respecting quoted strings and templates."""
    result, quote, escaped, selector, comment = [], None, False, False, False
    changed = False
    for character in query:
        result.append(character)
        if comment:
            comment = character != "\n"
        elif quote:
            if escaped:
                escaped = False
            elif character == "\\" and quote != "`":
                escaped = True
            elif character == quote:
                quote = None
        elif character in ('"', "`"):
            quote = character
        elif character == "#":
            comment = True
        elif character == "{":
            selector = True
        elif character == "}" and selector:
            result.append(expression)
            selector = False
            changed = True
    # Scalar-only expressions contain no log data and retain original semantics.
    return "".join(result) if changed else query


def query_path(path, expression):
    parts = urlsplit(path)
    if parts.path not in {
        "/loki/api/v1/query",
        "/loki/api/v1/query_range",
        "/loki/api/v1/detected_fields",
        "/loki/api/v1/detected_labels",
    }:
        return path
    parameters = [
        (key, constrain(value, expression) if key == "query" else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    encoded = urlencode(parameters)
    # The upstream HTTP server has a finite header budget. Never silently omit
    # streams or fall back to an unbounded query for a pathological sparse window.
    if len(encoded) > 512 * 1024:
        raise ValueError("retention_query_over_budget")
    return parts.path + ("?" + encoded if encoded else "")
