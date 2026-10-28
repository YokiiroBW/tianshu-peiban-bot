"""Renderer input: validate the fixed cold snapshot allowlist, then optionally prune."""

import argparse
import ast
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    _require(os.name == "posix" and os.geteuid() == 0, "linux_root_required")
    os.umask(0o077)
    # The historical worker is only an immutable input. Never execute release(),
    # start/stop owners, change configuration, or invoke its failure cleanup.
    stage = Path(EXPECTED["stage"])
    raw = (stage / "resident-update.py").read_bytes()
    _require(_sha(raw) == EXPECTED["worker_sha256"], "original_worker_changed")
    tree = ast.parse(raw)
    release_node = next(node for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "RELEASE" for target in node.targets))
    release = ast.literal_eval(release_node.value)
    _require(release["stage"] == EXPECTED["stage"] and release["paths"]["root"] == PLAN["authority_root"],
             "retention_release_scope_changed")
    _require(_sha(json.dumps(PLAN, sort_keys=True, separators=(",", ":")).encode() + b"\n") == EXPECTED["plan_sha256"],
             "retention_plan_changed")
    code = stage / "recovery-code"
    for name, item in release["recovery"]["files"].items():
        _require(_sha((code / name).read_bytes()) == item["sha256"], "recovery_source_changed")
    sys.path.insert(0, str(code))
    from ops.recovery import resident_snapshot as snapshot
    docker = release["paths"]["docker"]
    images = subprocess.run([docker, "image", "inspect", "--format", "{{.Id}}", *PLAN["protected_images"]],
                            check=True, capture_output=True, text=True, timeout=30).stdout.split()
    _require(set(images) == set(PLAN["protected_images"]), "retention_protected_images_changed")
    receipt = stage / ("retention-" + ("applied-" if args.execute else "dry-run-") + EXPECTED["plan_sha256"][:12] + ".json")
    _require(not receipt.exists(), "retention_run_receipt_exists")
    try:
        report = apply_plan(PLAN, snapshot=snapshot, containers=lambda: docker_references(docker), execute=args.execute)
    except Exception as error:
        report = {"status": "requires_inspection", "plan_sha256": EXPECTED["plan_sha256"],
                  "execute": args.execute, "code": str(error) if re.fullmatch("[a-z0-9_]+", str(error)) else "retention_failed",
                  "only_allowlisted_data_directories_eligible": True, "keep": PLAN["keep"]}
        with receipt.open("xb") as stream:
            stream.write(_json(report) + b"\n"); stream.flush(); os.fsync(stream.fileno())
        raise
    with receipt.open("xb") as stream:
        stream.write(_json(report) + b"\n"); stream.flush(); os.fsync(stream.fileno())
    print(json.dumps({**report, "receipt": str(receipt)}, sort_keys=True))


if __name__ == "__main__":
    main()
