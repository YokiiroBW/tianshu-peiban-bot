"""Scoped adaptation of frozen recovery modules for the guard's plugin lib64 alias.

Install only in the disposable recovery package returned by load_recovery. The
frozen sources, safe_path, file copying and PostgreSQL protocol remain unchanged.
The caller records this adapter's hash separately from the frozen bundle hashes.
"""

from contextvars import ContextVar
import os
import stat
from types import SimpleNamespace


def install(snapshot, safety, predicate):
    """Reuse guard._plugin_venv_link(entry, info, root, UNIFIED_PROFILE)."""
    require = safety.require
    require(
        not getattr(snapshot, "_native_snapshot_alias_installed", False),
        "alias_adapter_already_installed",
    )
    require(
        snapshot.RecoveryError is safety.RecoveryError,
        "alias_adapter_namespace_mismatch",
    )
    original_inventory = snapshot.inventory
    original_copy = snapshot.copy_tree
    safe_path = safety.safe_path
    capture = ContextVar("native_snapshot_alias_inventory", default=None)

    def identity(info):
        return tuple(
            getattr(info, key)
            for key in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        )

    def alias_record(path, root, *, info=None):
        # Never resolve or open the alias. All ancestors retain safe_path's
        # original link/hardlink checks; the predicate lstat's the sibling lib.
        safe_path(path.parent)
        before = path.lstat() if info is None else info
        entry = SimpleNamespace(path=str(path))
        require(predicate(entry, before, root, "unified-one"), "linked_path")
        target = os.readlink(path)
        after = path.lstat()
        require(
            identity(before) == identity(after) and os.readlink(path) == target,
            "authority_changed",
        )
        return {
            "kind": "directory_alias",
            "target": target,
            "mode": stat.S_IMODE(before.st_mode),
            "uid": before.st_uid,
            "gid": before.st_gid,
        }, identity(before)

    def walk_tree(root, *, topdown=True, hardlink_roots=()):
        # Same explicit scandir traversal as the frozen walk. A validated alias
        # enters neither dirs nor names, so no caller can follow it or hash it.
        root = safe_path(root)
        pending = [(root, None, None)]
        while pending:
            directory, dirs, names = pending.pop()
            if dirs is not None:
                yield str(directory), dirs, names
                continue
            dirs, names = [], []
            try:
                with os.scandir(safe_path(directory)) as entries:
                    for entry in entries:
                        path = directory / entry.name
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            record = alias_record(path, root, info=info)
                            active = capture.get()
                            if active is not None and active[0] == root:
                                active[1]()
                                active[2][path.relative_to(root).as_posix()] = record
                            continue
                        safe_path(path, hardlink_roots=hardlink_roots)
                        collection = (
                            dirs if entry.is_dir(follow_symlinks=False) else names
                        )
                        collection.append(entry.name)
            except OSError:
                raise safety.RecoveryError("directory_enumeration_failed") from None
            if topdown:
                yield str(directory), dirs, names
            else:
                pending.append((directory, dirs, names))
            pending.extend((directory / name, None, None) for name in reversed(dirs))

    def inventory(root, check, *, media_staging_roots=()):
        root = safe_path(root)
        aliases = {}
        token = capture.set((root, check, aliases))
        try:
            regular = original_inventory(
                root, check, media_staging_roots=media_staging_roots
            )
        finally:
            capture.reset(token)
        require(len(regular["entries"]) + len(aliases) <= 100000, "file_count_limit")
        result = {"bytes": regular["bytes"], "entries": dict(regular["entries"])}
        for name, recorded in aliases.items():
            check()
            require(alias_record(root / name, root) == recorded, "authority_changed")
            require(name not in result["entries"], "authority_changed")
            result["entries"][name] = recorded[0]
        return result

    def copy_tree(source, target, recorded, check, *, media_staging_roots=()):
        source = safe_path(source)
        target = safe_path(target, exists=False)
        aliases, regular = {}, {}
        for name, item in recorded["entries"].items():
            (aliases if item["kind"] == "directory_alias" else regular)[name] = item
        before = {}
        for name, item in aliases.items():
            safety.relative(name)
            check()
            current = alias_record(source / name, source)
            require(current[0] == item, "source_changed_during_copy")
            before[name] = current
        # Reuse the complete frozen regular-file path, including hardlink and
        # source-change checks, sync and independent-copy verification.
        original_copy(
            source,
            target,
            {"bytes": recorded["bytes"], "entries": regular},
            check,
            media_staging_roots=media_staging_roots,
        )
        for name, item in aliases.items():
            check()
            require(
                alias_record(source / name, source) == before[name],
                "source_changed_during_copy",
            )
            destination = safe_path(target / name, exists=False)
            safe_path(destination.parent)
            os.symlink(item["target"], destination, target_is_directory=True)
            os.lchown(destination, item["uid"], item["gid"])
            require(
                alias_record(destination, target)[0] == item, "copied_state_mismatch"
            )
        # Frozen sync_tree resolves its walk_tree from this same safety module.
        # The restricted walk validates aliases and fsyncs their real parents.
        snapshot.sync_tree(target)
        require(inventory(target, check) == recorded, "copied_state_mismatch")

    safety.walk_tree = snapshot.walk_tree = walk_tree
    snapshot.inventory = inventory
    snapshot.copy_tree = copy_tree
    snapshot._native_snapshot_alias_installed = True
