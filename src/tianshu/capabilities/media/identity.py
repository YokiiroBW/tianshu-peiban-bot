"""Bilibili content identity: BV/CID/MID shapes, stable keys, canonical links and path layout.

Identity is format-validated only. A well-formed ``BV`` says nothing about whether the upload
exists, is public, or is reachable by any account; that question belongs to the connector and the
account layer. Upload keys, part media keys and creator keys come from here so no other module has
to re-derive them, and no title, nickname, part ordinal, favourite id or raw URL ever becomes a
primary key.

Layout names are built from validated identity and fixed literals only. Display text never reaches a
file name, so a hostile title cannot move a package outside its own directory.
"""

from __future__ import annotations

import re

from .types import MEDIA_EXTENSIONS, PROVIDER, SEASON_DIRECTORY, MetadataValidationError

BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}\Z")
POSITIVE_DECIMAL_PATTERN = re.compile(r"[1-9][0-9]*\Z")
DECIMAL_MAX_DIGITS = 20
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{suffix}" for suffix in "123456789"}
    | {f"LPT{suffix}" for suffix in "123456789"}
)


def _reject(code: str, field: str) -> MetadataValidationError:
    return MetadataValidationError(code, field)


def _shape(value: object, code: str, field: str) -> str:
    """Accept an identifier only as a string; return it unchanged.

    A CID or MID is an identity, not a number this layer may re-spell. A JSON integer cannot carry
    a 64-bit identifier through a browser without losing precision, so an integer, boolean or float
    is refused instead of being converted; lower-case ``bv``/``Bv``/``bV`` prefixes are refused too,
    because normalizing the case would invent an identifier the source never published."""

    if isinstance(value, str):
        return value
    raise _reject(code, field)


def is_bvid(value: object) -> bool:
    """True only for an ASCII ``BV`` prefix plus ten ASCII letters or digits, matched exactly.

    Case is content: the prefix must be upper case and the ten body characters are kept as sent."""

    return isinstance(value, str) and bool(BV_PATTERN.match(value))


def is_positive_decimal(value: object, *, max_digits: int = DECIMAL_MAX_DIGITS) -> bool:
    """True for a positive decimal string with no leading zero and bounded length."""

    return (
        isinstance(value, str)
        and len(value) <= max_digits
        and bool(POSITIVE_DECIMAL_PATTERN.match(value))
    )


def require_bvid(value: object, field: str = "bvid") -> str:
    text = _shape(value, "invalid_bvid", field)
    if not is_bvid(text):
        raise _reject("invalid_bvid", field)
    return text


def require_cid(value: object, field: str = "cid") -> str:
    text = _shape(value, "invalid_cid", field)
    if not is_positive_decimal(text):
        raise _reject("invalid_cid", field)
    return text


def require_mid(value: object, field: str = "mid") -> str:
    text = _shape(value, "invalid_mid", field)
    if not is_positive_decimal(text):
        raise _reject("invalid_mid", field)
    return text


def require_positive_index(value: object, maximum: int, field: str = "index") -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise _reject("invalid_index", field)
    return value


def require_relative_path(value: object, field: str = "path") -> str:
    """Accept a relative POSIX path; refuse traversal, absolute and drive-qualified forms."""

    if not isinstance(value, str) or not value:
        raise _reject("invalid_relative_path", field)
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        raise _reject("invalid_relative_path", field)
    if len(value) > 1 and value[1] == ":":
        raise _reject("invalid_relative_path", field)
    if any(not segment or segment in {".", ".."} for segment in value.split("/")):
        raise _reject("invalid_relative_path", field)
    return value


def item_key(bvid: str) -> str:
    """Upload key: ``bilibili:video:<BV>``."""

    return f"{PROVIDER}:video:{require_bvid(bvid)}"


def media_key(bvid: str, cid: object) -> str:
    """Part media key: ``bilibili:video:<BV>:cid:<CID>``."""

    return f"{PROVIDER}:video:{require_bvid(bvid)}:cid:{require_cid(cid)}"


def creator_key(mid: object) -> str:
    """Person key: ``bilibili:creator:<MID>``."""

    return f"{PROVIDER}:creator:{require_mid(mid)}"


