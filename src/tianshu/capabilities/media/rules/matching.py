"""Field projection and ordinary-text matching: what a rule actually sees.

The projection is the whole contract between normalized metadata and a rule, and it exists so that
no rule, and no future caller, can invent its own idea of "the title":

* ``title`` and ``description`` read **original** values. A manual display override changes what a
  media server shows; it must not change whether a subscription downloads the upload;
* ``uploader_id`` and ``uploader_name`` only read creators whose role is ``uploader``. A
  collaborator is a person, not the source of the upload, so their nickname cannot satisfy an
  "UP主" rule;
* an all-blank source string is a recorded gap, not an empty value: it projects to *no* values, and
  a field with no values does not match anything — not even ``regex=.*``;
* the per-field byte budget is checked before any unbounded copy is made, and an over-budget field
  is refused instead of truncated: cutting source text would change the truth of a rule.

Length is measured in UTF-8 bytes because that is what the IPC line carries; a 64 KiB CJK title is
therefore refused well before the transport limit, and the refusal stays a named ``input_too_large``.
"""

from __future__ import annotations

from typing import Iterable

from ..types import ROLE_UPLOADER, MediaMetadata
from .types import (
    FIELD_DESCRIPTION,
    FIELD_TAGS,
    FIELD_TITLE,
    FIELD_UPLOADER_ID,
    FIELD_UPLOADER_NAME,
    FIELDS,
    OP_CONTAINS,
    OP_EQUALS,
    OP_NOT_EQUALS,
    OP_PREFIX,
    OP_SUFFIX,
)

FIELD_PROJECTION_MAX_BYTES = 64 * 1024

PLAIN_OPS: tuple[str, ...] = (OP_EQUALS, OP_CONTAINS, OP_PREFIX, OP_SUFFIX, OP_NOT_EQUALS)


def field_values(metadata: MediaMetadata, field: str) -> tuple[str, ...]:
    """Project one field of a normalized record into the values a rule is matched against.

    The order is the source order of the record and is preserved in the result; matching itself is
    order independent ("any value matches"), but a caller reading the projection sees the same
    order the connector produced.
    """

    if field == FIELD_TITLE:
        return (metadata.original_title,) if metadata.original_title.strip() else ()
    if field == FIELD_DESCRIPTION:
        return (metadata.original_description,) if metadata.original_description.strip() else ()
    if field == FIELD_UPLOADER_ID:
        return tuple(creator.mid for creator in metadata.creators if creator.role == ROLE_UPLOADER)
    if field == FIELD_UPLOADER_NAME:
        return tuple(
            creator.name
            for creator in metadata.creators
            if creator.role == ROLE_UPLOADER and creator.name_present
        )
    if field == FIELD_TAGS:
        return metadata.tags
    raise ValueError(f"unknown media rule field: {field}")


def projection_within_budget(values: Iterable[str]) -> bool:
    """True while the running total stays inside the per-field budget.

    The total is accumulated value by value so an over-budget projection is detected before the
    whole projection is materialized into one string.
    """

    total = 0
    for value in values:
        total += len(value.encode("utf-8"))
        if total > FIELD_PROJECTION_MAX_BYTES:
            return False
    return True


def compare(value_text: str, rule_value: str, op: str, *, case_sensitive: bool) -> bool:
    """Match one projected value against one ordinary-text rule value.

    ``case_sensitive=False`` compares Unicode **casefolded** strings on both sides; ``True`` compares
    the strings verbatim. Casefolding is used rather than a lower/upper round trip because it is the
    standard Unicode caseless comparison: ``Straße``, ``STRASSE`` and ``straße`` fold to one form,
    while a rule written with ``İ`` keeps its own semantics instead of acquiring a locale's.
    """

    if not case_sensitive:
        text = value_text.casefold()
        wanted = rule_value.casefold()
    else:
        text = value_text
        wanted = rule_value
    if op == OP_EQUALS:
        return text == wanted
    if op == OP_NOT_EQUALS:
        return text != wanted
    if op == OP_CONTAINS:
        return wanted in text
    if op == OP_PREFIX:
        return text.startswith(wanted)
    if op == OP_SUFFIX:
        return text.endswith(wanted)
    raise ValueError(f"not an ordinary-text operation: {op}")


def any_value_matches(
    values: tuple[str, ...], rule_value: str, op: str, *, case_sensitive: bool
) -> bool:
    """A multi-value field matches as soon as **one** value matches; no rule ever joins values.

    Grouping is deliberately not inferred: a rule cannot require that the same creator satisfies two
    conditions, because such a hidden join would be unpredictable for a two-creator upload. Callers
    that need a joined condition express it as one rule on a single field.
    """

    if op == OP_NOT_EQUALS:
        return bool(values) and not any(
            compare(value, rule_value, OP_EQUALS, case_sensitive=case_sensitive) for value in values
        )
    return any(compare(value, rule_value, op, case_sensitive=case_sensitive) for value in values)


__all__ = [
    "FIELDS",
    "FIELD_PROJECTION_MAX_BYTES",
    "PLAIN_OPS",
    "any_value_matches",
    "compare",
    "field_values",
    "projection_within_budget",
]
