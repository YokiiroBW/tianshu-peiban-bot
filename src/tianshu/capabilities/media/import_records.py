"""Closed operator evidence and read-only inventory paths, independent of publication."""

import os
import re
import stat
from pathlib import Path

from ..contracts import require

HEX = r"[a-f0-9]{64}"
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv"})


def relative_path(value):
    require(
        isinstance(value, str)
        and value
        and not value.startswith("/")
        and "\\" not in value
        and "\0" not in value,
        "import_invalid_manifest",
        400,
    )
    require(
        all(
            part not in {"", ".", ".."} and not re.match(r"^[A-Za-z]:", part)
            for part in value.split("/")
        ),
        "import_invalid_manifest",
        400,
    )
    return value


def imported_path(root, relative=None):
    root = Path(root)
    path = root.joinpath(*relative_path(relative).split("/")) if relative is not None else root
    for part in (*reversed(path.parents), path):
        require(not part.is_symlink() and not part.is_junction(), "import_unsafe_path", 409)
    require(path.is_relative_to(root), "import_unsafe_path", 409)
    return path


def observation(path):
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode), "import_unsafe_path", 409)
    return {
        "dev": value.st_dev,
        "ino": value.st_ino,
        "size_bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def same_identity(first, second):
    return bool(first and second) and all(
        first[key] == second[key] for key in ("dev", "ino", "size_bytes", "mtime_ns")
    )


def manifest_entries(manifest):
    require(
        isinstance(manifest, dict)
        and set(manifest) == {"schema_version", "entries"}
        and manifest["schema_version"] == 1
        and isinstance(manifest["entries"], list),
        "import_invalid_manifest",
        400,
    )
    entries = {}
    for entry in manifest["entries"]:
        require(
            isinstance(entry, dict)
            and set(entry)
            == {
                "relative_path",
                "bvid",
                "cid",
                "part_index",
                "size_bytes",
                "mtime_ns",
                "identity_evidence",
                "quality_evidence",
            },
            "import_invalid_manifest",
            400,
        )
        path = relative_path(entry["relative_path"])
        require(path not in entries, "import_evidence_conflict", 409)
        require(
            entry["bvid"] is None
            or isinstance(entry["bvid"], str)
            and re.fullmatch(r"BV[0-9A-Za-z]{10}", entry["bvid"]),
            "import_invalid_manifest",
            400,
        )
        require(
            entry["cid"] is None
            or isinstance(entry["cid"], str)
            and re.fullmatch(r"[1-9][0-9]{0,19}", entry["cid"]),
            "import_invalid_manifest",
            400,
        )
        require(
            entry["part_index"] is None
            or type(entry["part_index"]) is int
            and 1 <= entry["part_index"] <= 1000,
            "import_invalid_manifest",
            400,
        )
        require(
            type(entry["size_bytes"]) is int
            and entry["size_bytes"] > 0
            and type(entry["mtime_ns"]) is int
            and entry["mtime_ns"] >= 0,
            "import_invalid_manifest",
            400,
        )
        identity = entry["identity_evidence"]
        if identity is not None:
            require(
                isinstance(identity, dict)
                and set(identity) - {"expected_duration_seconds"}
                == {
                    "kind",
                    "binding",
                    "relative_path",
                    "bvid",
                    "cid",
                    "part_index",
                    "source_digest",
                },
                "import_invalid_manifest",
                400,
            )
            require(
                identity["kind"] in {"bili_sync_page", "metadata_verified"}
                and identity["binding"] in {"exact_path", "unique_bv_part"}
                and isinstance(identity["source_digest"], str)
                and re.fullmatch(HEX, identity["source_digest"]),
                "import_invalid_manifest",
                400,
            )
            require(
                all(
                    identity[key] == entry[key]
                    for key in ("relative_path", "bvid", "cid", "part_index")
                )
                and entry["bvid"] is not None
                and entry["cid"] is not None,
                "import_evidence_conflict",
                409,
            )
            duration = identity.get("expected_duration_seconds")
            require(
                duration is None or type(duration) in {int, float} and 0 < duration < 864000,
                "import_invalid_manifest",
                400,
            )
            require(
                identity["binding"] != "unique_bv_part" or duration is not None,
                "import_invalid_manifest",
                400,
            )
        quality = entry["quality_evidence"]
        if quality is not None:
            require(
                isinstance(quality, dict)
                and set(quality) == {"kind", "quality", "sha256", "size_bytes", "source_digest"},
                "import_invalid_manifest",
                400,
            )
            require(
                quality["kind"] == "actual_download"
                and type(quality["quality"]) is int
                and 1 <= quality["quality"] <= 9999
                and type(quality["size_bytes"]) is int
                and quality["size_bytes"] == entry["size_bytes"]
                and all(
                    isinstance(quality[key], str) and re.fullmatch(HEX, quality[key])
                    for key in ("sha256", "source_digest")
                ),
                "import_invalid_manifest",
                400,
            )
        entries[path] = dict(entry)
    return entries


def inventory(root):
    root = imported_path(root)
    require(root.is_dir(), "import_root_unavailable", 503)
    files, pending = [], [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.casefold() in {"@eadir", "#recycle"}:
                    continue
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif (
                    entry.is_file(follow_symlinks=False) and path.suffix.lower() in VIDEO_EXTENSIONS
                ):
                    files.append(path.relative_to(root).as_posix())
    return sorted(files)
