"""Pure addition of verified, per-part file observations to sidecar candidates."""

import json
from dataclasses import replace
from fractions import Fraction
from xml.etree import ElementTree

from .creators import NFO_PROFILE, director_name
from .types import NFO_XML_DECLARATION, SidecarFile


def element(root, name, value):
    if value is not None and value != "":
        ElementTree.SubElement(root, name).text = str(value)


def ratio(value):
    try:
        number = float(Fraction(value.replace(":", "/")))
        return number if number > 0 else None
    except (AttributeError, ValueError, ZeroDivisionError):
        return None


def source_observation(download):
    source = download.get("source", {})
    return {
        "actual_quality": download.get("actual_quality"),
        "sha256": download["sha256"],
        "size_bytes": download["size_bytes"],
        "source": {
            "kind": source.get("kind", "download"),
            **{
                key: source[key]
                for key in ("operation_id", "root_id", "import_record_id", "evidence_digest")
                if key in source
            },
        },
        "media": {
            key: download["media"].get(key, [])
            for key in ("duration_seconds", "video_streams", "audio_tracks", "subtitle_tracks")
        },
    }


def streamdetails(root, media):
    details = ElementTree.SubElement(ElementTree.SubElement(root, "fileinfo"), "streamdetails")
    for video in media.get("video_streams", []):
        node = ElementTree.SubElement(details, "video")
        for tag, key in (
            ("codec", "codec_name"),
            ("width", "width"),
            ("height", "height"),
            ("colorspace", "color_space"),
            ("colortransfer", "color_transfer"),
            ("colorprimaries", "color_primaries"),
        ):
            element(node, tag, video.get(key))
        element(
            node,
            "framerate",
            ratio(video.get("avg_frame_rate")) or ratio(video.get("r_frame_rate")),
        )
        if video.get("width") and video.get("height"):
            element(
                node,
                "aspect",
                ratio(video.get("display_aspect_ratio"))
                or video["width"]
                / video["height"]
                * (ratio(video.get("sample_aspect_ratio")) or 1),
            )
        duration = media.get("duration_seconds")
        element(node, "durationinseconds", int(duration) if duration is not None else None)
        hdr = (
            "dolbyvision"
            if any(
                side.get("side_data_type") == "DOVI configuration record"
                for side in video.get("side_data_list") or []
            )
            else "hdr10"
            if video.get("color_transfer") == "smpte2084"
            else "hlg"
            if video.get("color_transfer") == "arib-std-b67"
            else None
        )
        element(node, "hdrtype", hdr)
    for audio in media.get("audio_tracks", []):
        node = ElementTree.SubElement(details, "audio")
        for tag, value in (
            ("codec", audio.get("codec_name")),
            ("channels", audio.get("channels")),
            ("language", (audio.get("tags") or {}).get("language")),
            ("samplingrate", audio.get("sample_rate")),
        ):
            element(node, tag, value)
    for subtitle in media.get("subtitle_tracks", []):
        node = ElementTree.SubElement(details, "subtitle")
        element(node, "codec", subtitle.get("codec_name"))
        element(node, "language", (subtitle.get("tags") or {}).get("language"))


def enrich_sidecars(bundle, metadata, downloads):
    document = json.loads(bundle.text("source.json"))
    document["nfo_profile"] = NFO_PROFILE
    for creator in document["creators"]:
        if creator["role"] == "uploader" and not creator["name_missing"]:
            creator["director_name"] = director_name(creator["name"], creator["mid"])
    for part in document["parts"]:
        part["actual_media"] = (
            source_observation(downloads[part["cid"]]) if part["cid"] in downloads else None
        )
    files = []
    for file in bundle.files:
        if file.path == "source.json":
            files.append(
                SidecarFile(
                    file.path,
                    (
                        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode(),
                )
            )
            continue
        root = ElementTree.fromstring(file.content)
        extension = ElementTree.SubElement(root, "tianshu", {"profile": NFO_PROFILE})
        creators = ElementTree.SubElement(extension, "creators")
        for creator in metadata.creators:
            node = ElementTree.SubElement(
                creators, "creator", {"mid": creator.mid, "role": creator.role}
            )
            element(node, "name", creator.name)
            element(node, "url", "https://space.bilibili.com/" + creator.mid)
        cid = next(
            (
                part["cid"]
                for part in document["parts"]
                if part["selected"]
                and (file.path == "movie.nfo" or "cid-" + part["cid"] + ".nfo" in file.path)
            ),
            None,
        )
        if cid:
            observed = document["parts"][
                next(i for i, part in enumerate(document["parts"]) if part["cid"] == cid)
            ]["actual_media"]
            node = ElementTree.SubElement(
                extension, "source", {"provider": "bilibili", "bvid": metadata.bvid, "cid": cid}
            )
            for tag in ("actual_quality", "sha256", "size_bytes"):
                element(node, tag, observed[tag])
            streamdetails(root, observed["media"])
        ElementTree.indent(root, space="  ")
        raw = NFO_XML_DECLARATION + "\n" + ElementTree.tostring(root, encoding="unicode") + "\n"
        files.append(SidecarFile(file.path, raw.encode()))
    return replace(bundle, files=tuple(files))
