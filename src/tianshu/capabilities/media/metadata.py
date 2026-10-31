"""Snapshot validation and normalization for one upload's internal Bilibili projection.

The snapshot is the *future connector's* internal projection, not a Bilibili API response. This
module therefore validates exactly the frozen field table in the card and never guesses a site
protocol: every key is required, unknown keys are refused, and nothing is fetched, timed or written.

Three rules shape the whole module:

* the input mapping is read-only and is never modified in place, and the result shares no mutable
  container with it;
* an absent or blank source value is recorded as a gap (``missing``) instead of being invented, and
  a manual display override is recorded as ``user`` without erasing the original text;
* the original text is provenance and is kept byte for byte, including leading and trailing
  whitespace, while only the display value collapses an all-blank string to empty.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .identity import (
    canonical_urls,
    canonical_video_url,
    item_key,
    media_key,
    require_bvid,
    require_cid,
    require_mid,
)
from .types import (
    CREATOR_ROLES,
    FIELD_SOURCE_NAMES,
    ISSUE_AUTHOR_NAME_MISSING,
    ISSUE_COVER_SOURCE_MISSING,
    ISSUE_CREATOR_MISSING,
    ISSUE_DESCRIPTION_MISSING,
    ISSUE_PART_TITLE_MISSING,
    ISSUE_PUBLISHED_AT_MISSING,
    ISSUE_TITLE_MISSING,
    PROVIDER,
    ROLE_UPLOADER,
    SCHEMA_VERSION,
    SOURCE_MISSING,
    SOURCE_SOURCE,
    SOURCE_USER,
    CreatorEntry,
    Issue,
    MediaMetadata,
    MetadataValidationError,
    PartEntry,
)

SNAPSHOT_FIELDS: tuple[str, ...] = (
    "schema_version",
    "bvid",
    "title",
    "description",
    "published_at",
    "captured_at",
    "creators",
    "parts",
    "tags",
    "cover_available",
)

CREATOR_FIELDS: tuple[str, ...] = ("mid", "name", "role")
PART_FIELDS: tuple[str, ...] = ("cid", "index", "title", "duration_seconds")

TITLE_MAX = 512
DESCRIPTION_MAX = 65536
CREATOR_NAME_MAX = 128
CREATORS_MAX = 100
PARTS_MAX = 1000
INDEX_MAX = 1000
TAG_MAX = 128
TAGS_MAX = 100

TIMESTAMP_PATTERN = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(Z|[+-]\d{2}:\d{2})\Z"
)


def _reject(code: str, field: str) -> MetadataValidationError:
    return MetadataValidationError(code, field)


def _forbidden_characters(text: str) -> bool:
    """True for NUL, XML 1.0 forbidden controls, lone surrogates and non-characters. Ordinary
    newlines, tabs, quotes, emoji, CJK text and XML punctuation are all preserved: a candidate
    keeps the source text and lets the standard library escape the XML."""

    for character in text:
        point = ord(character)
        if point in (0x09, 0x0A, 0x0D):
            continue
        if point < 0x20:
            return True
        if 0xD800 <= point <= 0xDFFF:
            return True
        if point in (0xFFFE, 0xFFFF):
            return True
        if 0xFDD0 <= point <= 0xFDEF:
            return True
        if point & 0xFFFF in (0xFFFE, 0xFFFF):
            return True
    return False


def _optional_text(value: object, *, maximum: int, field: str, code: str) -> tuple[str, bool]:
    """Return ``(text, present)`` for a nullable source string.

    The text is kept exactly as sent, including leading and trailing newlines or spaces: the
    original is provenance and this layer may not destroy it. ``present`` is False for null and for
    an all-blank string, which is a recorded gap rather than a rewritten value."""

    if value is None:
        return "", False
    if not isinstance(value, str):
        raise _reject(code, field)
    if len(value) > maximum:
        raise _reject(code, field)
    if _forbidden_characters(value):
        raise _reject("forbidden_control_character", field)
    return value, bool(value.strip())


def _required_text(value: object, *, maximum: int, field: str, code: str) -> str:
    if not isinstance(value, str):
        raise _reject(code, field)
    if len(value) > maximum:
        raise _reject(code, field)
    if _forbidden_characters(value):
        raise _reject("forbidden_control_character", field)
    return value


def _normalize_timestamp(value: object, field: str) -> str:
    """Parse a second-precision ISO8601 string with an explicit offset and return UTC ``...Z``. A
    local time without an offset is refused: guessing a zone would silently move the premiere
    date. Fractional seconds are refused as well, because the frozen shape is second precision
    and the projection must not hand over a precision the platform then has to round."""

    if not isinstance(value, str):
        raise _reject("invalid_timestamp", field)
    match = TIMESTAMP_PATTERN.match(value)
    if match is None:
        raise _reject("invalid_timestamp", field)
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    try:
        stamp = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError as error:
        raise _reject("invalid_timestamp", field) from error
    zone = match.group(7)
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        offset_hours, offset_minutes = int(zone[1:3]), int(zone[4:6])
        if offset_hours > 23 or offset_minutes > 59:
            raise _reject("invalid_timestamp", field)
        try:
            stamp -= sign * timedelta(hours=offset_hours, minutes=offset_minutes)
        except OverflowError as error:
            # A well-formed local time whose offset falls outside the representable UTC range.
            raise _reject("invalid_timestamp", field) from error
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_mapping(value: object, field: str, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _reject(code, field)
    return value


def _collect_creators(raw: object) -> tuple[CreatorEntry, ...]:
    if not isinstance(raw, list):
        raise _reject("invalid_creators", "creators")
    if len(raw) > CREATORS_MAX:
        raise _reject("invalid_creators", "creators")
    merged: list[CreatorEntry] = []
    positions: dict[str, int] = {}
    for position, entry in enumerate(raw):
        field = f"creators[{position}]"
        record = _require_mapping(entry, field, "invalid_creators")
        if set(record) != set(CREATOR_FIELDS):
            code = "unknown_field" if set(record) - set(CREATOR_FIELDS) else "invalid_creators"
            raise _reject(code, field)
        mid = require_mid(record["mid"], f"{field}.mid")
        role = record["role"]
        if role not in CREATOR_ROLES:
            raise _reject("invalid_creator_role", f"{field}.role")
        name, name_present = _optional_text(
            record["name"],
            maximum=CREATOR_NAME_MAX,
            field=f"{field}.name",
            code="invalid_creator_name",
        )
        existing = positions.get(mid)
        if existing is None:
            positions[mid] = len(merged)
            merged.append(CreatorEntry(mid=mid, name=name, role=role, name_present=name_present))
            continue
        current = merged[existing]
        if current.name_present and name_present and current.name != name:
            raise _reject("conflicting_creator", f"{field}.name")
        name = current.name if current.name_present else name
        name_present = current.name_present or name_present
        role = ROLE_UPLOADER if ROLE_UPLOADER in (current.role, role) else role
        merged[existing] = CreatorEntry(mid=mid, name=name, role=role, name_present=name_present)
    return tuple(merged)


def _collect_parts(raw: object) -> tuple[PartEntry, ...]:
    if not isinstance(raw, list) or not raw:
        raise _reject("invalid_parts", "parts")
    if len(raw) > PARTS_MAX:
        raise _reject("invalid_parts", "parts")
    parts: list[PartEntry] = []
    cids: set[str] = set()
    indexes: set[int] = set()
    for position, entry in enumerate(raw):
        field = f"parts[{position}]"
        record = _require_mapping(entry, field, "invalid_parts")
        if set(record) != set(PART_FIELDS):
            code = "unknown_field" if set(record) - set(PART_FIELDS) else "invalid_parts"
            raise _reject(code, field)
        cid = require_cid(record["cid"], f"{field}.cid")
        if cid in cids:
            raise _reject("duplicate_cid", f"{field}.cid")
        index = record["index"]
        if type(index) is not int or index < 1 or index > INDEX_MAX:
            raise _reject("invalid_index", f"{field}.index")
        if index in indexes:
            raise _reject("duplicate_index", f"{field}.index")
        title, title_present = _optional_text(
            record["title"], maximum=TITLE_MAX, field=f"{field}.title", code="invalid_title"
        )
        duration = record["duration_seconds"]
        if duration is not None and (type(duration) is not int or duration < 0):
            raise _reject("invalid_duration", f"{field}.duration_seconds")
        cids.add(cid)
        indexes.add(index)
        parts.append(
            PartEntry(
                cid=cid,
                index=index,
                title=title,
                duration_seconds=duration,
                title_present=title_present,
            )
        )
    return tuple(parts)


def _collect_tags(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _reject("invalid_tags", "tags")
    if len(raw) > TAGS_MAX:
        raise _reject("invalid_tags", "tags")
    tags: list[str] = []
    seen: set[str] = set()
    for position, entry in enumerate(raw):
        field = f"tags[{position}]"
        if not isinstance(entry, str):
            raise _reject("invalid_tags", field)
        if len(entry) > TAG_MAX:
            raise _reject("invalid_tags", field)
        if _forbidden_characters(entry):
            raise _reject("forbidden_control_character", field)
        trimmed = entry.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        tags.append(trimmed)
    return tuple(tags)


def _collect_overrides(overrides: object) -> dict[str, tuple[str, bool]]:
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise _reject("invalid_overrides", "overrides")
    unknown = set(overrides) - set(FIELD_SOURCE_NAMES)
    if unknown:
        raise _reject("unknown_field", "overrides")
    resolved: dict[str, tuple[str, bool]] = {}
    for name in FIELD_SOURCE_NAMES:
        if name not in overrides:
            continue
        value = overrides[name]
        if not isinstance(value, str):
            raise _reject(f"invalid_{name}", f"overrides.{name}")
        maximum = TITLE_MAX if name == "title" else DESCRIPTION_MAX
        resolved[name] = _optional_text(
            value, maximum=maximum, field=f"overrides.{name}", code=f"invalid_{name}"
        )
    return resolved


def _resolve_field(
    source_value: tuple[str, bool], override: tuple[str, bool] | None
) -> tuple[str, str, str, bool]:
    """Return ``(original, display, source word, present)`` for one text field.

    The original is always the source string exactly as sent, so an override never erases
    provenance. A non-blank display string is used verbatim; an all-blank source or override is a
    real, recorded display gap — it collapses to the empty string and is marked missing rather than
    being silently replaced by the other value."""

    original, original_present = source_value
    if override is None:
        word = SOURCE_SOURCE if original_present else SOURCE_MISSING
        return original, original if original_present else "", word, original_present
    display, display_present = override
    return original, display if display_present else "", SOURCE_USER, display_present


def _collect_issues(
    *,
    title_present: bool,
    description_present: bool,
    creators: tuple[CreatorEntry, ...],
    parts: tuple[PartEntry, ...],
    published_at: str | None,
    cover_available: bool,
) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    if not title_present:
        issues.append(Issue(code=ISSUE_TITLE_MISSING, field="title"))
    if not description_present:
        issues.append(Issue(code=ISSUE_DESCRIPTION_MISSING, field="description"))
    if not creators:
        issues.append(Issue(code=ISSUE_CREATOR_MISSING, field="creators"))
    for position, creator in enumerate(creators):
        if not creator.name_present:
            issues.append(Issue(code=ISSUE_AUTHOR_NAME_MISSING, field=f"creators[{position}].name"))
    if not cover_available:
        issues.append(Issue(code=ISSUE_COVER_SOURCE_MISSING, field="cover_available"))
    if published_at is None:
        issues.append(Issue(code=ISSUE_PUBLISHED_AT_MISSING, field="published_at"))
    for position, part in enumerate(parts):
        if not part.title_present:
            issues.append(Issue(code=ISSUE_PART_TITLE_MISSING, field=f"parts[{position}].title"))
    return tuple(issues)


def normalize_bilibili(
    snapshot: Mapping[str, object], *, overrides: Mapping[str, object] | None = None
) -> MediaMetadata:
    """Validate one internal projection and return the immutable normalized metadata record."""

    source = _require_mapping(snapshot, "snapshot", "invalid_snapshot")
    unknown = set(source) - set(SNAPSHOT_FIELDS)
    if unknown:
        raise _reject("unknown_field", "snapshot")
    missing = [name for name in SNAPSHOT_FIELDS if name not in source]
    if missing:
        raise _reject("missing_field", missing[0])

    schema_version = source["schema_version"]
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise _reject("invalid_schema_version", "schema_version")

    bvid = require_bvid(source["bvid"], "bvid")
    title, title_present = _optional_text(
        source["title"], maximum=TITLE_MAX, field="title", code="invalid_title"
    )
    description, description_present = _optional_text(
        source["description"],
        maximum=DESCRIPTION_MAX,
        field="description",
        code="invalid_description",
    )
    published_raw = source["published_at"]
    published_at = (
        None if published_raw is None else _normalize_timestamp(published_raw, "published_at")
    )
    captured_at = _normalize_timestamp(source["captured_at"], "captured_at")
    creators = _collect_creators(source["creators"])
    parts = _collect_parts(source["parts"])
    tags = _collect_tags(source["tags"])
    cover_available = source["cover_available"]
    if type(cover_available) is not bool:
        raise _reject("invalid_cover_available", "cover_available")

    resolved = _collect_overrides(overrides)
    fields: list[tuple[str, str]] = []
    display_text: dict[str, str] = {}
    present: dict[str, bool] = {}
    originals = {
        "title": (title, title_present),
        "description": (description, description_present),
    }
    original_title = title
    original_description = description
    for name in FIELD_SOURCE_NAMES:
        original, display, source_word, is_present = _resolve_field(
            originals[name], resolved.get(name)
        )
        display_text[name] = display
        present[name] = is_present
        fields.append((name, source_word))
        if name == "title":
            original_title = original
        else:
            original_description = original

    issues = _collect_issues(
        title_present=present["title"],
        description_present=present["description"],
        creators=creators,
        parts=parts,
        published_at=published_at,
        cover_available=cover_available,
    )
    single_cid = parts[0].cid if len(parts) == 1 else None
    urls = canonical_urls(bvid, tuple((part.cid, part.index) for part in parts))
    return MediaMetadata(
        provider=PROVIDER,
        bvid=bvid,
        item_key=item_key(bvid),
        media_key=media_key(bvid, single_cid) if single_cid else item_key(bvid),
        canonical_url=canonical_video_url(bvid),
        canonical_urls=urls,
        original_title=original_title,
        original_description=original_description,
        display_title=display_text["title"],
        display_description=display_text["description"],
        field_source=tuple(fields),
        published_at=published_at,
        captured_at=captured_at,
        creators=creators,
        parts=parts,
        tags=tags,
        cover_available=cover_available,
        issues=issues,
    )


def part_by_cid(parts: tuple[PartEntry, ...], cid: str) -> PartEntry | None:
    """Look up one part by its stable CID."""

    for part in parts:
        if part.cid == cid:
            return part
    return None


__all__ = [
    "CREATORS_MAX",
    "CREATOR_NAME_MAX",
    "CREATOR_FIELDS",
    "DESCRIPTION_MAX",
    "INDEX_MAX",
    "PARTS_MAX",
    "PART_FIELDS",
    "SNAPSHOT_FIELDS",
    "TAGS_MAX",
    "TAG_MAX",
    "TITLE_MAX",
    "TIMESTAMP_PATTERN",
    "normalize_bilibili",
    "part_by_cid",
]
