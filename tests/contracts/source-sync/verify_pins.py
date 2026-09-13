"""Read only immutable Git blobs; this is source evidence, not runtime verification."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "docs/development/candidates/source-sync/evidence.json"


def audit(workspace):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    count = 0
    for product in evidence["products"]:
        repo = workspace / product["repository"]
        for entry in product["files"]:
            result = subprocess.run(
                ["git", "show", product["commit"] + ":" + entry["path"]],
                cwd=repo, check=True, capture_output=True,
            )
            data = result.stdout
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("Pinned blob mismatch: " + entry["path"])
            lines = data.decode("utf-8").splitlines()
            for anchor in entry["anchors"]:
                if anchor["text"] not in lines[anchor["line"] - 1]:
                    raise ValueError("Pinned anchor mismatch: " + entry["path"])
            count += 1
    for entry in evidence["published_packages"]:
        data = (ROOT / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != entry["sha256_lf"]:
            raise ValueError("Published dependency changed: " + entry["path"])
    print(f"PASS: {count} immutable product blobs and 2 published manifests; read-only, no product tests.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    audit(parser.parse_args().workspace.resolve())
