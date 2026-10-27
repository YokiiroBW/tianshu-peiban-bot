"""Linux/root synthetic verification using the real, hash-pinned recovery bundle.

No Docker, network, production configuration or source paths are used. --work-root
must name a new directory inside the caller's dedicated validation area. Preserve
the resulting trees as evidence; this script never deletes an existing tree.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import types
from uuid import uuid4


BUNDLE_SHA256 = "06bdc127ee205b91f662de392961ea8ac0e55d20067018f714b92cfb1c766ffc"
MODULE_SHA256 = {
    "safety": "fe650e570f246a3b6120d5a06eaaa89cde2e7a803eb6daf6e24994b1dc40b65a",
    "resident_snapshot": "8fdd4b8f5bffce3d679a47470b0450bb3430ae3d2951837f44e691d4756a8082",
}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def load_frozen(bundle_path):
    raw = bundle_path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == BUNDLE_SHA256, "frozen_bundle_changed")
    bundle = json.loads(raw)
    name = "native_alias_verification_" + uuid4().hex
    package = types.ModuleType(name)
    package.__path__ = []
    package.__package__ = name
    sys.modules[name] = package
    loaded = {}
    for module_name, expected in MODULE_SHA256.items():
        filename = "ops/recovery/" + module_name + ".py"
        item = bundle["files"][filename]
        source = base64.b64decode(item["base64"], validate=True)
        require(
            hashlib.sha256(source).hexdigest() == item["sha256"] == expected,
            "frozen_module_changed",
        )
        qualified = name + "." + module_name
        module = types.ModuleType(qualified)
        module.__file__ = filename
        module.__package__ = name
        sys.modules[qualified] = module
        exec(compile(source, filename, "exec"), module.__dict__)
        setattr(package, module_name, module)
        loaded[module_name] = module
    return loaded["resident_snapshot"], loaded["safety"]


def fingerprint(root):
    """Independent no-follow evidence, excluding read-induced access times."""
    result = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for path in [
            Path(directory),
            *(Path(directory) / name for name in sorted(dirs + names)),
        ]:
            info = path.lstat()
            value = [
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ]
            if stat.S_ISLNK(info.st_mode):
                value.append(os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                value.append(hashlib.sha256(path.read_bytes()).hexdigest())
            result[path.relative_to(root).as_posix()] = value
    return result


def make_source(
    root, *, package="a" * 64, target="lib", uid=10001, gid=10001, sibling=True
):
    environment = root / "data/platform-plugins/plugins" / package / ".environment"
    environment.mkdir(parents=True, mode=0o700)
    if sibling:
        (environment / "lib").mkdir(mode=0o700)
        (environment / "lib/payload.bin").write_bytes(b"synthetic plugin library\n")
    alias = environment / "lib64"
    os.symlink(target, alias, target_is_directory=True)
    os.lchown(alias, uid, gid)
    (root / "plain.txt").write_bytes(b"synthetic ordinary state\n")
    return alias


def rejected(safety, function):
    try:
        function()
    except safety.RecoveryError:
        return
    raise RuntimeError("unsafe_fixture_was_accepted")


def run(args):
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
    os.umask(0o077)
    root = args.work_root.absolute()
    require(
        not root.exists() and not root.is_symlink() and root.parent.is_dir(),
        "new_validation_root_required",
    )
    root.mkdir(mode=0o700)
    sys.path.insert(0, str(args.guard_root.resolve()))
    from ops.resident_capacity import guard

    require(
        Path(guard.__file__).resolve()
        == (args.guard_root / "ops/resident_capacity/guard.py").resolve(),
        "guard_import_path_changed",
    )
    snapshot, safety = load_frozen(args.bundle)
    adapter_path = Path(__file__).with_name("native_snapshot_alias.py")
    spec = importlib.util.spec_from_file_location(
        "validated_native_snapshot_alias", adapter_path
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    check = snapshot.Deadline(180)
    source = root / "source"
    alias = make_source(source)
    database = sqlite3.connect(source / "synthetic.sqlite")
    try:
        database.execute("CREATE TABLE fixture(value TEXT)")
        database.execute("INSERT INTO fixture VALUES ('synthetic only')")
        database.commit()
    finally:
        database.close()
    staging = source / "staging"
    staging.mkdir(mode=0o700)
    (staging / "a.bin").write_bytes(b"synthetic scoped hardlink\n")
    os.link(staging / "a.bin", staging / "b.bin")
    before = fingerprint(source)
    rejected(
        safety,
        lambda: snapshot.inventory(source, check, media_staging_roots=(staging,)),
    )
    original_safe_path = safety.safe_path
    adapter.install(snapshot, safety, guard._plugin_venv_link)
    require(safety.safe_path is original_safe_path, "safe_path_was_replaced")
    rejected(safety, lambda: safety.safe_path(alias))
    recorded = snapshot.inventory(source, check, media_staging_roots=(staging,))
    relative = alias.relative_to(source).as_posix()
    require(
        recorded["entries"][relative]
        == {
            "kind": "directory_alias",
            "target": "lib",
            "mode": 0o777,
            "uid": 10001,
            "gid": 10001,
        },
        "alias_manifest_changed",
    )
    require(
        not any(name.startswith(relative + "/") for name in recorded["entries"]),
        "alias_was_followed",
    )
    require(
        recorded["bytes"]
        == sum(
            item["size"]
            for item in recorded["entries"].values()
            if item["kind"] == "file"
        ),
        "alias_added_bytes",
    )
    backup, restored = root / "backup", root / "restored-disabled"
    snapshot.copy_tree(source, backup, recorded, check, media_staging_roots=(staging,))
    snapshot.copy_tree(backup, restored, recorded, check)
    for tree in (backup, restored):
        require(
            snapshot.inventory(tree, check) == recorded, "restore_inventory_changed"
        )
        link = tree / relative
        require(link.is_symlink() and os.readlink(link) == "lib", "alias_not_restored")
        require(
            (tree / "staging/a.bin").stat().st_ino
            != (tree / "staging/b.bin").stat().st_ino,
            "hardlinks_not_independent",
        )
        snapshot.sync_tree(tree)
    scratch = root / "sqlite-verification"
    scratch.mkdir(mode=0o700)
    require(
        snapshot.verify_sqlite(restored, recorded, scratch, check) == (1, 0),
        "frozen_sqlite_verification_failed",
    )
    require(fingerprint(source) == before, "source_was_modified")
    failures = []
    external = root / "external"
    external.mkdir(mode=0o700)
    (external / "sentinel").write_bytes(b"external must not be traversed\n")
    external_before = fingerprint(external)
    cases = {
        "wrong_target": {"target": "../lib"},
        "absolute_target": {"target": str(external)},
        "non_hex_package": {"package": "g" * 64},
        "wrong_uid": {"uid": 0},
        "wrong_gid": {"gid": 0},
        "missing_lib": {"sibling": False},
        "symlink_sibling_lib": {"sibling": False},
        "unrelated_link": {},
        "unscoped_hardlink": {},
    }
    for name, values in cases.items():
        tree = root / name
        current = make_source(tree, **values)
        if name == "symlink_sibling_lib":
            os.symlink(external, current.with_name("lib"), target_is_directory=True)
        elif name == "unrelated_link":
            os.symlink(external, tree / "unexpected")
        elif name == "unscoped_hardlink":
            os.link(tree / "plain.txt", tree / "duplicate.txt")
        original = fingerprint(tree)
        rejected(safety, lambda: snapshot.inventory(tree, check))
        require(fingerprint(tree) == original, "negative_source_was_modified")
        failures.append(name)
    require(fingerprint(external) == external_before, "external_source_was_modified")
    changing = root / "changing-source"
    changing_alias = make_source(changing)
    changing_recorded = snapshot.inventory(changing, check)
    original_copy_new = snapshot.copy_new
    changed = False

    def replace_after_regular_copy(*values, **keywords):
        nonlocal changed
        result = original_copy_new(*values, **keywords)
        if not changed:
            temporary = changing_alias.with_name("replacement")
            os.symlink("lib", temporary, target_is_directory=True)
            os.lchown(temporary, 10001, 10001)
            os.replace(temporary, changing_alias)
            changed = True
        return result

    snapshot.copy_new = replace_after_regular_copy
    try:
        rejected(
            safety,
            lambda: snapshot.copy_tree(
                changing, root / "changing-copy", changing_recorded, check
            ),
        )
        require(changed, "source_change_not_exercised")
    finally:
        snapshot.copy_new = original_copy_new
    failures.append("alias_replaced_during_copy")
    return {
        "status": "native_snapshot_alias_verified",
        "bundle_sha256": BUNDLE_SHA256,
        "frozen_modules_sha256": MODULE_SHA256,
        "adapter_sha256": hashlib.sha256(adapter_path.read_bytes()).hexdigest(),
        "guard_sha256": hashlib.sha256(Path(guard.__file__).read_bytes()).hexdigest(),
        "verification_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "work_root": str(root),
        "source_unchanged": True,
        "copies_verified": 2,
        "unadapted_alias_rejected": True,
        "alias_bytes": 0,
        "frozen_sqlite_databases_verified": 1,
        "negative_cases_rejected": failures,
        "production_touched": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--guard-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    print(json.dumps(run(parser.parse_args()), sort_keys=True))
