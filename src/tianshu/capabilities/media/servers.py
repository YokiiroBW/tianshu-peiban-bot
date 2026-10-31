"""Media-server discovery and observations; the server owns its catalog."""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import quote

from ..contracts import Fault, require
from .identity import item_key, media_key
from .publication import matching_image
from .destination import safe_path
from .engine import file_record
from .creators import NFO_PROFILE, director_name
from .creator_images import verify_creator_images


class MediaServers:
    def __init__(self, remote, creator_cache=None):
        self.remote = remote
        self.creator_cache = creator_cache

    async def libraries(self, server):
        try:
            return await self._libraries(server)
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Fault("media_peer_invalid_response", 503) from None

    async def _libraries(self, server):
        if server["kind"] == "plex":
            value = await self.remote.request(server, "GET", "/library/sections", server=True)
            rows = value.get("MediaContainer", {}).get("Directory", [])
            return [
                {
                    "library_id": str(row["key"]),
                    "label": row.get("title", str(row["key"])),
                    "paths": [
                        p["path"] for p in row.get("Location", []) if isinstance(p.get("path"), str)
                    ],
                }
                for row in rows
                if row.get("type") in {"movie", "show"}
            ]
        value = await self.remote.request(server, "GET", "/Library/VirtualFolders", server=True)
        require(isinstance(value, list), "media_peer_invalid_response", 503)
        return [
            {
                "library_id": str(row["ItemId"]),
                "label": row.get("Name", str(row["ItemId"])),
                "paths": row.get("Locations", []),
            }
            for row in value
            if row.get("ItemId")
        ]

    async def verify_plex(self, server, job):
        section = quote(server["library_id"], safe="")
        root = (
            server["media_root"].replace("\\", "/").rstrip("/")
            + "/"
            + job["asset_receipt"]["target_relative_path"]
        )
        await self.remote.request(
            server,
            "GET",
            f"/library/sections/{section}/refresh",
            params={"path": root},
            server=True,
        )
        manifest = json.loads(base64.b64decode(job["package"]["manifest_base64"]))
        wanted = {root + "/" + f["path"] for f in manifest["files"] if f["kind"] == "video"}
        found = {}
        # Plex lists episodes separately from show containers. Follow bounded pagination.
        for item_type in (1, 4):
            for start in range(0, 10000, 200):
                value = await self.remote.request(
                    server,
                    "GET",
                    f"/library/sections/{section}/all",
                    params={
                        "type": item_type,
                        "X-Plex-Container-Start": start,
                        "X-Plex-Container-Size": 200,
                    },
                    server=True,
                )
                container = value.get("MediaContainer", {})
                rows = container.get("Metadata", [])
                require(isinstance(rows, list), "media_peer_invalid_response", 503)
                for row in rows:
                    for media in row.get("Media", []):
                        for part in media.get("Part", []):
                            path = part.get("file", "").replace("\\", "/")
                            if path in wanted and row.get("ratingKey"):
                                found[path] = str(row["ratingKey"])
                if wanted <= found.keys():
                    return {
                        "state": "verified",
                        "code": "library_files_verified",
                        "item_id": next(iter(found.values())),
                    }
                if not rows or start + len(rows) >= int(
                    container.get("totalSize", start + len(rows))
                ):
                    break
        return {
            "state": "pending",
            "code": "library_item_pending",
            "item_id": next(iter(found.values()), None),
        }

    async def verify(self, target, job):
        results = []
        for server in target.get("servers", []):
            result = {
                "server_id": server["server_id"],
                "kind": server["kind"],
                "state": "unavailable",
                "code": "media_server_unavailable",
                "item_id": None,
            }
            try:
                if server["kind"] == "plex":
                    result.update(await self.verify_plex(server, job))
                    results.append(result)
                    continue
                require(server["kind"] in {"emby", "jellyfin"}, "invalid_input", 400)
                await self.remote.request(server, "POST", "/Library/Refresh", server=True)
                receipt = job["asset_receipt"]
                manifest = json.loads(base64.b64decode(job["package"]["manifest_base64"]))
                root = (
                    server["media_root"].replace("\\", "/").rstrip("/")
                    + "/"
                    + receipt["target_relative_path"]
                )
                expected_title = job.get("overrides", {}).get(
                    "title", job["snapshot"].get("title") or ""
                )
                description = job.get("overrides", {}).get(
                    "description", job["snapshot"].get("description") or ""
                )
                names = {
                    creator["name"] for creator in job["snapshot"]["creators"] if creator["name"]
                }
                profile = job["package"].get("nfo_profile") == NFO_PROFILE
                expected_people = (
                    {
                        ("Director", director_name(creator["name"], creator["mid"]))
                        if creator["role"] == "uploader"
                        else ("Actor", creator["name"])
                        for creator in job["snapshot"]["creators"]
                        if creator["name"]
                    }
                    if profile
                    else set()
                )
                checked_images = {}
                director_results = []
                single = manifest["layout"] == "single"
                identities = (
                    [media_key(job["bvid"], job["selected_cids"][0])]
                    if single
                    else [item_key(job["bvid"])]
                    + [media_key(job["bvid"], cid) for cid in job["selected_cids"]]
                )
                matched = True
                missing = False
                for index, identity in enumerate(identities):
                    cid = (
                        job["selected_cids"][0]
                        if single
                        else None
                        if index == 0
                        else job["selected_cids"][index - 1]
                    )
                    file = next(
                        (
                            value
                            for value in manifest["files"]
                            if value["kind"] == "video" and value["cid"] == cid
                        ),
                        None,
                    )
                    expected_path = root + ("/" + file["path"] if file else "")
                    listing = await self.remote.request(
                        server,
                        "GET",
                        "/Items",
                        params={
                            "ParentId": server["library_id"],
                            "Recursive": "true",
                            "AnyProviderIdEquals": "bilibili." + identity,
                            "Fields": "Overview,ProviderIds,People,ImageTags,Path",
                            "Limit": 200,
                        },
                        server=True,
                    )
                    items = listing.get("Items", []) if isinstance(listing, dict) else []
                    item = next(
                        (
                            value
                            for value in items
                            if value.get("ProviderIds", {}).get("bilibili") == identity
                            and value.get("Path", "").replace("\\", "/").rstrip("/")
                            == expected_path
                        ),
                        None,
                    )
                    if item is None:
                        missing = True
                        matched = False
                        continue
                    result["item_id"] = result["item_id"] or str(item.get("Id"))
                    expected_name = (
                        expected_title
                        if single or cid is None
                        else next(
                            part.get("title") or ""
                            for part in job["snapshot"]["parts"]
                            if part["cid"] == cid
                        )
                    )
                    observed_names = {person.get("Name") for person in item.get("People", [])}
                    observed_people = {
                        (person.get("Type"), person.get("Name"))
                        for person in item.get("People", [])
                    }
                    correct = (
                        item.get("Name") == expected_name
                        and (not description or item.get("Overview") == description)
                        and (
                            expected_people <= observed_people
                            if profile
                            else names <= observed_names
                        )
                    )
                    if profile and server["kind"] == "emby" and correct:
                        directors = await verify_creator_images(
                            self.remote, self.creator_cache, server, job, item, checked_images
                        )
                        director_results.extend(directors)
                        correct = correct and all(
                            director["state"] == "verified" for director in directors
                        )
                    image = next(
                        (
                            value
                            for value in manifest["files"]
                            if value["kind"]
                            == ("poster" if cid is None or single else "episode_thumb")
                            and value["cid"] == (None if single else cid)
                        ),
                        None,
                    )
                    if image:
                        require(item.get("Id"), "library_item_pending", 503)
                        observed_image = await self.remote.image(server, str(item["Id"]))
                        if target.get("kind") == "media_server":
                            directory = safe_path(
                                Path(target["directory"]), receipt["target_relative_path"]
                            )
                            original = safe_path(directory, image["path"])
                            record = await asyncio.to_thread(
                                file_record, original, image["path"], image["kind"], image["cid"]
                            )
                            require(record == image, "source_changed", 409)
                        else:
                            original = (
                                Path(job["staging_directory"])
                                / job["package"]["staging_ref"]
                                / image["path"]
                            )
                        correct = correct and await asyncio.to_thread(
                            matching_image, original.read_bytes(), observed_image
                        )
                    matched = matched and correct
                result.update(
                    state="verified" if matched else "pending" if missing else "mismatch",
                    code="verified"
                    if matched
                    else "library_item_pending"
                    if missing
                    else "library_metadata_mismatch",
                )
                if profile:
                    result["director_images"] = (
                        director_results
                        if server["kind"] == "emby"
                        else [
                            {
                                "state": "not_supported",
                                "code": "director_image_server_not_supported",
                            }
                        ]
                    )
                    if director_results and not all(
                        director["state"] == "verified" for director in director_results
                    ):
                        result.update(state="pending", code="director_image_pending")
            except Fault as error:
                result["code"] = error.code
            except OSError:
                result["code"] = "source_missing"
            except (KeyError, TypeError, AttributeError, ValueError):
                result["code"] = "media_peer_invalid_response"
            results.append(result)
        return results
