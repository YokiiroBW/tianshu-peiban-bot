"""Pure, conservative additions to an existing historical NFO."""

import re
from xml.etree import ElementTree as ET

from ..contracts import require
from .creators import named_uploaders
from .nfo_details import element, streamdetails
from .types import NFO_XML_DECLARATION

PROFILE = "bilibili-import-metadata-v1"
KINDS = frozenset({"movie", "episodedetails", "tvshow"})


def parse(raw):
    require(len(raw) <= 1024 * 1024, "import_nfo_invalid", 409)
    try:
        root = ET.fromstring(
            raw, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        )
    except ET.ParseError:
        require(False, "import_nfo_invalid", 409)
    require(root.tag in KINDS, "import_nfo_invalid", 409)
    return root


def replace_text(root, tag, text):
    nodes = root.findall(tag)
    node = nodes[0] if nodes else ET.SubElement(root, tag)
    node.text = text
    for duplicate in nodes[1:]:
        root.remove(duplicate)


def created_nfo(creation, records, resolved, template=None):
    require(resolved is not None, "import_metadata_source_unavailable", 409)
    root = parse(template) if template else ET.Element(creation["type"])
    require(root.tag == creation["type"], "import_nfo_invalid", 409)
    if root.tag == "episodedetails":
        part = next(
            part for part in resolved["snapshot"]["parts"] if part["cid"] == records[0]["cid"]
        )
        require(part.get("title"), "import_metadata_title_missing", 409)
        replace_text(root, "title", part["title"])
        replace_text(root, "season", str(creation["season"]))
        replace_text(root, "episode", str(creation["episode"]))
    elif not template:
        require(resolved["snapshot"].get("title"), "import_metadata_title_missing", 409)
        replace_text(root, "title", resolved["snapshot"]["title"])
        element(root, "uniqueid", records[0]["bvid"])
        root.find("uniqueid").set("type", "bilibili")
    return ET.tostring(root, encoding="utf-8")


def render(raw, bvid, records, resolved=None):
    root = parse(raw)
    identifiers = [
        node.text
        for node in root.findall("uniqueid")
        if node.get("type", "").lower() == "bilibili" and node.text
    ]
    require(all(value == bvid for value in identifiers), "import_nfo_identity_conflict", 409)
    require(root.tag == "tvshow" or len(records) == 1, "import_nfo_ambiguous", 409)
    snapshot = resolved["snapshot"] if resolved else None
    description = snapshot.get("description") if snapshot else None
    if isinstance(description, str) and description.strip():
        # Emby's historical reader can prefer outline. Keep both current.
        replace_text(root, "plot", description)
        replace_text(root, "outline", description)
    if snapshot:
        for creator in named_uploaders(snapshot):
            # Preserve unrelated Directors and every historical actor/role/thumb.
            for node in root.findall("director"):
                if re.search(r"（B站 UID " + re.escape(creator["mid"]) + r"）$", node.text or ""):
                    root.remove(node)
            element(root, "director", creator["director_name"])
    for node in root.findall("tianshu"):
        if node.get("profile") == PROFILE:
            root.remove(node)
    extension = ET.SubElement(root, "tianshu", {"profile": PROFILE})
    source = ET.SubElement(extension, "source", {"provider": "bilibili", "bvid": bvid})
    if root.tag != "tvshow":
        source.set("cid", records[0]["cid"])
    element(source, "metadata_state", "available" if resolved else "unavailable")
    if resolved:
        element(source, "metadata_source_kind", resolved.get("source_kind", "bilibili"))
        element(source, "metadata_source_digest", resolved.get("source_digest"))
        element(source, "metadata_source_warning", resolved.get("source_warning"))
        element(source, "metadata_captured_at", snapshot.get("captured_at"))
    creators = ET.SubElement(extension, "creators")
    for creator in snapshot.get("creators", []) if snapshot else []:
        node = ET.SubElement(creators, "creator", {"mid": creator["mid"], "role": creator["role"]})
        element(node, "name", creator.get("name"))
        element(node, "url", "https://space.bilibili.com/" + creator["mid"])
    parts = ET.SubElement(source, "parts") if root.tag == "tvshow" else source
    for record in records:
        node = (
            ET.SubElement(parts, "part", {"cid": record["cid"]}) if root.tag == "tvshow" else parts
        )
        for tag in ("actual_quality", "sha256"):
            element(node, tag, record.get(tag))
        element(node, "size_bytes", record["observation"]["size_bytes"])
        element(
            node,
            "quality_state",
            "confirmed" if record.get("actual_quality") and record.get("sha256") else "unknown",
        )
        element(node, "import_state", record["state"])
        element(node, "import_code", record.get("code"))
        element(
            node,
            "source_duration_seconds",
            (record.get("identity_evidence") or {}).get("expected_duration_seconds"),
        )
        element(node, "actual_duration_seconds", record["media"].get("duration_seconds"))
        if snapshot:
            part = next(part for part in snapshot["parts"] if part["cid"] == record["cid"])
            element(node, "current_source_duration_seconds", part.get("duration_seconds"))
        observed = ET.SubElement(node, "file_observation")
        for key, value in record["observation"].items():
            element(observed, key, value)
    if root.tag != "tvshow":
        existing = root.find("fileinfo")
        if existing is None:
            streamdetails(root, records[0]["media"])
        else:
            for node in existing.findall("streamdetails"):
                existing.remove(node)
            temporary = ET.Element("candidate")
            streamdetails(temporary, records[0]["media"])
            existing.append(temporary.find("fileinfo/streamdetails"))
    ET.indent(root, space="  ")
    return (NFO_XML_DECLARATION + "\n" + ET.tostring(root, encoding="unicode") + "\n").encode()
