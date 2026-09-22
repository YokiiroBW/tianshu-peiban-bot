"""Read-only final-manifest consumption by the accepted DEP-F implementation.

No lifecycle, database, backup or restore operation is performed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

COMMIT = "543340ac5489545e32772eb28849b4a5c1038eed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("new_output_required")
    files = (
        "ops/recovery/__init__.py",
        "ops/recovery/manifest.py",
        "ops/recovery/safety.py",
    )
    hashes = {}
    with tempfile.TemporaryDirectory(dir=args.output.absolute().parent) as folder:
        root = Path(folder)
        for name in files:
            raw = subprocess.check_output(
                ["git", "-C", str(args.repository), "show", COMMIT + ":" + name],
                timeout=30,
            )
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            hashes[name] = hashlib.sha256(raw).hexdigest()
        script = """
import copy,json,sys
from ops.recovery.manifest import release
m=json.load(open(sys.argv[1],encoding="utf-8"))
v=release(m)
assert len([x for x in v["volumes"] if x["category"]=="observability_state"])==5
bad=copy.deepcopy(m)
bad["volumes"]=[x for x in bad["volumes"] if x["id"]!="obs-loki-state"]
try: release(bad)
except Exception as e:
    assert str(e)=="missing_observability_volume"
else: raise AssertionError("omission_accepted")
print(json.dumps({"manifest_read":"passed","missing_loki_refused":True}))
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(args.manifest.absolute())],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    report = {
        "kind": "dep-e-dep-f-manifest-consumption",
        "recovery_commit": COMMIT,
        "recovery_files_sha256": hashes,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "checks": json.loads(result.stdout),
        "real_product_restore": "not_run",
        "lifecycle": "not_run",
    }
    args.output.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
