"""Freeze the complete registered SQLite set, fold WAL, and bind independent guards."""

import hashlib
import os
import sqlite3
import time
from contextlib import ExitStack, closing, contextmanager

from .safety import (
    canonical,
    child,
    digest,
    file_hash,
    files,
    read_json,
    require,
    safe_path,
    walk_tree,
)

ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def volume_directories(root, manifest, *, max_directories):
    result = set()
    for volume in manifest["volumes"]:
        if volume["kind"] != "directory":
            continue
        base = child(root, volume["host_path"])
        for directory, dirs, _ in walk_tree(base):
            for path in [
                safe_path(directory),
                *(safe_path(os.path.join(directory, name)) for name in dirs),
            ]:
                result.add(path.relative_to(root).as_posix())
                require(len(result) <= max_directories, "directory_count_limit")
    return sorted(result)


def connect(path, *, write=False):
    db = sqlite3.connect(
        safe_path(path).as_uri() + ("?mode=rw" if write else "?mode=ro"),
        uri=True,
        timeout=0.1,
        isolation_level=None,
    )
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA busy_timeout=100")
    return db


def atom(value):
    return {"blob": value.hex()} if isinstance(value, bytes) else value


def fingerprint(path, check_cancel=lambda: None):
    with closing(connect(path)) as db:
        db.set_progress_handler(lambda: (check_cancel(), 0)[1], 10000)
        require(
            db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "sqlite_integrity_failed",
        )
        require(
            not db.execute("PRAGMA foreign_key_check").fetchone(),
            "sqlite_foreign_key_failed",
        )
        definitions = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
        version = db.execute("PRAGMA user_version").fetchone()[0]
        application = db.execute("PRAGMA application_id").fetchone()[0]
        schema = digest(canonical([version, application, definitions]))
        result = hashlib.sha256(canonical([schema]))
        tables = db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        table_properties = {
            row[1]: (row[2], row[4]) for row in db.execute("PRAGMA main.table_list")
        }
        count = 0
        # Stable across page layout and WAL checkpoints; includes every table, even ledgers,
        # suppression rows, virtual-table shadows and SQLite sequence state. No value leaves IO.
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [
                column[0]
                for column in db.execute(f"SELECT * FROM {quoted} LIMIT 0").description
            ]
            require(table in table_properties, "sqlite_table_metadata_unavailable")
            table_kind, without_rowid = table_properties[table]
            require(without_rowid in (0, 1), "sqlite_table_metadata_unavailable")
            select = "*"
            if not without_rowid:
                declared = db.execute(f"PRAGMA main.table_xinfo({quoted})").fetchall()
                # SQLite identifier matching folds ASCII case, including hidden/generated
                # columns. SELECT * alone does not enumerate all possible shadowing names.
                names = {column[1].translate(ASCII_LOWER) for column in declared}
                aliases = [
                    name for name in ("_rowid_", "rowid", "oid") if name not in names
                ]
                if aliases:
                    select = aliases[0] + ",*"
                    columns = [aliases[0], *columns]
                else:
                    primary_key = [column for column in declared if column[5] > 0]
                    indexes = db.execute(f"PRAGMA main.index_list({quoted})").fetchall()
                    # A single exact INTEGER PRIMARY KEY with no separate PK index aliases
                    # the rowid and is already in SELECT *. INT, composite PK, and the
                    # inline INTEGER PRIMARY KEY DESC exception do not prove identity.
                    require(
                        table_kind in {"table", "shadow"}
                        and len(primary_key) == 1
                        and primary_key[0][2].translate(ASCII_LOWER) == "integer"
                        and primary_key[0][6] == 0
                        and primary_key[0][1] in columns
                        and not any(index[3] == "pk" for index in indexes),
                        "sqlite_row_identity_unavailable",
                    )
            order = ",".join(str(index + 1) for index in range(len(columns)))
            require(order, "sqlite_table_without_columns")
            result.update(canonical(table))
            for row in db.execute(f"SELECT {select} FROM {quoted} ORDER BY {order}"):
                check_cancel()
                result.update(canonical([atom(value) for value in row]) + b"\n")
                count += 1
        return {"sha256": result.hexdigest(), "schema_sha256": schema, "rows": count}


def verify_guards(root, inventory, resources):
    for check in inventory["guard_checks"]:
        resource = resources[check["database"]]
        guard = resources[check["guard"]]
        with closing(connect(child(root, resource["path"]))) as db:
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
        require(metadata.get("schema") == "3", "unsupported_memory_schema")
        expected = {
            "schema": 3,
            "instance": metadata["source_instance"],
            "revision": int(metadata["source_revision"]),
            "recovery": metadata["source_recovery"],
        }
        require(
            expected["revision"] >= 0 and bool(expected["instance"]),
            "invalid_guard_metadata",
        )
        require(
            canonical(read_json(child(root, guard["path"]))) == canonical(expected),
            "guard_database_mismatch",
        )


