"""Build/export an exact committed source on the explicit managed NAS build host."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile


BASES = {
    name + "_IMAGE"
    for name in ("PYTHON", "NODE", "UV", "PG_CLIENT", "VECTOR", "LOKI", "PROMETHEUS", "GRAFANA")
}


def run(command, *, cwd=None):
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode:
        raise RuntimeError("release_build_command_failed")


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def build(checkout, source_sha, owned_root, output, images):
    checkout, owned_root, output = (
        Path(checkout).resolve(strict=True),
        Path(owned_root).resolve(strict=True),
        Path(output),
    )
    if (
        not output.is_absolute()
        or output.exists()
        or not output.resolve().is_relative_to(owned_root)
        or output.resolve() == owned_root
        or owned_root == Path(owned_root.anchor)
    ):
        raise ValueError("new_managed_build_directory_required")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("exact_source_sha_required")
    refs = json.loads(Path(images).read_text())
    if set(refs) != BASES or any(
        not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", value) for value in refs.values()
    ):
        raise ValueError("immutable_base_image_digests_required")
    if shutil.disk_usage(owned_root).free < 21474836480:
        raise ValueError("build_host_minimum_free_bytes_breached")
    output.mkdir(mode=0o700)
    run(
        [
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(output / "source.tar"),
            source_sha,
            ".dockerignore",
            "pyproject.toml",
            "uv.lock",
            "src",
            "web",
            "packages/tianshu-plugin",
            "tools/music",
            "deploy/core",
        ],
        cwd=checkout,
    )
    source = output / "source"
    source.mkdir()
    with tarfile.open(output / "source.tar") as archive:
        archive.extractall(source, filter="data")
    args = [
        "docker",
        "build",
        "--file",
        str(source / "deploy/core/Dockerfile"),
        "--iidfile",
        str(output / "image-id"),
        "--tag",
        "tianshu-core:" + source_sha,
        "--build-arg",
        "TIANSHU_SOURCE_SHA=" + source_sha,
    ]
    for key, value in sorted(refs.items()):
        args.extend(["--build-arg", key + "=" + value])
    run([*args, str(source)])
    image_id = (output / "image-id").read_text().strip()
    run(["docker", "save", "--output", str(output / "image.tar"), image_id])
    manifest = {
        "schema_version": "tianshu.release.v1",
        "source_sha": source_sha,
        "image_id": image_id,
        "created_at": datetime.now(UTC).isoformat(),
        "base_images": refs,
        "build_host_artifact_root": str(output),
        "image_tar_sha256": file_digest(output / "image.tar"),
        "source_tar_sha256": file_digest(output / "source.tar"),
    }
    (output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "release_manifest": str(output / "release.json"),
        "image_tar": str(output / "image.tar"),
        "image_id": image_id,
    }


def main():
    parser = argparse.ArgumentParser()
    for name in ("checkout", "source-sha", "owned-root", "output", "images"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(build(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
