"""Bind a built console to its exact frontend inputs and ship its verified bytes."""

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def rows_hash(rows):
    return digest(
        json.dumps(
            rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )


def inventory(directory, *, prefix=""):
    rows = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("frontend_regular_files_required")
        if path.is_file():
            raw = path.read_bytes()
            rows.append(
                {
                    "path": prefix + path.relative_to(directory).as_posix(),
                    "size": len(raw),
                    "sha256": digest(raw),
                }
            )
    return sorted(rows, key=lambda row: row["path"])


def package_frontend(bundle, manifest_path, platform_source, output, platform_commit):
    """Backend integration may change the commit; every frontend input must still match.

    The build manifest attests the Node/npm command. This step independently
    verifies the provided bundle and every tracked frontend input against the
    exact exported Platform source, without trusting its mutable checkout.
    """
    bundle, source, output = map(Path, (bundle, platform_source, output))
    raw_manifest = Path(manifest_path).read_bytes()
    manifest = json.loads(raw_manifest)
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("source_clean") is not True
    ):
        raise ValueError("frontend_clean_build_manifest_required")
    inputs = inventory(source / "apps/web", prefix="apps/web/")
    for name in ("package.json", "package-lock.json"):
        raw = (source / name).read_bytes()
        inputs.append({"path": name, "size": len(raw), "sha256": digest(raw)})
    inputs.sort(key=lambda row: row["path"])
    files = inventory(bundle)
    if (
        inputs != manifest.get("inputs")
        or rows_hash(inputs) != manifest.get("inputs_sha256")
        or digest((source / "package-lock.json").read_bytes())
        != manifest.get("lock_sha256")
    ):
        raise ValueError("frontend_source_inputs_mismatch")
    if (
        files != manifest.get("files")
        or rows_hash(files) != manifest.get("files_sha256")
        or not any(row["path"] == "index.html" for row in files)
    ):
        raise ValueError("frontend_bundle_hash_mismatch")
    if output.exists():
        raise ValueError("frontend_output_already_exists")
    for row in files:
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("frontend_bundle_path_invalid")
        target = output.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / row["path"], target)
    return {
        "producer_source_commit": manifest["source_commit"],
        "release_platform_commit": platform_commit,
        "source_inputs_match": True,
        "manifest_sha256": digest(raw_manifest),
        "inputs_sha256": rows_hash(inputs),
        "files_sha256": rows_hash(files),
        "node_version": manifest["node_version"],
        "npm_version": manifest["npm_version"],
        "target": "/srv/tianshu/web",
        "files": files,
    }
