"""Prepare an explicit read-only completion for a resumed resident release."""

import argparse
import ast
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def prepare(*, stage, worker_sha256, image_id, platform_id, gateway_id, state_dir,
            guard_commit, memory_limit, resume_origin_stage, output):
    output = Path(output)
    if not output.is_absolute() or not output.parent.is_dir() or output.exists():
        raise ValueError("new_absolute_output_required")
    expected = {"stage": stage, "worker_sha256": worker_sha256, "image_id": image_id,
                "platform_id": platform_id, "gateway_id": gateway_id, "state_dir": state_dir,
                "guard_commit": guard_commit, "memory_limit": int(memory_limit),
                "resume_origin_stage": resume_origin_stage,
                "sqlite_databases": 37, "non_targets": 367}
    raw = ("EXPECTED=" + repr(expected) + "\n" + (HERE / "resident_metadata.py").read_text(encoding="utf-8")
           + "\n" + (HERE / "resident_readback_body.py").read_text(encoding="utf-8")).replace("\r\n", "\n").encode()
    compile(raw, "resume-readback.py", "exec")
    rollback_path = output.with_name("rollback-canonical-metadata.py")
    if rollback_path.exists() or rollback_path == output:
        raise ValueError("new_rollback_output_required")
    body = (HERE / "resident_readback_body.py").read_text(encoding="utf-8")
    loader = next(node for node in ast.parse(body).body if isinstance(node, ast.FunctionDef) and node.name == "load_original")
    rollback = ("EXPECTED=" + repr(expected) + "\n" + (HERE / "resident_metadata.py").read_text(encoding="utf-8")
                + "\n" + ast.get_source_segment(body, loader) + "\n"
                + (HERE / "resident_rollback_body.py").read_text(encoding="utf-8")).replace("\r\n", "\n").encode()
    compile(rollback, "rollback-canonical-metadata.py", "exec")
    with output.open("xb") as stream:
        stream.write(raw)
    with rollback_path.open("xb") as stream:
        stream.write(rollback)
    return {"status": "prepared_locally", "script_sha256": hashlib.sha256(raw).hexdigest(),
            "output": str(output), "remote_executed": False, "starts_owners": False,
            "rollback_output": str(rollback_path), "rollback_sha256": hashlib.sha256(rollback).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("stage", "worker-sha256", "image-id", "platform-id", "gateway-id", "state-dir", "guard-commit", "memory-limit", "resume-origin-stage", "output"):
        parser.add_argument("--" + name, required=True)
    print(json.dumps(prepare(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
