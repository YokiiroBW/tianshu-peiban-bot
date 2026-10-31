"""Observe applied historical NFOs by exact Emby paths, independently of publication."""

from pathlib import Path

from ..contracts import Fault, digest, require
from .creator_images import verify_creator_images
from .creators import named_uploaders
from .import_metadata_files import read_nfo
from .import_nfo import parse
from .import_records import imported_path, observation
from .storage_scan import server_path


def technical_match(root, item):
    expected = root.findall("fileinfo/streamdetails/video")
    if not expected:
        return True
    streams = item.get("MediaStreams") or []
    videos = [stream for stream in streams if stream.get("Type") == "Video"]
    audio = [stream for stream in streams if stream.get("Type") == "Audio"]
    if len(videos) != len(expected) or len(audio) != len(
        root.findall("fileinfo/streamdetails/audio")
    ):
        return False
    for node, stream in zip(expected, videos):
        if node.findtext("codec") != stream.get("Codec"):
            return False
        for tag, field in (("width", "Width"), ("height", "Height")):
            if node.findtext(tag) and str(stream.get(field)) != node.findtext(tag):
                return False
    for node, stream in zip(root.findall("fileinfo/streamdetails/audio"), audio):
        if node.findtext("codec") != stream.get("Codec"):
            return False
        if node.findtext("channels") and str(stream.get("Channels")) != node.findtext("channels"):
            return False
    return True


