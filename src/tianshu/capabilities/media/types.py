"""Immutable public value types, named failures and issue vocabulary for media metadata.

TS-090 is a pure domain block: it turns the future connector's internal Bilibili projection into a
normalized metadata record and then into in-memory NFO/source.json candidates. Nothing here reads a
clock, a file, the network or a database, and nothing here can claim that a candidate was validated,
published or imported by a media server.

Every public collection is a tuple or a read-only mapping view, so callers cannot mutate a result in
place and cannot smuggle a mutable ``dict``/``list`` out of the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

PROVIDER = "bilibili"
SCHEMA_VERSION = 1
GENERATOR = "tianshu-media-metadata/1"

ROLE_UPLOADER = "uploader"
ROLE_COLLABORATOR = "collaborator"
CREATOR_ROLES: tuple[str, ...] = (ROLE_UPLOADER, ROLE_COLLABORATOR)

ROLE_LABELS: Mapping[str, str] = MappingProxyType(
    {ROLE_UPLOADER: "UP主", ROLE_COLLABORATOR: "联合创作者"}
)

SOURCE_SOURCE = "source"
SOURCE_USER = "user"
SOURCE_MISSING = "missing"

FIELD_SOURCE_NAMES: tuple[str, ...] = ("title", "description")

LAYOUT_SINGLE = "single"
LAYOUT_MULTIPART = "multipart"
LAYOUTS: tuple[str, ...] = (LAYOUT_SINGLE, LAYOUT_MULTIPART)

MEDIA_EXTENSIONS: tuple[str, ...] = ("mp4", "mkv")
IMAGE_ROLES: tuple[str, ...] = ("poster", "episode_thumb")
IMAGE_EXTENSIONS: tuple[str, ...] = ("jpg", "png")

STATUS_RENDERED_UNVERIFIED = "rendered_unverified"

NFO_MOVIE = "movie"
NFO_TVSHOW = "tvshow"
NFO_EPISODEDETAILS = "episodedetails"
NFO_XML_DECLARATION = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'

SEASON_NUMBER = 1
EPISODE_NUMBER_MAX = 999999
SEASON_DIRECTORY = "Season 01"

ISSUE_TITLE_MISSING = "title_missing"
ISSUE_DESCRIPTION_MISSING = "description_missing"
ISSUE_CREATOR_MISSING = "creator_missing"
ISSUE_AUTHOR_NAME_MISSING = "author_name_missing"
ISSUE_COVER_SOURCE_MISSING = "cover_source_missing"
ISSUE_COVER_LOCAL_MISSING = "cover_local_missing"
ISSUE_PUBLISHED_AT_MISSING = "published_at_missing"
ISSUE_PART_TITLE_MISSING = "part_title_missing"

ISSUE_CODES: tuple[str, ...] = (
    ISSUE_TITLE_MISSING,
    ISSUE_DESCRIPTION_MISSING,
    ISSUE_CREATOR_MISSING,
    ISSUE_AUTHOR_NAME_MISSING,
    ISSUE_COVER_SOURCE_MISSING,
    ISSUE_COVER_LOCAL_MISSING,
    ISSUE_PUBLISHED_AT_MISSING,
    ISSUE_PART_TITLE_MISSING,
)


class MetadataValidationError(ValueError):
    """A snapshot, override or normalized record was refused. ``code`` is a fixed machine word and
    ``field`` names the offending input path. The message never repeats the offending value: a
    snapshot may carry credentials or a signed URL, and an error must not turn that into a log
    line."""

    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"media metadata refused: {code} at {field}")
        self.code = code
        self.field = field

    def __str__(self) -> str:  # pragma: no cover - defensive, mirrors __init__
        return f"media metadata refused: {self.code} at {self.field}"


class RenderRequestError(ValueError):
    """A render request cannot be honoured by the normalized metadata it was given."""

    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"render request refused: {code} at {field}")
        self.code = code
        self.field = field

    def __str__(self) -> str:  # pragma: no cover - defensive, mirrors __init__
        return f"render request refused: {self.code} at {self.field}"


class _Frozen:
    """Refuse attribute assignment after construction for the immutable public value types."""

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")


def frozen_pairs(pairs: Iterable[tuple[str, str]]) -> Mapping[str, str]:
    """Return a read-only mapping view over a copy of ``pairs``."""

    return MappingProxyType(dict(pairs))


def _checked_pairs(items: object, key_name: str) -> tuple[tuple[str, Any], ...]:
    materialized = tuple(items)  # type: ignore[arg-type]
    for entry in materialized:
        if not (isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str)):
            raise TypeError(f"{key_name} entries must be (string, value) pairs")
    return materialized


@dataclass(frozen=True)
class Issue:
    """One explicit gap in a candidate, addressed at the input field that carries it."""

    code: str
    field: str


@dataclass(frozen=True)
class CreatorEntry:
    """A person identity. ``name`` is ``""`` when the projection carried no usable nickname."""

    mid: str
    name: str
    role: str
    name_present: bool


@dataclass(frozen=True)
class PartEntry:
    """One part (分 P) of the upload. Duration is unknown when ``duration_seconds`` is ``None``."""

    cid: str
    index: int
    title: str
    duration_seconds: int | None
    title_present: bool


@dataclass(frozen=True)
class MediaMetadata(_Frozen):
    """Normalized, immutable media metadata. Original and display values never overwrite each
    other."""

    provider: str
    bvid: str
    item_key: str
    media_key: str
    canonical_url: str
    canonical_urls: tuple[tuple[str, str], ...]
    original_title: str
    original_description: str
    display_title: str
    display_description: str
    field_source: tuple[tuple[str, str], ...]
    published_at: str | None
    captured_at: str
    creators: tuple[CreatorEntry, ...]
    parts: tuple[PartEntry, ...]
    tags: tuple[str, ...]
    cover_available: bool
    issues: tuple[Issue, ...]
    schema_version: int = SCHEMA_VERSION

    @property
    def field_sources(self) -> Mapping[str, str]:
        """Read-only ``field name -> source`` view (``source``/``user``/``missing``)."""

        return frozen_pairs(self.field_source)

    @property
    def part_cids(self) -> tuple[str, ...]:
        return tuple(part.cid for part in self.parts)

    @property
    def selected_part(self) -> PartEntry:
        """The first part, for packages that can only carry one."""

        first = self.parts[0]
        if len(self.parts) != 1:
            raise MetadataValidationError("multipart_item", "parts")
        return first

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def named_creator_count(self) -> int:
        return sum(1 for creator in self.creators if creator.name_present)

    def part(self, cid: str) -> PartEntry | None:
        for entry in self.parts:
            if entry.cid == cid:
                return entry
        return None

    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


@dataclass(frozen=True)
class ImageBinding:
    """A caller declaration that an already verified local image file exists. The declaration is
    not evidence: this pure block cannot look at the file, so a binding only decides which
    relative path the candidate mentions."""

    role: str
    cid: str | None
    extension: str


@dataclass(frozen=True)
class RenderRequest(_Frozen):
    """What the application layer asks the renderer to lay out, with persistent episode numbers."""

    layout: str
    selected_cids: tuple[str, ...]
    episode_numbers: tuple[tuple[str, int], ...]
    media_extension: str
    images: tuple[ImageBinding, ...]

    @classmethod
    def build(
        cls,
        *,
        layout: str,
        selected_cids: Iterable[str],
        media_extension: str,
        episode_numbers: Mapping[str, int] | Iterable[tuple[str, int]] = (),
        images: Iterable[ImageBinding] = (),
    ) -> "RenderRequest":
        """Construct a request from ordinary Python collections, refusing malformed shapes."""

        if not isinstance(layout, str):
            raise RenderRequestError("invalid_layout", "layout")
        if isinstance(selected_cids, (str, bytes)):
            raise RenderRequestError("invalid_selected_cids", "selected_cids")
        try:
            selected = tuple(selected_cids)
        except TypeError as error:
            raise RenderRequestError("invalid_selected_cids", "selected_cids") from error
        for cid in selected:
            if not isinstance(cid, str):
                raise RenderRequestError("invalid_selected_cids", "selected_cids")
        if isinstance(episode_numbers, Mapping):
            numbers: Iterable[tuple[str, int]] = tuple(episode_numbers.items())
        elif isinstance(episode_numbers, (str, bytes)):
            raise RenderRequestError("invalid_episode_numbers", "episode_numbers")
        else:
            try:
                numbers = tuple(episode_numbers)
            except TypeError as error:
                raise RenderRequestError("invalid_episode_numbers", "episode_numbers") from error
        if isinstance(images, (str, bytes)):
            raise RenderRequestError("invalid_images", "images")
        try:
            image_items = tuple(images)
        except TypeError as error:
            raise RenderRequestError("invalid_images", "images") from error
        for image in image_items:
            if not isinstance(image, ImageBinding):
                raise RenderRequestError("invalid_images", "images")
        if not isinstance(media_extension, str):
            raise RenderRequestError("invalid_media_extension", "media_extension")
        try:
            pairs = _checked_pairs(numbers, "episode_numbers")
        except TypeError as error:
            raise RenderRequestError("invalid_episode_numbers", "episode_numbers") from error
        for cid, number in pairs:
            if not isinstance(cid, str):
                raise RenderRequestError("invalid_episode_numbers", "episode_numbers")
            if not isinstance(number, int) or isinstance(number, bool):
                raise RenderRequestError("invalid_episode_numbers", "episode_numbers")
        return cls(
            layout=layout,
            selected_cids=selected,
            episode_numbers=pairs,
            media_extension=media_extension,
            images=image_items,
        )

    @property
    def episode_map(self) -> Mapping[str, int]:
        """Read-only ``cid -> persistent episode number`` view."""

        return frozen_pairs(self.episode_numbers)

    def episode_for(self, cid: str) -> int | None:
        for entry_cid, number in self.episode_numbers:
            if entry_cid == cid:
                return number
        return None

    def image_for(self, role: str, cid: str | None) -> ImageBinding | None:
        for image in self.images:
            if image.role == role and image.cid == cid:
                return image
        return None


@dataclass(frozen=True)
class SidecarFile:
    """One generated text sidecar: a relative POSIX path and its exact bytes."""

    path: str
    content: bytes


@dataclass(frozen=True)
class ExpectedMedia:
    """A video file the caller must still produce. Never generated here, only named."""

    path: str
    extension: str


@dataclass(frozen=True)
class ReferencedImage:
    """A relative path the package mentions because the caller declared a verified local image."""

    path: str
    role: str
    cid: str | None


@dataclass(frozen=True)
class SidecarBundle(_Frozen):
    """The complete in-memory candidate package. Only XML and JSON are ever present."""

    package_key: str
    relative_directory: str
    files: tuple[SidecarFile, ...]
    expected_media: tuple[ExpectedMedia, ...]
    referenced_images: tuple[ReferencedImage, ...]
    issues: tuple[Issue, ...]
    status: str = STATUS_RENDERED_UNVERIFIED

    @property
    def media_extension(self) -> str:
        for expected in self.expected_media:
            return expected.extension
        raise MetadataValidationError("missing_expected_media", "expected_media")

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.files)

    def file(self, path: str) -> SidecarFile | None:
        for entry in self.files:
            if entry.path == path:
                return entry
        return None

    def text(self, path: str) -> str:
        entry = self.file(path)
        if entry is None:
            raise MetadataValidationError("unknown_sidecar_path", "path")
        return entry.content.decode("utf-8")


__all__ = [
    "CREATOR_ROLES",
    "EPISODE_NUMBER_MAX",
    "FIELD_SOURCE_NAMES",
    "GENERATOR",
    "IMAGE_EXTENSIONS",
    "IMAGE_ROLES",
    "ISSUE_AUTHOR_NAME_MISSING",
    "ISSUE_CODES",
    "ISSUE_COVER_LOCAL_MISSING",
    "ISSUE_COVER_SOURCE_MISSING",
    "ISSUE_CREATOR_MISSING",
    "ISSUE_DESCRIPTION_MISSING",
    "ISSUE_PART_TITLE_MISSING",
    "ISSUE_PUBLISHED_AT_MISSING",
    "ISSUE_TITLE_MISSING",
    "LAYOUTS",
    "LAYOUT_MULTIPART",
    "LAYOUT_SINGLE",
    "MEDIA_EXTENSIONS",
    "NFO_EPISODEDETAILS",
    "NFO_MOVIE",
    "NFO_TVSHOW",
    "NFO_XML_DECLARATION",
    "PROVIDER",
    "ROLE_COLLABORATOR",
    "ROLE_LABELS",
    "ROLE_UPLOADER",
    "SCHEMA_VERSION",
    "SEASON_DIRECTORY",
    "SEASON_NUMBER",
    "SOURCE_MISSING",
    "SOURCE_SOURCE",
    "SOURCE_USER",
    "STATUS_RENDERED_UNVERIFIED",
    "CreatorEntry",
    "ExpectedMedia",
    "ImageBinding",
    "Issue",
    "MediaMetadata",
    "MetadataValidationError",
    "PartEntry",
    "ReferencedImage",
    "RenderRequest",
    "RenderRequestError",
    "SidecarBundle",
    "SidecarFile",
    "frozen_pairs",
]
