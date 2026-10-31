"""Owned candidate files and read-only historical NFO discovery."""

import base64
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from ..contracts import Fault, digest, require
from .import_nfo import parse
from .import_records import imported_path, observation, same_identity
from .video_storage import sync_directory


def read_nfo(root, relative):
    path = imported_path(root, relative)
    before = observation(path)
    require(before["size_bytes"] <= 1024 * 1024, "import_nfo_invalid", 409)
    raw = path.read_bytes()
    require(observation(path) == before, "import_nfo_changed", 409)
    return raw, hashlib.sha256(raw).hexdigest()


def discover(root, records):
    groups, failures, creations = {}, [], {}
    parents = {}
    for record in records:
        parents.setdefault(Path(record["relative_path"]).parent, []).append(record)
    for record in records:
        relative = record["relative_path"]
        if any(part in {"@eaDir", "#recycle"} for part in Path(relative).parts):
            continue
        try:
            video = imported_path(root, relative)
            require(
                same_identity(observation(video), record.get("observation")),
                "import_observation_changed",
                409,
            )
            candidates = [video.with_suffix(".nfo")]
            if len(parents[Path(relative).parent]) == 1:
                candidates.append(video.parent / "movie.nfo")
            found, found_type = None, None
            for path in dict.fromkeys(candidates):
                if path.exists():
                    nfo_relative = path.relative_to(root).as_posix()
                    raw, _ = read_nfo(root, nfo_relative)
                    found_type = parse(raw).tag
                    require(found_type != "tvshow", "import_nfo_ambiguous", 409)
                    found = nfo_relative
                    break
            numbering = re.search(r"(?:^|[ -])S([0-9]{1,3})E([0-9]{1,4})(?:\D|$)", video.stem)
            season_folder = re.fullmatch(r"Season[ _-]*([0-9]+)", video.parent.name, re.IGNORECASE)
            proven_episode = bool(
                numbering
                and season_folder
                and record.get("bvid")
                and record.get("cid")
                and record.get("identity_evidence")
                and int(numbering[2]) == record.get("part_index")
                and int(season_folder[1]) == int(numbering[1])
            )
            if found is None:
                require(proven_episode, "import_nfo_missing", 409)
                found = video.with_suffix(".nfo").relative_to(root).as_posix()
                imported_path(root, found)
                creations[found] = {
                    "type": "episodedetails",
                    "season": int(numbering[1]),
                    "episode": int(numbering[2]),
                }
                found_type = "episodedetails"
            groups.setdefault(found, {})[relative] = record
            found_show = False
            for parent in (video.parent, *video.parent.parents):
                if not parent.is_relative_to(root):
                    break
                show = parent / "tvshow.nfo"
                if show.exists():
                    show_relative = show.relative_to(root).as_posix()
                    raw, _ = read_nfo(root, show_relative)
                    require(parse(raw).tag == "tvshow", "import_nfo_invalid", 409)
                    groups.setdefault(show_relative, {})[relative] = record
                    found_show = True
                    break
            if proven_episode and found_type == "episodedetails" and not found_show:
                show_relative = (video.parent.parent / "tvshow.nfo").relative_to(root).as_posix()
                imported_path(root, show_relative)
                groups.setdefault(show_relative, {})[relative] = record
                creations[show_relative] = {"type": "tvshow"}
        except (Fault, OSError) as error:
            failures.append(
                {
                    "relative_path": relative,
                    "bvid": record.get("bvid"),
                    "code": error.code if isinstance(error, Fault) else "import_source_unavailable",
                }
            )
    for relative, creation in creations.items():
        bvid = next(iter(groups[relative].values()))["bvid"]
        if creation["type"] == "tvshow":
            for template, parts in sorted(groups.items()):
                if template not in creations and all(
                    record.get("bvid") == bvid for record in parts.values()
                ):
                    raw, _ = read_nfo(root, template)
                    if parse(raw).tag == "tvshow":
                        creation["template_nfo_relative_path"] = template
                        break
    return (
        {relative: list(parts.values()) for relative, parts in groups.items()},
        failures,
        creations,
    )


def atomic_write(path, raw):
    path = Path(path)
    require(
        not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)),
        "import_unsafe_path",
        409,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".candidate-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def save_candidate(directory, raw):
    hashed = hashlib.sha256(raw).hexdigest()
    path = Path(directory) / "files" / (hashed + ".nfo")
    atomic_write(path, raw)
    return {"candidate_file": str(path), "after_sha256": hashed}


def export_plan(plan, entries, videos):
    result = {key: value for key, value in plan.items() if key != "revision"}
    result["files"] = sorted(entries, key=lambda item: item["nfo_relative_path"])
    result["videos"] = sorted(videos.values(), key=lambda item: item["relative_path"])
    counts = {
        state: sum(entry["state"] == state for entry in entries)
        for state in ("ready", "partial", "failed", "unchanged", "already_applied", "pending")
    }
    result["counts"] = {
        **counts,
        "files": len(entries),
        "videos": len(videos),
        "discovery_failures": len(plan.get("discovery_failures", [])),
    }
    for entry in result["files"]:
        if entry.get("candidate_file"):
            path = Path(entry["candidate_file"])
            require(
                path.is_relative_to(Path(plan["output_directory"])), "import_candidate_changed", 409
            )
            raw = path.read_bytes()
            require(
                hashlib.sha256(raw).hexdigest() == entry["after_sha256"],
                "import_candidate_changed",
                409,
            )
            entry["content_base64"] = base64.b64encode(raw).decode("ascii")
    result["plan_file"] = str(Path(plan["output_directory"]) / (plan["plan_id"] + ".json"))
    atomic_write(
        result["plan_file"], (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()
    )
    return result


def entry_id(plan_id, relative):
    return digest({"plan_id": plan_id, "nfo_relative_path": relative})
