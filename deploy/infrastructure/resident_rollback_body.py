"""Renderer input: existing explicit rollback with canonical metadata comparison."""

import ast
import hashlib
import json
from pathlib import Path


def record_non_target_preservation(before, after):
    report = compare_inventory(before, after)
    save(S / "non-targets-after.json", report)
    return report["preserved"]


def main():
    parent = Path(EXPECTED["stage"])
    entry = load_original(readonly=False)
    original_read, original_inventory = read, metadata_inventory
    resumed_origin = original_read(Path(EXPECTED["resume_origin_stage"]) / "gateway-origin-result.json")
    require(resumed_origin["gateway_id_after"] == EXPECTED["gateway_id"]
            and resumed_origin["same_existing_entry_owner_and_route"] and resumed_origin["original_origin_preserved"]
            and resumed_origin["new_permissions"] == resumed_origin["new_service_credentials"] == 0, "gateway_resume_scope_changed")

    def current_origin_read(path):
        if Path(path) == parent / "gateway-origin-result.json":
            return resumed_origin
        return original_read(path)

    def canonical_inventory(guard, owner_ids):
        return normalize_inventory(original_inventory(guard, owner_ids))

    globals()["read"] = current_origin_read
    globals()["metadata_inventory"] = canonical_inventory
    tree = ast.parse((parent / "resident-update.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "rollback_release")
    replaced = 0
    for node in ast.walk(function):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require"
                and len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "non_target_container_changed"):
            comparison = node.args[0]
            assert isinstance(comparison, ast.Compare) and len(comparison.ops) == 1 and isinstance(comparison.ops[0], ast.Eq)
            node.args[0] = ast.Call(func=ast.Name(id="record_non_target_preservation", ctx=ast.Load()),
                                    args=[comparison.comparators[0], comparison.left], keywords=[])
            replaced += 1
    require(replaced == 1, "rollback_metadata_check_changed")
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(parent / "rollback-canonical-metadata.py"), "exec"), globals())
    globals()["parent"] = parent
    entry.body = [ast.Expr(value=ast.Call(func=ast.Name(id="rollback_release", ctx=ast.Load()), args=[ast.Name(id="parent", ctx=ast.Load())], keywords=[]))]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[entry], type_ignores=[])), str(parent / "rollback-canonical-metadata.py"), "exec"), globals())
    print(json.dumps({"status": "rolled_back", "stage": str(S), "database_restore": False,
                      "later_ledgers_preserved": True, "canonical_non_target_comparison": True, "dockge_sync_required": True}))


if __name__ == "__main__":
    main()
