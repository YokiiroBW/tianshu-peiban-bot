"""Materialize an exact owner contract tree using only root sources and bundled overlays."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def safe(base, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts) or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe relative path: " + relative)
    result = (base / relative).resolve()
    if not result.is_relative_to(base.resolve()):
        raise ValueError("Path escapes source: " + relative)
    return result


def materialize(root, profile, output):
    bundle = Path(__file__).resolve().parent
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    records = dict(manifest["common"])
    records.update(manifest["profiles"][profile])
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be a new or empty directory")
    prepared = []
    for target, entry in sorted(records.items()):
        source_base = root if entry["kind"] == "root" else bundle
        data = normalized(safe(source_base, entry["source"]))
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Source hash changed: " + entry["source"])
        prepared.append((safe(output, target), data, entry["sha256"]))
    # All inputs are checked before creating the output tree. Never overwrite a live tree.
    output.mkdir(parents=True, exist_ok=True)
    for target, data, expected in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        if hashlib.sha256(normalized(target)).hexdigest() != expected:
            raise ValueError("Output verification failed: " + str(target))
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != set(records):
        raise ValueError("Output file inventory mismatch")
    return {"profile": profile, "files": len(records), "verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Git coordination root containing contracts/")
    parser.add_argument("--profile", choices=("cp", "memory"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="New or empty contracts directory")
    args = parser.parse_args()
    print(json.dumps(materialize(args.root.resolve(), args.profile, args.output.resolve())))