def enumerate_inputs(root, manifest, resources, *, max_bytes, max_files, omitted=()):
    result = {}
    declared = {r["path"]: r for r in resources.values()}
    ignored = {
        r["path"] + suffix
        for r in resources.values()
        if r["kind"] == "sqlite"
        for suffix in ("-wal", "-shm")
    }
    for volume in manifest["volumes"]:
        if volume["host_path"] in omitted:
            continue
        # Some optional features create sidecar databases only on first use.
        # Absence is permitted only when the independently captured inventory
        # never registered the file and an owned state directory covers it.
        if (
            volume["category"] == "sidecar"
            and volume["mount"] is False
            and volume["kind"] == "file"
            and volume["host_path"] not in declared
        ):
            optional = child(root, volume["host_path"], exists=False)
            if not optional.exists():
                require(
                    any(
                        parent["category"] == "state"
                        and parent["mount"]
                        and parent["kind"] == "directory"
                        and volume["host_path"].startswith(parent["host_path"] + "/")
                        and all(
                            parent[key] == volume[key]
                            for key in ("product", "owner_service", "backup_group")
                        )
                        for parent in manifest["volumes"]
                    ),
                    "unowned_optional_sidecar",
                )
                continue
        base = child(root, volume["host_path"])
        require(
            base.is_dir() == (volume["kind"] == "directory"), "volume_kind_mismatch"
        )
        paths = (
            [volume["host_path"]]
            if base.is_file()
            else [
                volume["host_path"] + "/" + name
                for name in files(base, max_files=max_files)
            ]
        )
        for name in paths:
            path = child(root, name)
            if name in ignored:
                continue  # WAL is consumed by SQLite backup; SHM is transient coordination.
            if name in declared:
                resource = declared[name]
                kind = resource["kind"]
                if kind != "sqlite":
                    with path.open("rb") as stream:
                        require(
                            stream.read(16) != b"SQLite format 3\x00",
                            "sqlite_must_use_backup_api",
                        )
            else:
                require(
                    volume["category"] in {"logs", "observability_state"},
                    "unregistered_state_file",
                )
                kind = (
                    "opaque_state"
                    if volume["category"] == "observability_state"
                    else "log"
                )
            prior = result.get(name)
            require(prior is None or prior == kind, "ambiguous_file_role")
            result[name] = kind
    require(set(declared) <= set(result), "resource_missing")
    for contract in manifest["contracts"]:
        base = contract["path"]
        package_manifest = child(root, base + "/manifest.json")
        require(
            file_hash(package_manifest) == contract["manifest_sha256"],
            "contract_hash_mismatch",
        )
        expected = {item["path"]: item["sha256"] for item in contract["files"]}
        expected["manifest.json"] = contract["manifest_sha256"]
        require(
            set(files(child(root, base))) == set(expected), "contract_file_set_mismatch"
        )
        for name, checksum in expected.items():
            path = base + "/" + name
            require(path not in result, "contract_volume_overlap")
            require(file_hash(child(root, path)) == checksum, "contract_hash_mismatch")
            result[path] = "contract"
    for item in manifest["evidence"]:
        name = item["path"]
        require(name not in result, "evidence_path_overlap")
        require(
            file_hash(child(root, name)) == item["sha256"], "evidence_hash_mismatch"
        )
        result[name] = "evidence_unverified"
    for name in ("release-manifest.json", "recovery-inventory.json"):
        require(name not in result, "reserved_resource_path")
        result[name] = "metadata"
    require(len(result) <= max_files, "file_count_limit")
    require(
        sum(child(root, name).stat().st_size for name in result) <= max_bytes,
        "total_size_limit",
    )
    return dict(sorted(result.items()))


@contextmanager
def freeze(root, inventory, resources):
    # The surrounding deployment lease excludes cooperating synthetic runtime processes.
    # SQLite reservations additionally block accidental database writers for the entire set.
    with ExitStack() as stack:
        for item in sorted(resources.values(), key=lambda value: value["path"]):
            if item["kind"] != "sqlite":
                continue
            db = stack.enter_context(
                closing(connect(child(root, item["path"]), write=True))
            )
            db.execute("BEGIN IMMEDIATE")
            stack.callback(db.rollback)
        verify_guards(root, inventory, resources)
        yield
        verify_guards(root, inventory, resources)


def backup_sqlite(source, target, check_cancel):
    safe_path(target, exists=False)
    require(not target.exists(), "destination_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    def progress(status, remaining, total):
        check_cancel()
        require(time.monotonic() - started < 60, "sqlite_backup_timeout")

    with (
        closing(connect(source)) as db,
        closing(sqlite3.connect(target)) as destination,
    ):
        db.backup(destination, pages=128, progress=progress, sleep=0.01)
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.commit()
    # Windows _commit requires a writable descriptor even after SQLite has closed it.
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())


def state_fingerprints(root, resources, check_cancel=lambda: None):
    result = {}
    for item in resources.values():
        if item["kind"] == "owner_lock":
            continue
        path = child(root, item["path"])
        if item["kind"] == "sqlite":
            result[item["path"]] = fingerprint(path, check_cancel)
        elif item["kind"] != "owner_lock":
            result[item["path"]] = {"sha256": file_hash(path)}
    return result
