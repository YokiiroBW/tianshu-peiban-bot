"""Extract data-only vocabulary from fixed Git objects; never import product code."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

PRODUCTS = {
    "platform": ("tianshu-platform", "services/platform/diagnostics.py"),
    "companion": ("tianshu-companion", "src/tianshu_companion/observability.py"),
    "memory": ("tianshu-memory", "src/tianshu_memory/diagnostics.py"),
    "gateway": ("tianshu-model-gateway", "src/tianshu_gateway/observability/events.py"),
}


def extract(source, service):
    tree = ast.parse(source)

    def literal(value):
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            return literal(value.left) | literal(value.right)
        return ast.literal_eval(value)

    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [n.id for n in node.targets if isinstance(n, ast.Name)]
            for key in ("EVENTS", "ERROR_CODES"):
                if key in names:
                    value = node.value
                    if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "frozenset"
                    ):
                        value = value.args[0]
                    result[key.lower()] = sorted(literal(value))
    if service == "gateway":
        result["events"] = sorted(
            {
                n.args[0].value
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_spec"
                and n.args
                and isinstance(n.args[0], ast.Constant)
            }
        )
    if set(result) != {"events", "error_codes"} or not all(result.values()):
        raise ValueError("unsupported_static_registry")
    return result


def capture(projects, commits, contract, output):
    result = {"schema_version": "dep-b-v1", "products": {}}
    for service, (repo, path) in PRODUCTS.items():
        commit = commits[service]
        if not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise ValueError("full_commit_required")
        raw = subprocess.check_output(
            ["git", "-C", str(projects / repo), "show", f"{commit}:{path}"]
        )
        result["products"][service] = {
            "repo": repo,
            "commit": commit,
            "source_path": path,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            **extract(raw.decode(), service),
        }
    manifest = (contract / "manifest.json").read_bytes()
    result["contract"] = {
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "files": {},
    }
    for name in (
        "manifest.json",
        "README.md",
        "event.schema.json",
        "examples.json",
        "negative-examples.json",
    ):
        result["contract"]["files"][name] = hashlib.sha256(
            (contract / name).read_bytes()
        ).hexdigest()
    result["product_runtime_verification"] = "not_performed_by_snapshot"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=Path, required=True)
    parser.add_argument("--commits", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture(
        args.projects, json.loads(args.commits.read_text()), args.contract, args.output
    )