def canonical_video_url(bvid: str) -> str:
    """The whole-upload page URL. Only the BV is carried."""

    return f"https://www.bilibili.com/video/{require_bvid(bvid)}/"


def canonical_part_url(bvid: str, index: object) -> str:
    """The part page URL: the upload URL plus the required ``?p=`` ordinal."""

    return f"{canonical_video_url(bvid)}?p={require_positive_index(index, 1000)}"


def canonical_urls(bvid: str, parts: tuple[tuple[str, int], ...]) -> tuple[tuple[str, str], ...]:
    """Stable ``cid -> canonical part URL`` pairs, kept in part order."""

    return tuple((cid, canonical_part_url(bvid, index)) for cid, index in parts)


def require_media_extension(value: object, field: str = "media_extension") -> str:
    if not isinstance(value, str) or value not in MEDIA_EXTENSIONS:
        raise _reject("invalid_media_extension", field)
    return value


def require_image_extension(value: object, field: str = "extension") -> str:
    if not isinstance(value, str) or value not in ("jpg", "png"):
        raise _reject("invalid_image_extension", field)
    return value


def single_directory(bvid: str, cid: object) -> str:
    """``bilibili-<BV>-cid-<CID>`` for a one-part upload."""

    return f"bilibili-{require_bvid(bvid)}-cid-{require_cid(cid)}"


def multipart_directory(bvid: str) -> str:
    """``bilibili-<BV>`` for an upload rendered as a show."""

    return f"bilibili-{require_bvid(bvid)}"


def package_key(layout: str, bvid: str, cid: object | None = None) -> str:
    """Stable package identity, independent of any title or nickname."""

    if layout == "multipart":
        if cid is not None:
            raise _reject("invalid_package_key", "layout")
        return f"{PROVIDER}:package:{require_bvid(bvid)}"
    if layout == "single":
        return f"{PROVIDER}:package:{require_bvid(bvid)}:cid:{require_cid(cid)}"
    raise _reject("invalid_package_key", "layout")


def episode_video_name(episode: int, cid: object, extension: str) -> str:
    """``S01E<episode:02d>-cid-<CID>.<ext>``. Two digits is a floor, never a truncation."""

    number = require_positive_index(episode, 999999, "episode")
    resolved_cid = require_cid(cid)
    suffix = require_media_extension(extension)
    return f"S01E{number:02d}-cid-{resolved_cid}.{suffix}"


def season_path(name: str) -> str:
    """``Season 01/<name>``. The season number is fixed by the card, not by the source list."""

    return f"{SEASON_DIRECTORY}/{require_relative_path(name, 'name')}"


def with_extension(name: str, extension: str, field: str = "extension") -> str:
    """``<name>.<extension>`` for a video path passed into ``season_path``, or a leaf name."""

    if not isinstance(extension, str) or not extension:
        raise _reject("invalid_sidecar_name", field)
    return f"{name}.{extension}"


def nfo_stem(video_path: str, extension: str) -> str:
    """Strip the media extension from a package-relative video path to name its sidecars."""

    resolved = require_media_extension(extension)
    resolved_path = require_relative_path(video_path, "path")
    suffix = f".{resolved}"
    if not resolved_path.endswith(suffix):
        raise _reject("invalid_relative_path", "path")
    stem = resolved_path[: -len(suffix)]
    if not stem:
        raise _reject("invalid_relative_path", "path")
    return stem


def looks_like_windows_device_name(value: str) -> bool:
    """Diagnostic helper: reserved device names exist only where a caller supplies a file name."""

    return value.split(".", 1)[0].strip().upper() in WINDOWS_RESERVED_NAMES


__all__ = [
    "BV_PATTERN",
    "DECIMAL_MAX_DIGITS",
    "WINDOWS_RESERVED_NAMES",
    "canonical_part_url",
    "canonical_urls",
    "canonical_video_url",
    "creator_key",
    "episode_video_name",
    "is_bvid",
    "is_positive_decimal",
    "item_key",
    "looks_like_windows_device_name",
    "media_key",
    "multipart_directory",
    "nfo_stem",
    "package_key",
    "require_bvid",
    "require_cid",
    "require_image_extension",
    "require_media_extension",
    "require_mid",
    "require_positive_index",
    "require_relative_path",
    "season_path",
    "single_directory",
    "with_extension",
]
