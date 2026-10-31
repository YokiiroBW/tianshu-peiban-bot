"""In-memory NFO and source.json candidates for one layout decision.

The renderer is the last pure step before anything touches a disk. It receives already normalized
metadata plus a request that names the layout, the selected parts, the persistent episode numbers
and any *already verified* local images, and it returns an immutable bundle of relative paths and
UTF-8 bytes. It never assigns an episode number, never reads a clock, never opens a file and never
claims that its output was validated, published or imported.

Only XML and JSON appear in ``files``. Video and images are named in ``expected_media`` and
``referenced_images``; a missing local image becomes an issue instead of being faked with an empty
file, an upstream URL or a fabricated thumbnail reference.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from xml.etree import ElementTree

from .creators import director_name

from .identity import (
    creator_key,
    episode_video_name,
    media_key,
    multipart_directory,
    nfo_stem,
    package_key,
    require_image_extension,
    require_media_extension,
    require_relative_path,
    season_path,
    single_directory,
    with_extension,
)
from .types import (
    EPISODE_NUMBER_MAX,
    GENERATOR,
    IMAGE_ROLES,
    ISSUE_COVER_LOCAL_MISSING,
    LAYOUT_MULTIPART,
    LAYOUT_SINGLE,
    LAYOUTS,
    NFO_EPISODEDETAILS,
    NFO_MOVIE,
    NFO_TVSHOW,
    NFO_XML_DECLARATION,
    PROVIDER,
    ROLE_LABELS,
    SCHEMA_VERSION,
    SEASON_NUMBER,
    STATUS_RENDERED_UNVERIFIED,
    ExpectedMedia,
    ImageBinding,
    Issue,
    MediaMetadata,
    MetadataValidationError,
    ReferencedImage,
    RenderRequest,
    RenderRequestError,
    SidecarBundle,
    SidecarFile,
)

POSTER_STEM = "poster"
VIDEO_STEM = "video"
MOVIE_NFO_NAME = "movie.nfo"
TVSHOW_NFO_NAME = "tvshow.nfo"
SOURCE_JSON_NAME = "source.json"
EPISODE_THUMB_SUFFIX = "-thumb"
ROLE_LANGUAGE = "zh-CN"


def _reject(code: str, field: str) -> RenderRequestError:
    return RenderRequestError(code, field)


def _checked_media_extension(value: object) -> str:
    """Reuse the identity rule, but report the failure as a render problem with the same code."""

    try:
        return require_media_extension(value)
    except MetadataValidationError as error:
        raise _reject(error.code, error.field) from error


def _checked_image_extension(value: object, field: str) -> str:
    try:
        return require_image_extension(value, field)
    except MetadataValidationError as error:
        raise _reject(error.code, error.field) from error


def _validate_request(metadata: MediaMetadata, request: object) -> RenderRequest:
    """Validate a render request defensively, including one assembled by hand rather than
    ``build``."""

    if not isinstance(request, RenderRequest):
        raise _reject("invalid_request", "request")
    layout = request.layout
    if layout not in LAYOUTS:
        raise _reject("invalid_layout", "layout")
    extension = _checked_media_extension(request.media_extension)
    if layout == LAYOUT_SINGLE:
        if metadata.part_count != 1:
            raise _reject("single_layout_requires_one_part", "layout")
    elif metadata.part_count < 1:
        raise _reject("multipart_layout_requires_parts", "layout")

    selected = request.selected_cids
    if not isinstance(selected, tuple):
        raise _reject("invalid_selected_cids", "selected_cids")
    if not selected:
        raise _reject("empty_selection", "selected_cids")
    seen: set[str] = set()
    for position, cid in enumerate(selected):
        field = f"selected_cids[{position}]"
        if not isinstance(cid, str):
            raise _reject("invalid_selected_cids", field)
        if cid in seen:
            raise _reject("duplicate_selected_cid", field)
        if metadata.part(cid) is None:
            raise _reject("unknown_cid", field)
        seen.add(cid)

    numbers = request.episode_numbers
    if not isinstance(numbers, tuple):
        raise _reject("invalid_episode_numbers", "episode_numbers")
    assigned: dict[str, int] = {}
    used: set[int] = set()
    for position, pair in enumerate(numbers):
        field = f"episode_numbers[{position}]"
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise _reject("invalid_episode_numbers", field)
        cid, number = pair
        if not isinstance(cid, str) or cid not in seen:
            raise _reject("episode_cid_not_selected", field)
        if type(number) is not int or number < 1 or number > EPISODE_NUMBER_MAX:
            raise _reject("invalid_episode_number", field)
        if cid in assigned:
            raise _reject("duplicate_episode_cid", field)
        if number in used:
            raise _reject("duplicate_episode_number", field)
        assigned[cid] = number
        used.add(number)
    if layout == LAYOUT_MULTIPART:
        for position, cid in enumerate(selected):
            if cid not in assigned:
                raise _reject("missing_episode_number", f"episode_numbers[{position}]")
    elif assigned:
        raise _reject("unexpected_episode_numbers", "episode_numbers")

    images = request.images
    if not isinstance(images, tuple):
        raise _reject("invalid_images", "images")
    bound: set[tuple[str, str | None]] = set()
    for position, image in enumerate(images):
        field = f"images[{position}]"
        if not isinstance(image, ImageBinding):
            raise _reject("invalid_images", field)
        if not isinstance(image.role, str) or image.role not in IMAGE_ROLES:
            raise _reject("invalid_image_role", f"{field}.role")
        _checked_image_extension(image.extension, f"{field}.extension")
        if image.cid is not None and not isinstance(image.cid, str):
            # Checked before any set membership or hashing: a list, dict or integer cid must be a
            # named refusal, not a TypeError escaping from ``in``.
            code = "invalid_poster_cid" if image.role == "poster" else "invalid_thumb_cid"
            raise _reject(code, f"{field}.cid")
        if image.role == "poster":
            if image.cid is not None:
                raise _reject("invalid_poster_cid", f"{field}.cid")
        elif image.cid is None or image.cid not in seen:
            raise _reject("invalid_thumb_cid", f"{field}.cid")
        binding = (image.role, image.cid)
        if binding in bound:
            raise _reject("duplicate_image_binding", field)
        bound.add(binding)

    if layout == LAYOUT_SINGLE:
        if selected != (metadata.parts[0].cid,):
            raise _reject("single_layout_cid_mismatch", "selected_cids")
        if any(image.role != "poster" for image in images):
            raise _reject("single_layout_rejects_thumb", "images")
    return RenderRequest(
        layout=layout,
        selected_cids=selected,
        episode_numbers=numbers,
        media_extension=extension,
        images=images,
    )


def _element(root: ElementTree.Element, tag: str, text: object, forced: bool = False) -> None:
    """Append ``tag`` with ``text`` when it has text, or when the layout requires the element."""

    if text is None:
        return
    if isinstance(text, str) and not text and not forced:
        return
    ElementTree.SubElement(root, tag).text = text if isinstance(text, str) else str(text)


def _add_creators(root: ElementTree.Element, metadata: MediaMetadata) -> None:
    """Named uploaders are MID-qualified directors; collaborators remain actors.

    A person without a nickname stays in the normalized record and in ``source.json`` with an
    ``author_name_missing`` issue, but produces no ``actor`` node here: an empty actor would be a
    fabricated person candidate that a media server could merge into someone else. Named authors
    are always all written."""

    order = 0
    for creator in metadata.creators:
        if not creator.name_present:
            continue
        if creator.role == "uploader":
            ElementTree.SubElement(root, "director").text = director_name(creator.name, creator.mid)
            continue
        actor = ElementTree.SubElement(root, "actor")
        ElementTree.SubElement(actor, "name").text = creator.name
        role = ElementTree.SubElement(actor, "role")
        role.set("infoset", "true")
        role.set("name", creator.role)
        role.set("language", ROLE_LANGUAGE)
        role.text = ROLE_LABELS[creator.role]
        ElementTree.SubElement(actor, "order").text = str(order)
        order += 1


def _add_unique_id(root: ElementTree.Element, key: str) -> None:
    node = ElementTree.SubElement(root, "uniqueid")
    node.set("type", PROVIDER)
    node.set("default", "true")
    node.text = key


def _premiere_date(published_at: str | None) -> str | None:
    """The date profile of ``premiered``/``aired``: ``YYYY-MM-DD`` in UTC, or nothing.

    The stored value is already normalized UTC second precision, so the date is its first ten
    characters. ``source.json`` keeps the full timestamp; the XbmcMetadata reader this candidate
    targets parses a date by default, so writing a full datetime here would be a profile this layer
    cannot claim."""

    return published_at[:10] if published_at else None


def _year(premiere_date: str | None) -> str | None:
    return premiere_date[:4] if premiere_date else None


def _runtime_minutes(duration_seconds: int | None) -> int | None:
    """Whole minutes from the source seconds. An unknown duration stays unknown, never zero."""

    return None if duration_seconds is None else duration_seconds // 60


def _serialize(root: ElementTree.Element) -> bytes:
    ElementTree.indent(root, space="  ")
    body = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
    return f"{NFO_XML_DECLARATION}\n{body}\n".encode("utf-8")


def _add_poster(root: ElementTree.Element, poster: str | None) -> None:
    """The poster profile the XbmcMetadata reader looks for: ``<thumb aspect="poster">``.

    A leaf file name is written, so the reference resolves inside the directory that holds the NFO.
    No top-level ``poster`` element is invented, and no binding means no image element at all."""

    if poster is None:
        return
    node = ElementTree.SubElement(root, "thumb")
    node.set("aspect", "poster")
    node.text = poster


def _add_common_header(root: ElementTree.Element, metadata: MediaMetadata) -> None:
    """Title, plot, premiere date, tags and authors. The title element always exists."""

    premiere = _premiere_date(metadata.published_at)
    _element(root, "title", metadata.display_title, forced=True)
    _element(root, "originaltitle", metadata.original_title)
    _element(root, "plot", metadata.display_description)
    _element(root, "year", _year(premiere))
    _element(root, "premiered", premiere)
    for tag in metadata.tags:
        _element(root, "tag", tag)
    _add_creators(root, metadata)


def _render_movie(metadata: MediaMetadata, poster: str | None) -> bytes:
    root = ElementTree.Element(NFO_MOVIE)
    _add_common_header(root, metadata)
    _element(root, "runtime", _runtime_minutes(metadata.parts[0].duration_seconds))
    _add_poster(root, poster)
    _add_unique_id(root, metadata.media_key)
    return _serialize(root)


def _render_tvshow(metadata: MediaMetadata, poster: str | None) -> bytes:
    root = ElementTree.Element(NFO_TVSHOW)
    _add_common_header(root, metadata)
    _add_poster(root, poster)
    _add_unique_id(root, metadata.item_key)
    return _serialize(root)


def _episode_title(metadata: MediaMetadata, part_title: str, part_present: bool, index: int) -> str:
    """Part title, else ``<display title> - P<index>``, else nothing at all. The fallback only
    formats fields that already exist. It never invents a title for a part whose source title
    and whose upload title are both missing."""

    if part_present:
        return part_title
    if metadata.display_title:
        return f"{metadata.display_title} - P{index}"
    return ""


def _render_episode(
    metadata: MediaMetadata, request: RenderRequest, cid: str, thumb: str | None
) -> bytes:
    """One ``episodedetails``. ``thumb`` is the *leaf* file name of the thumbnail that sits next to
    this NFO; ``source.json`` keeps the package-root relative path for the same file."""

    part = metadata.part(cid)
    if part is None:  # pragma: no cover - validated against these very parts
        raise _reject("unknown_cid", "selected_cids")
    premiere = _premiere_date(metadata.published_at)
    root = ElementTree.Element(NFO_EPISODEDETAILS)
    _element(root, "title", _episode_title(metadata, part.title, part.title_present, part.index))
    _element(root, "originaltitle", metadata.original_title)
    _element(root, "showtitle", metadata.display_title)
    _element(root, "plot", metadata.display_description)
    _element(root, "season", SEASON_NUMBER)
    _element(root, "episode", request.episode_for(cid))
    _element(root, "year", _year(premiere))
    _element(root, "aired", premiere)
    _element(root, "runtime", _runtime_minutes(part.duration_seconds))
    for tag in metadata.tags:
        _element(root, "tag", tag)
    _element(root, "thumb", thumb)
    _add_creators(root, metadata)
    _add_unique_id(root, media_key(metadata.bvid, cid))
    return _serialize(root)


def _source_document(
    metadata: MediaMetadata,
    request: RenderRequest,
    media: tuple[ExpectedMedia, ...],
    images: tuple[ReferencedImage, ...],
    video_paths: dict[str, str],
    issues: tuple[Issue, ...],
) -> dict[str, object]:
    """The whitelisted, rebuildable ``source.json`` document. Nothing here comes from the caller's
    raw snapshot: there is no ``raw`` passthrough, no cookie or header, no signed or temporary media
    URL and no environment path. A package can be rebuilt from the normalized projection plus the
    application's own layout and episode assignments. The issue list is the bundle's list, so a gap
    the renderer itself found (a declared cover with no local image) travels with the package
    instead of staying in memory. Key order is imposed by the serializer, not by this literal."""

    parts: list[dict[str, object]] = []
    for part in metadata.parts:
        parts.append(
            {
                "cid": part.cid,
                "index": part.index,
                "title": part.title,
                "title_missing": not part.title_present,
                "duration_seconds": part.duration_seconds,
                "selected": part.cid in request.selected_cids,
                "episode_number": request.episode_for(part.cid),
                "video": video_paths.get(part.cid),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "provider": metadata.provider,
        "bvid": metadata.bvid,
        "item_key": metadata.item_key,
        "media_key": metadata.media_key,
        "canonical_url": metadata.canonical_url,
        "layout": request.layout,
        "media_extension": request.media_extension,
        "original_title": metadata.original_title,
        "original_description": metadata.original_description,
        "display_title": metadata.display_title,
        "display_description": metadata.display_description,
        "field_sources": {name: word for name, word in metadata.field_source},
        "published_at": metadata.published_at,
        "captured_at": metadata.captured_at,
        "cover_available": metadata.cover_available,
        "creators": [
            {
                "key": creator_key(creator.mid),
                "mid": creator.mid,
                "name": creator.name,
                "name_missing": not creator.name_present,
                "role": creator.role,
            }
            for creator in metadata.creators
        ],
        "tags": list(metadata.tags),
        "parts": parts,
        "selected_cids": list(request.selected_cids),
        "episode_numbers": [[cid, number] for cid, number in request.episode_numbers],
        "expected_media": [{"path": entry.path, "extension": entry.extension} for entry in media],
        "referenced_images": [
            {"path": entry.path, "role": entry.role, "cid": entry.cid} for entry in images
        ],
        "issues": [{"code": issue.code, "field": issue.field} for issue in issues],
    }


def _serialize_source_json(document: dict[str, object]) -> bytes:
    """UTF-8, ``ensure_ascii=False``, two-space indent, one trailing newline.

    Every mapping is written in recursive lexicographic key order (``sort_keys=True``), so the same
    projection always produces the same bytes no matter how the document was assembled. Arrays keep
    their business order: ``parts`` follows the source snapshot, ``tags``/``creators`` keep their
    first-seen order and ``selected_cids``/``episode_numbers`` keep the caller's order."""

    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    return f"{text}\n".encode("utf-8")


