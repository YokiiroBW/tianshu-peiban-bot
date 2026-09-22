"""Read static vocabularies from pinned Git blobs without importing product code."""

import ast
import subprocess

from .evidence import digest
from .inputs import ROLES, require

FILES = {
    "platform": "services/platform/diagnostics.py",
    "companion": "src/tianshu_companion/observability.py",
    "memory": "src/tianshu_memory/diagnostics.py",
    "gateway": "src/tianshu_gateway/observability/events.py",
}


def literal_registry(tree, name):
    def literal(value):
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
            left, right = literal(value.left), literal(value.right)
            require(
                isinstance(left, set) and isinstance(right, set),
                "unsupported_registry_union",
            )
            return left | right
        return ast.literal_eval(value)

    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "frozenset"
            ):
                require(
                    len(value.args) == 1 and not value.keywords,
                    "unsupported_registry_expression",
                )
                value = value.args[0]
            values = literal(value)
            require(
                isinstance(values, (set, tuple, dict, list)),
                "unsupported_registry_expression",
            )
            return sorted(values)
    raise ValueError("registry_not_found")


def build_catalog(binding, repositories):
    services, provenance = {}, {}
    for role in ROLES:
        commit = binding["products"][role]["commit"]
        raw = subprocess.check_output(
            ["git", "-C", repositories[role], "show", commit + ":" + FILES[role]],
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        tree = ast.parse(raw)
        if role == "gateway":
            events = sorted(
                {
                    node.args[0].value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_spec"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                }
            )
            require(events, "registry_not_found")
        else:
            events = literal_registry(tree, "EVENTS")
        services[role] = {
            "events": events,
            "error_codes": literal_registry(tree, "ERROR_CODES"),
        }
        provenance[role] = {
            "commit": commit,
            "path": FILES[role],
            "sha256": digest(raw),
        }
    services["memory-knowledge"] = services["memory"]
    return {
        "catalog_version": "dep-d/1",
        "products": binding["products"],
        "source_blobs": provenance,
        "services": services,
    }
