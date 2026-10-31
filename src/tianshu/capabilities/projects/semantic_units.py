"""Complete semantic groups and project evidence; no application imports."""

from .primitives import require
from .primitives import exact, integer


def blocks(text, groups):
    """Explicit reviewed ranges partition all lines. Dependency closure is indivisible.

    No model guesses boundaries: the fallback is the entire source. Citations use
    decoded text lines (HTML: visible-text lines, with original bytes retained separately).
    """
    lines = text.splitlines(keepends=True)
    if groups is None:
        return [{"spans": [[1, len(lines)]], "text": text}]
    require(isinstance(groups, list) and 0 < len(groups) <= 128, "invalid_groups", 400)
    covered, nodes = set(), []
    for index, group in enumerate(groups):
        exact(group, "start end depends_on")
        integer(group["start"], 1, len(lines))
        integer(group["end"], group["start"], len(lines))
        span = set(range(group["start"], group["end"] + 1))
        require(not span & covered, "invalid_groups", 400)
        covered |= span
        require(isinstance(group["depends_on"], list), "invalid_groups", 400)
        for dependency in group["depends_on"]:
            integer(dependency, 0, len(groups) - 1)
        nodes.append({index, *group["depends_on"]})
    require(covered == set(range(1, len(lines) + 1)), "invalid_groups", 400)
    components = []
    for node in nodes:
        merged = set(node)
        for other in components[:]:
            if merged & other:
                merged |= other
                components.remove(other)
        components.append(merged)
    result = []
    for component in components:
        spans = sorted([groups[i]["start"], groups[i]["end"]] for i in component)
        result.append(
            {
                "spans": spans,
                "text": "\n".join("".join(lines[start - 1 : end]) for start, end in spans),
            }
        )
    return result