def _check_sidecars(files: tuple[SidecarFile, ...], directory: str) -> None:
    if not isinstance(directory, str) or not directory or "/" in directory or "\\" in directory:
        raise _reject("invalid_directory", "relative_directory")
    if ".." in directory:
        raise _reject("invalid_directory", "relative_directory")
    seen: set[str] = set()
    for entry in files:
        path = require_relative_path(entry.path, "files")
        if path in seen:
            raise _reject("duplicate_sidecar_path", "files")
        seen.add(path)
        if not isinstance(entry.content, bytes):
            raise _reject("invalid_sidecar_content", "files")


def render_sidecars(metadata: MediaMetadata, request: RenderRequest) -> SidecarBundle:
    """Build the candidate package for one layout decision and return it as immutable bytes."""

    if not isinstance(metadata, MediaMetadata):
        raise _reject("invalid_metadata", "metadata")
    checked = _validate_request(metadata, request)
    poster_binding = checked.image_for("poster", None)
    poster_name = (
        with_extension(POSTER_STEM, require_image_extension(poster_binding.extension))
        if poster_binding is not None
        else None
    )
    single = checked.layout == LAYOUT_SINGLE
    directory = (
        single_directory(metadata.bvid, checked.selected_cids[0])
        if single
        else multipart_directory(metadata.bvid)
    )
    files: list[SidecarFile] = []
    media: list[ExpectedMedia] = []
    images: list[ReferencedImage] = []
    video_paths: dict[str, str] = {}
    if single:
        video = with_extension(VIDEO_STEM, checked.media_extension)
        media.append(ExpectedMedia(path=video, extension=checked.media_extension))
        video_paths[checked.selected_cids[0]] = video
        files.append(SidecarFile(MOVIE_NFO_NAME, _render_movie(metadata, poster_name)))
    else:
        files.append(SidecarFile(TVSHOW_NFO_NAME, _render_tvshow(metadata, poster_name)))
        for cid in checked.selected_cids:
            episode = checked.episode_for(cid)
            if episode is None:  # pragma: no cover - validated as present for multipart
                raise _reject("missing_episode_number", "episode_numbers")
            video = season_path(episode_video_name(episode, cid, checked.media_extension))
            media.append(ExpectedMedia(path=video, extension=checked.media_extension))
            video_paths[cid] = video
            stem = nfo_stem(video, checked.media_extension)
            binding = checked.image_for("episode_thumb", cid)
            if binding is None:
                thumb = None
            else:
                extension = require_image_extension(binding.extension)
                # The declaration is package-root relative; the NFO references the same file by the
                # name it has inside its own directory.
                thumb_path = f"{stem}{EPISODE_THUMB_SUFFIX}.{extension}"
                images.append(ReferencedImage(path=thumb_path, role="episode_thumb", cid=cid))
                thumb = PurePosixPath(thumb_path).name
            files.append(SidecarFile(f"{stem}.nfo", _render_episode(metadata, checked, cid, thumb)))
    issues = list(metadata.issues)
    if poster_name is not None:
        images.insert(0, ReferencedImage(path=poster_name, role="poster", cid=None))
    else:
        issues.append(Issue(code=ISSUE_COVER_LOCAL_MISSING, field="images"))
    document = _source_document(
        metadata, checked, tuple(media), tuple(images), video_paths, tuple(issues)
    )
    files.append(SidecarFile(SOURCE_JSON_NAME, _serialize_source_json(document)))
    ordered = tuple(files)
    _check_sidecars(ordered, directory)
    return SidecarBundle(
        package_key=(
            package_key(LAYOUT_SINGLE, metadata.bvid, checked.selected_cids[0])
            if single
            else package_key(LAYOUT_MULTIPART, metadata.bvid)
        ),
        relative_directory=directory,
        files=ordered,
        expected_media=tuple(media),
        referenced_images=tuple(images),
        issues=tuple(issues),
        status=STATUS_RENDERED_UNVERIFIED,
    )


__all__ = [
    "EPISODE_THUMB_SUFFIX",
    "MOVIE_NFO_NAME",
    "POSTER_STEM",
    "SOURCE_JSON_NAME",
    "TVSHOW_NFO_NAME",
    "VIDEO_STEM",
    "render_sidecars",
]
