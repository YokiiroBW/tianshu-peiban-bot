"""Prepare a pinned resident cold-backup retention dry-run; never connect remotely."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from ops.recovery import resident_retention as retention


HERE = Path(__file__).resolve().parent


def prepare(*, inventory, catalog, sizes, keep, stage, worker_sha256, current_image,
            previous_image, output):
    inventory_path, catalog_path, sizes_path = map(Path, (inventory, catalog, sizes))
    facts, children, usage = (json.loads(path.read_bytes()) for path in (inventory_path, catalog_path, sizes_path))
    child_map = {row["path"]: row["children"] for row in children["snapshots"]}
    for row in facts["snapshots"]:
        row["children"] = child_map[row["path"]]
    root = str(PurePosixPath(keep).parents[1])
    if root != facts["root"] or root != children["root"] or not facts["source_readonly"]:
        raise ValueError("retention_catalog_scope_changed")
    if str(PurePosixPath(keep).parent) != stage or usage["exit_code"] != 0:
        raise ValueError("retention_stage_or_usage_invalid")
    plan = retention.make_plan(updates_root=root, authority_root="/volume2/tianshu-v2-resident", keep=keep,
                               snapshots=facts["snapshots"], containers=facts["containers"],
                               protected_images=[current_image, previous_image],
                               allocated_kib=usage["allocated_kib_sum"])
    plan["catalog_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (inventory_path, catalog_path, sizes_path)}
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    expected = {"stage": stage, "worker_sha256": worker_sha256, "plan_sha256": hashlib.sha256(encoded).hexdigest()}
    module = Path(retention.__file__).read_text(encoding="utf-8")
    raw = ("PLAN=" + repr(plan) + "\nEXPECTED=" + repr(expected) + "\n" + module + "\n"
           + (HERE / "resident_retention_body.py").read_text(encoding="utf-8")).replace("\r\n", "\n").encode()
    compile(raw, "retain-resident-cold-backups.py", "exec")
    output = Path(output)
    if not output.is_absolute() or output.exists() or not output.parent.is_dir():
        raise ValueError("new_absolute_output_required")
    with output.open("xb") as stream:
        stream.write(raw)
    plan_path = output.with_suffix(".plan.json")
    with plan_path.open("xb") as stream:
        stream.write(encoded)
    return {"status": "prepared_locally", "output": str(output), "script_sha256": hashlib.sha256(raw).hexdigest(),
            "plan": str(plan_path), "plan_sha256": expected["plan_sha256"], "keep": keep,
            "old_full_backups": len(plan["snapshots"]) - 1,
            "data_directories": sum(len(row["remove"]) for row in plan["snapshots"]),
            "production_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inventory", "catalog", "sizes", "keep", "stage", "worker-sha256", "current-image", "previous-image", "output"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
