"""The media owner's configuration and public command validation."""

import re
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..contracts import require
from .stream_policy import DEFAULT_OPTIONS, normalize_options

ENGINE_VERSION = "2026.08.19"
SOURCE_KINDS = ("favorite", "collection", "series", "uploader", "watch_later")
EMPTY_RULES = {"schema_version": 1, "revision": 1, "whitelist": [], "blacklist": []}


def text(value, maximum=256):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum, "invalid_input", 400)
    require(not any(ord(c) < 32 for c in value), "invalid_input", 400)
    return value.strip()


def quality(value):
    require(isinstance(value, dict), "invalid_input", 400)
    require(
        {"mode", "quality_id", "allow_fallback"}
        <= set(value)
        <= {"mode", "quality_id", "allow_fallback", "stream_options"},
        "invalid_input",
        400,
    )
    require(value["mode"] in {"best", "exact"}, "invalid_input", 400)
    require(type(value["allow_fallback"]) is bool, "invalid_input", 400)
    if value["mode"] == "best":
        require(value["quality_id"] is None, "invalid_input", 400)
    else:
        require(
            isinstance(value["quality_id"], str)
            and re.fullmatch(r"[1-9][0-9]{0,3}", value["quality_id"]),
            "invalid_input",
            400,
        )
    result = {key: value[key] for key in ("mode", "quality_id", "allow_fallback")}
    if "stream_options" in value:
        policy = normalize_options(value["stream_options"])
        if policy != DEFAULT_OPTIONS:
            result["stream_options"] = policy
    return result


def source(value):
    require(isinstance(value, dict) and set(value) == {"kind", "id"}, "invalid_input", 400)
    require(value["kind"] in SOURCE_KINDS, "invalid_input", 400)
    identity = value["id"]
    require(isinstance(identity, str) and len(identity) <= 2048, "invalid_input", 400)
    if value["kind"] == "watch_later":
        require(identity == "self", "invalid_input", 400)
        return {"kind": "watch_later", "id": "self"}
    if identity.startswith("https://"):
        parsed = urlsplit(identity)
        require(
            parsed.hostname in {"space.bilibili.com", "www.bilibili.com", "bilibili.com"}
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443},
            "invalid_source_url",
            400,
        )
        params = parse_qs(parsed.query)
        mid = (
            re.fullmatch(r"/([1-9][0-9]{0,19})(?:/(.*))?/?", parsed.path)
            if parsed.hostname == "space.bilibili.com"
            else None
        )
        if value["kind"] == "uploader":
            require(mid and (mid[2] or "").strip("/") in {"", "video"}, "source_kind_mismatch", 400)
            identity = mid[1]
        elif value["kind"] == "favorite":
            require(
                mid and (mid[2] or "").strip("/") == "favlist" and len(params.get("fid", [])) == 1,
                "source_kind_mismatch",
                400,
            )
            identity = params["fid"][0]
        else:
            sid_name = "sid"
            expected_type = "season" if value["kind"] == "collection" else "series"
            if mid:
                suffix = (mid[2] or "").strip("/")
                list_path = re.fullmatch(r"lists/([1-9][0-9]{0,19})", suffix)
                legacy_path = (
                    suffix == "channel/collectiondetail"
                    if value["kind"] == "collection"
                    else suffix == "channel/seriesdetail"
                )
                require(
                    list_path
                    and params.get("type") == [expected_type]
                    or legacy_path
                    and len(params.get(sid_name, [])) == 1,
                    "source_kind_mismatch",
                    400,
                )
                identity = mid[1] + ":" + (list_path[1] if list_path else params[sid_name][0])
            else:
                require(
                    parsed.path.rstrip("/") == "/medialist/play"
                    and params.get("business") == ["space"]
                    and params.get("business_type")
                    == ["5" if value["kind"] == "collection" else "1"]
                    and len(params.get("business_id", [])) == 1
                    and len(params.get("mid", [])) == 1,
                    "source_kind_mismatch",
                    400,
                )
                identity = params["mid"][0] + ":" + params["business_id"][0]
    pattern = r"[1-9][0-9]{0,19}"
    if value["kind"] in {"collection", "series"}:
        pattern += r":[1-9][0-9]{0,19}"
    require(re.fullmatch(pattern, identity), "invalid_input", 400)
    return {"kind": value["kind"], "id": identity}


