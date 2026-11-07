"""Source-bound visual input and the semantic view of conversation structure."""

import json

from tianshu.runtime.serialization import wire
from tianshu.types import MediaRef

IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
STRUCTURAL_PARTS = frozenset({"reply", "mention", "mention_all", "reference"})
TRANSPORT_SOURCES = frozenset({"channel_forward", "channel_transport_original"})


def _media_mime(media):
    value = wire(media) if media else {}
    return (value.get("mime_type") or "").partition(";")[0].strip().lower()


def evidence_kind(text, media):
    if media:
        return "text_image" if text.strip() else "image"
    return "text" if text.strip() else "reference"


def read_view(read):
    """Keep actual speech and images; a serialized transport part is not speech."""
    locator = read.get("locator") or {}
    kind = locator.get("part_kind")
    content = read.get("content") or ""
    if kind in STRUCTURAL_PARTS:
        content = ""
    elif kind and kind != "text" and content.lstrip().startswith("{"):
        try:
            part = json.loads(content)
        except (TypeError, ValueError):
            part = None
        if (isinstance(part, dict) and part.get("kind") == kind
                and set(part) <= {"kind", "text", "media", "metadata"}):
            content = part.get("text") or ""
    missing = set(getattr(read.get("coverage"), "missing", ()))
    media = read.get("media")
    media = wire(media) if media else None
    mime = _media_mime(media)
    image = media if mime in IMAGE_MIME_TYPES else None
    if mime.startswith("image/") and mime not in IMAGE_MIME_TYPES:
        missing.add("image_mime_unsupported")
    elif kind == "image" and media is None:
        missing.add("image_original_unavailable")
    if image:
        size=image.get("size_bytes")
        if size is None:
            missing.add("image_size_unknown")
            image=None
        elif size<0:
            missing.add("image_size_invalid")
            image=None
    if image is None and (kind == "image" or mime.startswith("image/")):
        # Failed image intake is stored as transport JSON, potentially paged in
        # the middle of metadata. None of those fragments is an image analysis.
        content = ""
    return {"text": content, "media": image, "evidence_kind": evidence_kind(content,image),
        "coverage_missing": sorted(missing)}


def read_text_media_original(read, view):
    """Recognize a Source's decoded original, not a caption or transport part.

    Sources.capture retains the exact decoded UTF-8 text alongside its media
    copy. Event parts instead carry independent text and media fields, so their
    text alone does not establish that the attached file was read.
    """
    media = read.get("media")
    if not media:
        return False
    mime = _media_mime(media)
    if not (mime.startswith("text/") or mime in {"application/json", "application/xml"}
            or mime.startswith("application/") and mime.endswith(("+json", "+xml"))):
        return False
    locator = read.get("locator") or {}
    event_part = (locator.get("part_kind") is not None
        or all(name in locator for name in ("producer", "event_id", "part")))
    if event_part or locator.get("kind") in TRANSPORT_SOURCES:
        return False
    coverage = read.get("coverage")
    content = read.get("content")
    return (getattr(coverage, "representation", None) == "text" and isinstance(content, str)
        and view.get("text") == content)


def image_media(material):
    value = material.get("media")
    if not value:
        return None
    media = value if isinstance(value, MediaRef) else MediaRef(**value)
    return media if media.mime_type.partition(";")[0].strip().lower() in IMAGE_MIME_TYPES else None


def source_speaker(read, actor_id=None):
    speaker = read.get("speaker_id")
    if not speaker and read.get("origin_kind") == "human" and image_media(read):
        # This is the verified uploader, never a claim about a pictured person.
        speaker = read.get("source_person_id")
    if not speaker and read.get("origin_kind") == "actor" and actor_id:
        speaker = "actor:" + actor_id
    return speaker


def image_key(material):
    """A controlled source version, never the file hash across authorization domains."""
    media = image_media(material)
    return media.media_id if media else None


def image_aliases(materials):
    aliases, result = {}, {}
    for alias, material in materials.items():
        identity = image_key(material)
        if identity:
            result[alias] = aliases.setdefault(identity, alias)
    return result


def reply_event_ids(locator):
    values = []
    locator = locator or {}
    metadata = locator.get("metadata") or {}
    for data in (locator, metadata):
        for key in ("reply_to", "reply_to_event_id", "quoted_event_id"):
            value = data.get(key)
            if isinstance(value, (str, int)):
                values.append(str(value))
            elif isinstance(value, dict):
                candidate = value.get("event_id") or value.get("id")
                if candidate is not None:
                    values.append(str(candidate))
    if locator.get("part_kind") in {"reply", "reply_quote", "quote", "reference"}:
        original = metadata.get("original_message_id") or metadata.get("event_id")
        if original is not None:
            values.append(str(original))
    # Native QQ message IDs resolve only in the original conversation namespace.
    event = str(locator.get("event_id", ""))
    if event.startswith("message:") and event.count(":") >= 2:
        prefix = event.rsplit(":", 1)[0] + ":"
        values.extend(prefix + value for value in tuple(values) if ":" not in value)
    return list(dict.fromkeys(values))


def _event_keys(material):
    locator = material.get("locator") or {}
    # message_id on a material is the database row ID, not a channel event ID.
    return tuple(str(locator[name]) for name in ("event_id", "message_id") if locator.get(name) is not None)


def material_relations(materials):
    """Only supplied local aliases may appear in model-visible relationships."""
    events = {}
    for alias, material in materials.items():
        for event in _event_keys(material):
            events.setdefault(event, []).append(alias)
    result = {}
    for alias, material in materials.items():
        siblings = list(dict.fromkeys(other for event in _event_keys(material)
            for other in events[event] if other != alias))
        group = (alias, *siblings)
        parents = []
        mentions = []
        for member in group:
            item = materials[member]
            parents.extend(parent for event in reply_event_ids(item.get("locator"))
                for parent in events.get(event, ()) if parent not in group)
            parents.extend(parent for parent in item.get("reply_to", ())
                if parent in materials and parent not in group)
            metadata = (item.get("locator") or {}).get("metadata") or {}
            mentions.extend(value for name in ("verified_subject_ids", "mentioned_person_ids")
                for value in metadata.get(name, ()) if isinstance(value, str))
        result[alias] = {"reply_to": list(dict.fromkeys(parents)),
            "related_material_ids": siblings, "mention_subject_ids": list(dict.fromkeys(mentions))}
    return result


def visible_media(material, alias):
    media = image_media(material)
    if media is None:
        return None
    return {"kind": "image", "ref": alias, "mime_type": media.mime_type,
        **{name: getattr(media, name) for name in ("width", "height") if getattr(media, name) is not None}}
