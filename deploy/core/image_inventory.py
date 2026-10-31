"""Build-time evidence; never invokes providers, the database, or product migrations."""

import argparse
from hashlib import sha256
from importlib.metadata import distributions, distribution
import json
from pathlib import Path
import platform
import re


def tree_hash(root):
    result = sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            result.update(path.relative_to(root).as_posix().encode() + b"\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.source_sha):
        raise ValueError("exact_source_commit_required")
    browsers = Path(
        distribution("playwright").locate_file("playwright/driver/package/browsers.json")
    )
    value = {
        "schema_version": "tianshu.image.v1",
        "source_sha": args.source_sha,
        "python": platform.python_version(),
        "static_sha256": tree_hash(Path("/opt/tianshu/web/dist")),
        "packages": {item.metadata["Name"]: item.version for item in distributions()},
        "browsers": json.loads(browsers.read_text()),
    }
    Path("/opt/tianshu/image-manifest/product.json").write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    main()