def configuration(settings):
    raw = settings.get("media")
    if raw is None:
        return None
    require(isinstance(raw, dict), "invalid_input", 400)
    require(
        set(raw)
        <= {
            "enabled",
            "staging_directory",
            "package_contract_directory",
            "engine",
            "targets",
            "scan_page_limit",
            "scan_item_limit",
            "scan_timeout_seconds",
            "workers",
            "storage_mappings",
            "import_roots",
        },
        "invalid_input",
        400,
    )
    require(type(raw.get("enabled", True)) is bool, "invalid_input", 400)
    if not raw.get("enabled", True):
        return None
    staging = raw.get(
        "staging_directory", str(Path(settings["database_path"]).with_suffix(".media-staging"))
    )
    require(isinstance(staging, str) and Path(staging).is_absolute(), "invalid_input", 400)
    engine = raw.get("engine", {})
    require(
        isinstance(engine, dict)
        and set(engine) <= {"python", "ffmpeg", "ffprobe", "timeout_seconds", "max_bytes"},
        "invalid_input",
        400,
    )
    targets = raw.get("targets", [])
    require(isinstance(targets, list) and len(targets) <= 32, "invalid_input", 400)
    targets = [dict(target) if isinstance(target, dict) else target for target in targets]
    ids = set()
    for target in targets:
        require(isinstance(target, dict), "invalid_input", 400)
        identity = text(target.get("target_id"), 64)
        require(identity not in ids, "invalid_input", 400)
        ids.add(identity)
        text(target.get("label"), 128)
        kind = target.setdefault(
            "kind", "assetlibrary" if target.get("publisher") else "media_server"
        )
        require(kind in {"media_server", "assetlibrary"}, "invalid_input", 400)
        if kind == "media_server":
            require(not target.get("publisher"), "invalid_input", 400)
            target.setdefault(
                "library_id",
                str(uuid.uuid5(uuid.NAMESPACE_URL, "tianshu-media-library:" + identity)),
            )
            directory = target.get("directory")
            require(
                isinstance(directory, str) and Path(directory).is_absolute(), "invalid_input", 400
            )
            target["directory"] = str(Path(directory).absolute())
            root, staging_path = Path(directory).resolve(), Path(staging).resolve()
            require(
                root != staging_path
                and root not in staging_path.parents
                and staging_path not in root.parents,
                "invalid_input",
                400,
            )
        require(isinstance(target.get("servers", []), list), "invalid_input", 400)
    local_roots = [Path(t["directory"]).resolve() for t in targets if t["kind"] == "media_server"]
    for index, root in enumerate(local_roots):
        require(
            not any(
                root == other or root in other.parents or other in root.parents
                for other in local_roots[index + 1 :]
            ),
            "invalid_input",
            400,
        )
    from .storage_scan import server_path

    mappings = raw.get("storage_mappings", [])
    require(isinstance(mappings, list) and len(mappings) <= 128, "invalid_input", 400)
    storage_mappings, mapped = [], set()
    for mapping in mappings:
        require(
            isinstance(mapping, dict)
            and set(mapping) == {"server_id", "server_prefix", "local_prefix"},
            "invalid_input",
            400,
        )
        identity = text(mapping["server_id"], 64)
        prefix = server_path(mapping["server_prefix"])
        local_prefix = text(mapping["local_prefix"], 4096)
        require(Path(local_prefix).is_absolute(), "invalid_input", 400)
        key = (identity, prefix.casefold() if re.match(r"^[A-Za-z]:/", prefix) else prefix)
        require(key not in mapped, "invalid_input", 400)
        mapped.add(key)
        storage_mappings.append(
            {
                "server_id": identity,
                "server_prefix": prefix,
                "local_prefix": str(Path(local_prefix).absolute()),
            }
        )
    result = {
        "staging_directory": str(Path(staging).resolve()),
        "package_contract_directory": raw.get(
            "package_contract_directory",
            str(Path(__file__).parent / "package_contract"),
        ),
        "engine": {
            "python": engine.get("python", sys.executable),
            "ffmpeg": engine.get("ffmpeg", "ffmpeg"),
            "ffprobe": engine.get("ffprobe", "ffprobe"),
            "timeout_seconds": engine.get("timeout_seconds", 7200),
            "max_bytes": engine.get("max_bytes", 20 * 1024**3),
        },
        "targets": {target["target_id"]: dict(target) for target in targets},
        "scan_page_limit": raw.get("scan_page_limit", 200),
        "scan_item_limit": raw.get("scan_item_limit", 5000),
        "scan_timeout_seconds": raw.get("scan_timeout_seconds", 900),
        "workers": raw.get("workers", 2),
        "storage_mappings": storage_mappings,
        "import_roots": [],
    }
    roots = raw.get("import_roots", [])
    require(isinstance(roots, list) and len(roots) <= 32, "invalid_input", 400)
    imported = set()
    for root in roots:
        require(
            isinstance(root, dict) and set(root) == {"root_id", "label", "directory"},
            "invalid_input",
            400,
        )
        identity = text(root["root_id"], 64)
        directory = text(root["directory"], 4096)
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity)
            and identity not in imported
            and Path(directory).is_absolute(),
            "invalid_input",
            400,
        )
        imported.add(identity)
        import_path, staging_path = Path(directory).resolve(), Path(staging).resolve()
        require(
            import_path != staging_path
            and import_path not in staging_path.parents
            and staging_path not in import_path.parents,
            "invalid_input",
            400,
        )
        result["import_roots"].append(
            {
                "root_id": identity,
                "label": text(root["label"], 128),
                "directory": str(Path(directory).absolute()),
            }
        )
    for name, lower, upper in (
        ("scan_page_limit", 1, 1000),
        ("scan_item_limit", 1, 50000),
        ("scan_timeout_seconds", 30, 86400),
        ("workers", 1, 4),
    ):
        require(type(result[name]) is int and lower <= result[name] <= upper, "invalid_input", 400)
    for name, lower, upper in (
        ("timeout_seconds", 30, 86400),
        ("max_bytes", 1024**2, 200 * 1024**3),
    ):
        require(
            type(result["engine"][name]) is int and lower <= result["engine"][name] <= upper,
            "invalid_input",
            400,
        )
    for name in ("python", "ffmpeg", "ffprobe"):
        text(result["engine"][name], 4096)
    return result