async def verify_plan(media, plan, server_id, library_id, server_root, refresh):
    root = media.imports.root(plan["root_id"])
    require(root["directory"] == plan["root_directory"], "import_scope_changed", 409)
    prefix = server_path(server_root)
    require(isinstance(library_id, str) and library_id, "invalid_input", 400)
    servers = [
        server
        for target in media.config["targets"].values()
        for server in target.get("servers", [])
        if server["server_id"] == server_id
    ]
    require(
        servers and all(server == servers[0] for server in servers), "target_not_configured", 409
    )
    server = {**servers[0], "library_id": library_id}
    require(server["kind"] == "emby", "invalid_input", 400)
    report = {
        "plan_id": plan["plan_id"],
        "root_id": plan["root_id"],
        "server_id": server_id,
        "server_root": prefix,
        "library_id": library_id,
        "state": "partial",
        "observed_at": media.repo.clock(),
        "files": [],
        "code": None,
    }
    checked_images = {}
    try:
        if refresh:
            await media.remote.request(server, "POST", "/Library/Refresh", server=True)
        found = {}
        for start in range(0, 100000, 200):
            listing = await media.remote.request(
                server,
                "GET",
                "/Items",
                params={
                    "ParentId": library_id,
                    "Recursive": "true",
                    "Fields": "Overview,People,Path,ProviderIds,MediaStreams",
                    "Limit": 200,
                    "StartIndex": start,
                },
                server=True,
            )
            require(
                isinstance(listing, dict) and isinstance(listing.get("Items"), list),
                "media_peer_invalid_response",
                503,
            )
            rows = listing["Items"]
            for item in rows:
                if item.get("Path"):
                    found.setdefault(item["Path"].replace("\\", "/").rstrip("/"), []).append(item)
            if (
                len(rows) < 200
                or listing.get("TotalRecordCount") is not None
                and start + len(rows) >= int(listing["TotalRecordCount"])
            ):
                break
        else:
            raise Fault("library_listing_incomplete", 503)
        for entry in plan["files"]:
            result = {
                "entry_id": entry["entry_id"],
                "nfo_relative_path": entry["nfo_relative_path"],
                "state": "pending",
                "code": "import_metadata_not_applied",
                "item_id": None,
                "director_images": [],
            }
            try:
                require(
                    entry.get("candidate_file") and entry["state"] != "failed",
                    "import_candidate_unavailable",
                    409,
                )
                raw, hashed = await media.local(
                    read_nfo, root["directory"], entry["nfo_relative_path"]
                )
                require(hashed == entry["after_sha256"], "import_metadata_not_applied", 409)
                for relative, expected in entry["video_observations"].items():
                    path = await media.local(imported_path, root["directory"], relative)
                    require(
                        await media.local(observation, path) == expected,
                        "import_observation_changed",
                        409,
                    )
                nfo = parse(raw)
                relative = (
                    Path(entry["nfo_relative_path"]).parent.as_posix()
                    if entry["type"] == "tvshow"
                    else entry["video_relative_paths"][0]
                )
                expected_path = prefix + ("/" + relative if relative != "." else "")
                result["expected_path"] = expected_path
                kind = {"movie": "Movie", "episodedetails": "Episode", "tvshow": "Series"}[
                    entry["type"]
                ]
                items = [item for item in found.get(expected_path, []) if item.get("Type") == kind]
                require(
                    len(items) == 1,
                    "library_item_pending" if not items else "library_identity_conflict",
                    503,
                )
                item = items[0]
                result["item_id"] = item.get("Id")
                require(entry.get("snapshot"), "import_metadata_source_unavailable", 503)
                named_snapshot = {
                    **entry["snapshot"],
                    "creators": [
                        creator
                        for creator in entry["snapshot"]["creators"]
                        if isinstance(creator.get("name"), str)
                    ],
                }
                uploaders = named_uploaders(named_snapshot)
                require(uploaders, "director_source_missing", 503)
                description = nfo.findtext("plot") or nfo.findtext("outline")
                require(
                    not description or item.get("Overview") == description,
                    "library_metadata_mismatch",
                    503,
                )
                require(item.get("Name") == nfo.findtext("title"), "library_metadata_mismatch", 503)
                people = {
                    (person.get("Type"), person.get("Name")) for person in item.get("People", [])
                }
                require(
                    all(("Director", creator["director_name"]) in people for creator in uploaders),
                    "director_pending",
                    503,
                )
                require(technical_match(nfo, item), "library_streamdetails_mismatch", 503)
                avatars = []
                for avatar in entry.get("creator_avatars", []):
                    current = await media.local(media.repo.get, "creator_avatar", avatar["mid"])
                    avatars.append(current if current and current["state"] == "ready" else avatar)
                result["director_images"] = await verify_creator_images(
                    media.remote,
                    media.creator_cache,
                    server,
                    {"snapshot": named_snapshot, "creator_avatars": avatars},
                    item,
                    checked_images,
                )
                require(
                    all(image["state"] == "verified" for image in result["director_images"]),
                    "director_image_pending",
                    503,
                )
                require(media.imports.root(plan["root_id"]) == root, "import_scope_changed", 409)
                require(
                    (await media.local(read_nfo, root["directory"], entry["nfo_relative_path"]))[1]
                    == entry["after_sha256"],
                    "import_metadata_not_applied",
                    409,
                )
                for relative, expected in entry["video_observations"].items():
                    path = await media.local(imported_path, root["directory"], relative)
                    require(
                        await media.local(observation, path) == expected,
                        "import_observation_changed",
                        409,
                    )
                result.update(state="verified", code="import_metadata_verified")
            except (Fault, OSError, ValueError) as error:
                result.update(
                    state="pending",
                    code=error.code if isinstance(error, Fault) else "import_source_unavailable",
                )
            report["files"].append(result)
        report["state"] = (
            "verified"
            if report["files"]
            and all(entry["state"] == "verified" for entry in report["files"])
            and not plan.get("discovery_failures")
            else "partial"
        )
    except (Fault, ValueError, TypeError, KeyError) as error:
        report.update(
            state="unavailable",
            code=error.code if isinstance(error, Fault) else "media_peer_invalid_response",
        )
    identity = digest(
        {
            "plan_id": plan["plan_id"],
            "server_id": server_id,
            "library_id": library_id,
            "server_root": prefix,
        }
    )
    return await media.local(media.repo.put, "import_metadata_verification", identity, report)
