"""Version the existing resident recovery source input with the shared retention hook."""

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path

from ops.recovery import resident_retention, resident_snapshot, safety


SNAPSHOT = "ops/recovery/resident_snapshot.py"
POLICY = "ops/recovery/resident_retention.py"
FILES = {"ops/__init__.py", "ops/recovery/__init__.py", "ops/recovery/safety.py", SNAPSHOT, POLICY}


def current_recovery(base, *, base_sha256):
    """Freeze one public source set for every new renderer, including music releases."""
    provided = base is not None
    if base is None:
        base = {"revision": "public-resident-recovery/1", "files": {}}
    result = copy.deepcopy(base)
    if not set(base["files"]) <= FILES or (provided and SNAPSHOT not in base["files"]):
        raise ValueError("recovery_source_set_invalid")
    for item in base["files"].values():
        if hashlib.sha256(base64.b64decode(item["base64"])).hexdigest() != item["sha256"]:
            raise ValueError("recovery_source_changed")
    # One public source feeds both the CLI and each fixed release bundle.
    recovery_dir = Path(resident_snapshot.__file__).parent
    sources = {SNAPSHOT: Path(resident_snapshot.__file__), POLICY: Path(resident_retention.__file__),
               "ops/recovery/safety.py": Path(safety.__file__), "ops/recovery/__init__.py": recovery_dir / "__init__.py"}
    for name, path in sources.items():
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        result["files"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}
    # The source repository uses an ops namespace. A standalone fixed input
    # gets an empty package marker so a different installed ops cannot win.
    result["files"].setdefault("ops/__init__.py", {"sha256": hashlib.sha256(b"").hexdigest(), "base64": ""})
    result["retention"] = {"policy": "latest_verified_resident_cold_backup/1",
                           "base_input_sha256": base_sha256,
                           "base_snapshot_sha256": base["files"].get(SNAPSHOT, {}).get("sha256"),
                           "snapshot_sha256": result["files"][SNAPSHOT]["sha256"],
                           "policy_sha256": result["files"][POLICY]["sha256"]}
    return result


def prepare(*, output, source=None):
    source, output = Path(source) if source is not None else None, Path(output)
    if not output.is_absolute() or output.exists() or not output.parent.is_dir():
        raise ValueError("new_absolute_output_required")
    base_raw = source.read_bytes() if source is not None else None
    result = current_recovery(json.loads(base_raw) if base_raw is not None else None,
                              base_sha256=hashlib.sha256(base_raw).hexdigest() if base_raw is not None else None)
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    with output.open("xb") as stream:
        stream.write(raw)
    directory = output.with_suffix("").with_name(output.stem + "-files")
    directory.mkdir(mode=0o700)
    for name, item in result["files"].items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(item["base64"]))
    return {"status": "prepared_locally", "output": str(output), "sha256": hashlib.sha256(raw).hexdigest(),
            "files_directory": str(directory), "retention": result["retention"],
            "files": {name: item["sha256"] for name, item in result["files"].items()}, "production_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Optional historical input for provenance; code always comes from the current public source set.")
    parser.add_argument("--output", required=True)
    print(json.dumps(prepare(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
