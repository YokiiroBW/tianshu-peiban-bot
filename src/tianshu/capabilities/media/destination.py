"""One directory identity for subscription snapshots, queue ownership and publication."""

import hashlib
import os
import unicodedata
from pathlib import Path

from ..contracts import require
from .identity import looks_like_windows_device_name, require_relative_path

DESTINATION_MODES = ("source", "custom")


def path_component(value, fallback="未知", maximum=100):
    """Portable display-name component shared by managed music and book files."""
    value = unicodedata.normalize("NFC", value or fallback)
    value = (
        "".join(
            "_" if ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or c in '/\\:<>"|?*' else c
            for c in value
        ).strip(" .")
        or fallback
    )
    if looks_like_windows_device_name(value):
        value = "_" + value
    return value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore").rstrip(" .") or fallback


def supports_subdirectory(target):
    return target.get("kind") == "media_server" and bool(target.get("directory"))


def source_subdirectory(source_value, label):
    """A portable source name, with an untruncated identity suffix to separate namesakes."""
    kind, identity = source_value["kind"], source_value["id"]
    if kind == "watch_later":
        return "稍后再看"
    category, suffix = {
        "uploader": ("UP主", f" [UID{identity}]"),
        "favorite": ("收藏夹", f" [FID{identity}]"),
        "collection": ("合集", f" [合集{identity.replace(':', '-')}]"),
        "series": ("合集", f" [系列{identity.replace(':', '-')}]"),
    }[kind]
    name = unicodedata.normalize("NFC", label)
    name = "".join(
        "_" if ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or c in '/\\:<>"|?*' else c for c in name
    )
    name = " ".join(name.split()).strip(" .") or "未命名"
    if looks_like_windows_device_name(name):
        name = "_" + name
    maximum = 255 - len(suffix.encode("utf-8"))
    name = name.encode("utf-8")[:maximum].decode("utf-8", errors="ignore").rstrip(" .")
    return subdirectory(f"{category}/{name or '未命名'}{suffix}")


def subscription_snapshot(target, source_value, label, mode, value=None, current=None):
    """Resolve source/custom through the same path validator used by jobs and publication."""
    require(isinstance(mode, str) and mode in DESTINATION_MODES, "invalid_input", 400)
    current = current or {}
    determined = current.get("source_subdirectory")
    if mode == "source":
        require(supports_subdirectory(target), "destination_not_supported", 409)
        determined = (
            determined
            or (
                current.get("destination_subdirectory")
                if current.get("destination_mode") == "source"
                else None
            )
            or source_subdirectory(source_value, label)
        )
        value = determined
    elif value is None:
        value = current.get("destination_subdirectory", "")
    return {
        "destination_mode": mode,
        **snapshot(target, value),
        "source_subdirectory": determined,
    }


def subdirectory(value=""):
    require(isinstance(value, str), "invalid_destination_subdirectory", 400)
    value = value.strip().replace("\\", "/")
    require(
        not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "invalid_destination_subdirectory", 400
    )
    require(len(value) <= 1024, "invalid_destination_subdirectory", 400)
    if not value:
        return ""
    parts = value.split("/")
    require(parts[0].casefold() != ".tianshu-transfer", "invalid_destination_subdirectory", 400)
    require(
        all(
            part not in {"", ".", ".."}
            and len(part.encode("utf-8")) <= 255
            and not any(ord(c) < 32 or c in ':<>"|?*' for c in part)
            and not part.endswith((" ", "."))
            and not looks_like_windows_device_name(part)
            for part in parts
        ),
        "invalid_destination_subdirectory",
        400,
    )
    return value


def safe_path(root, relative):
    require_relative_path(relative)
    parts = relative.split("/")
    require(
        all(
            not any(c in part for c in ':\x00<>"|?*')
            and not part.endswith((" ", "."))
            and not looks_like_windows_device_name(part)
            for part in parts
        ),
        "unsafe_library_path",
        409,
    )
    root = Path(root)
    for parent in (root, *root.parents):
        require(not parent.is_symlink() and not parent.is_junction(), "unsafe_library_path", 409)
    path = root
    for part in parts:
        path = path / part
        require(not path.is_symlink() and not path.is_junction(), "unsafe_library_path", 409)
    require(path.resolve().is_relative_to(root.resolve()), "unsafe_library_path", 409)
    return path


def snapshot(target, value=""):
    value = subdirectory(value)
    require(not value or supports_subdirectory(target), "destination_not_supported", 409)
    root = target.get("directory")
    path = safe_path(Path(root), value) if root and value else Path(root) if root else None
    if path:
        for parent in (path, *path.parents):
            require(
                not parent.is_symlink() and not parent.is_junction(), "unsafe_library_path", 409
            )
    return {
        "destination_subdirectory": value,
        "destination_directory": str(path) if path else None,
    }


def head_key(job):
    # Retain the existing empty-directory key, including already published jobs.
    base = job["target_id"] + ":" + job["bvid"]
    value = directory_identity(job)
    return base if not value else base + ":dir:" + hashlib.sha256(value.encode()).hexdigest()


def same_destination(first, second):
    return first["target_id"] == second["target_id"] and directory_identity(
        first
    ) == directory_identity(second)


def directory_identity(job):
    return os.path.normcase(job.get("destination_subdirectory") or "").replace("\\", "/")
