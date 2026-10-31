"""Stopped-product PG/files/sole-key backup and empty-target restore for TianShu Core."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

import psycopg
from psycopg.conninfo import conninfo_to_dict

from capacity import CapacityGuard, docker
from installation import configuration_files, configuration_names


PAYLOAD = (".env", "installation.json", "compose.yaml", "seccomp-chromium.json", "config", "data")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def environment(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.replace("_", "a").isalnum():
            raise ValueError("invalid_installation_environment")
        result[key] = json.loads(value).replace("$$", "$") if value.startswith('"') else value
    return result


def database_parameters(dsn):
    params = conninfo_to_dict(dsn)
    if (
        params.get("dbname") != "tianshu_core"
        or params.get("user") != "tianshu_core"
        or not params.get("host")
        or params.get("hostaddr")
        or params.get("service")
    ):
        raise ValueError("dedicated_core_database_required")
    return params


def pg_tool(binary, arguments, dsn, temporary):
    params = database_parameters(dsn)
    password = params.get("password", "")
    escape = lambda value: str(value).replace("\\", "\\\\").replace(":", "\\:")
    passfile = Path(temporary) / "pgpass"
    with passfile.open("x", encoding="utf-8") as stream:
        stream.write(
            ":".join(
                escape(params.get(key, default))
                for key, default in (("host", ""), ("port", "5432"), ("dbname", ""), ("user", ""))
            )
            + ":"
            + escape(password)
            + "\n"
        )
    passfile.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update(
        {
            "PGHOST": params["host"],
            "PGPORT": params.get("port", "5432"),
            "PGUSER": params["user"],
            "PGDATABASE": params["dbname"],
            "PGPASSFILE": str(passfile),
            "PGCONNECT_TIMEOUT": "5",
        }
    )
    if params.get("sslmode"):
        env["PGSSLMODE"] = params["sslmode"]
    for name in ("sslrootcert", "sslcert", "sslkey", "sslcrl"):
        if params.get(name):
            env["PG" + name.upper()] = params[name]
    try:
        result = subprocess.run(
            [binary, *arguments],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("postgres_backup_tool_failed")
    finally:
        passfile.unlink(missing_ok=True)


def require_stopped(root):
    guard = CapacityGuard(json.loads((root / "guard/capacity.json").read_text()))
    guard.owned_container()
    row = json.loads(docker(guard.binary, "inspect", guard.verified))[0]
    if row["State"]["Running"] or row["State"].get("Restarting"):
        raise ValueError("stop_owned_product_before_backup")
    return row


def backup(root, destination):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("linux_installation_owner_required")
    root, destination = Path(root).resolve(), Path(destination)
    if (
        not destination.is_absolute()
        or destination.exists()
        or destination.resolve().is_relative_to(root)
    ):
        raise ValueError("new_external_backup_directory_required")
    row = require_stopped(root)
    install = json.loads((root / "installation.json").read_text())
    compose_names = configuration_files(root, install)
    values = environment(root / ".env")
    dsn = values["TIANSHU_DATABASE_URL"]
    params = database_parameters(dsn)
    if (
        params["host"] != install["database"]["host"]
        or int(params.get("port", 5432)) != install["database"]["port"]
    ):
        raise ValueError("installation_database_mismatch")
    destination.mkdir(mode=0o700)
    (destination / "INCOMPLETE").write_text("Backup is not complete.\n")
    with tempfile.TemporaryDirectory(prefix="pg-", dir=destination) as temporary:
        pg_tool(
            "pg_dump",
            [
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(destination / "database.dump"),
            ],
            dsn,
            temporary,
        )
    with tarfile.open(destination / "payload.tar", "w") as archive:
        payload = list(PAYLOAD)
        for name in compose_names:
            if not any(PurePosixPath(name).is_relative_to(parent) for parent in payload):
                payload.append(name)
        for name in payload:
            archive.add(root / name, arcname=name, recursive=True)
    with tarfile.open(destination / "payload.tar", "r") as archive:
        validate_members(archive)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        migrations = [
            item[0]
            for item in conn.execute(
                "SELECT name FROM runtime.schema_migrations ORDER BY name"
            ).fetchall()
        ]
        attempts = dict(
            conn.execute("SELECT status,count(*) FROM models.attempts GROUP BY status").fetchall()
        )
        vector = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0]
    manifest = {
        "schema_version": "tianshu.backup.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "installation_id": install["installation_id"],
        "source_sha": install["source_sha"],
        "image_id": row["Image"],
        "container_id": row["Id"],
        "pgvector": vector,
        "migrations": migrations,
        "attempt_states": attempts,
        "master_key_sha256": hashlib.sha256(values["TIANSHU_MASTER_KEY"].encode()).hexdigest(),
        "external_binds": install["external_binds"],
        "files": {name: digest(destination / name) for name in ("database.dump", "payload.tar")},
    }
    path = destination / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    for file in destination.iterdir():
        if file.is_file():
            file.chmod(0o600)
    (destination / "INCOMPLETE").unlink()
    return {
        "backup": str(destination),
        "manifest_sha256": digest(path),
        "source_sha": install["source_sha"],
    }


def validate_members(archive):
    members = archive.getmembers()
    try:
        installation_member = archive.getmember("installation.json")
        if not installation_member.isfile() or installation_member.size > 1048576:
            raise ValueError()
        with archive.extractfile(installation_member) as stream:
            install = json.load(stream)
        compose_names = configuration_names(Path(install["deployment_root"]), install)
    except (KeyError, TypeError, ValueError):
        raise ValueError("unsafe_backup_payload") from None
    allowed_files = {PurePosixPath(name) for name in (*PAYLOAD, *compose_names)}
    trees = {PurePosixPath("config"), PurePosixPath("data")}
    parents = {parent for name in allowed_files for parent in name.parents if parent.parts}
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path in seen
            or path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or not (
                any(path.is_relative_to(tree) for tree in trees)
                or member.isfile()
                and path in allowed_files
                or member.isdir()
                and path in parents
            )
            or not (member.isfile() or member.isdir())
            or member.uid not in {0, 10001}
            or member.gid not in {0, 10001}
        ):
            raise ValueError("unsafe_backup_payload")
        seen.add(path)
    if not allowed_files <= seen:
        raise ValueError("unsafe_backup_payload")
    return members


def restore(bundle, root, dsn_file, manifest_sha256):
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("linux_installation_owner_required")
    bundle, raw_root = Path(bundle).resolve(), Path(root)
    if (
        not raw_root.is_absolute()
        or raw_root.exists()
        or raw_root.is_symlink()
        or raw_root.resolve() == Path(raw_root.anchor)
    ):
        raise ValueError("new_empty_restore_root_required")
    if (bundle / "INCOMPLETE").exists() or digest(bundle / "manifest.json") != manifest_sha256:
        raise ValueError("backup_manifest_mismatch")
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("schema_version") != "tianshu.backup.v1" or set(manifest["files"]) != {
        "database.dump",
        "payload.tar",
    }:
        raise ValueError("backup_schema_mismatch")
    for name, expected in manifest["files"].items():
        if digest(bundle / name) != expected:
            raise ValueError("backup_content_mismatch")
    dsn = Path(dsn_file).read_text().strip()
    params = database_parameters(dsn)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        if (
            conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname NOT IN('public','information_schema') AND nspname NOT LIKE 'pg_%' LIMIT 1"
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN('r','p','v','m') LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("empty_dedicated_restore_database_required")
    with tarfile.open(bundle / "payload.tar", "r") as archive:
        members = validate_members(archive)
        raw_root.mkdir(mode=0o700)
        root = raw_root.resolve()
        (root / "RESTORE_INCOMPLETE").write_text("Database/files restore has not completed.\n")
        with tempfile.TemporaryDirectory(prefix="pg-", dir=root) as temporary:
            pg_tool(
                "pg_restore",
                [
                    "--dbname",
                    params["dbname"],
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    str(bundle / "database.dump"),
                ],
                dsn,
                temporary,
            )
        for member in members:
            path = root.joinpath(*PurePosixPath(member.name).parts)
            if not path.resolve().is_relative_to(root):
                raise ValueError("unsafe_restore_target")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                with path.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
            os.chown(path, member.uid, member.gid)
            path.chmod(member.mode & 0o777)
    values = environment(root / ".env")
    if (
        hashlib.sha256(values["TIANSHU_MASTER_KEY"].encode()).hexdigest()
        != manifest["master_key_sha256"]
    ):
        raise ValueError("restore_credential_anchor_mismatch")
    values["TIANSHU_DATABASE_URL"] = dsn
    (root / ".env").write_text(
        "".join(
            key + "=" + json.dumps(value.replace("$", "$$")) + "\n" for key, value in values.items()
        )
    )
    (root / ".env").chmod(0o600)
    install = json.loads((root / "installation.json").read_text())
    # Absolute references inside the original installation relocate to the same
    # reviewed relative files in the new root, preserving their Compose order.
    install["config_files"] = list(configuration_names(Path(install["deployment_root"]), install))
    install["deployment_root"] = str(root)
    install["database"] = {
        "host": params["host"],
        "port": int(params.get("port", 5432)),
        "name": params["dbname"],
        "user": params["user"],
    }
    configuration_files(root, install)
    (root / "installation.json").write_text(json.dumps(install, indent=2) + "\n")
    (root / "RESTORE_INCOMPLETE").unlink()
    return {
        "restored_root": str(root),
        "source_sha": install["source_sha"],
        "capacity_guard": "must_freeze_new_container_identity_before_start",
    }


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--root", required=True)
    save.add_argument("--destination", required=True)
    load = commands.add_parser("restore")
    load.add_argument("--bundle", required=True)
    load.add_argument("--root", required=True)
    load.add_argument("--dsn-file", required=True)
    load.add_argument("--manifest-sha256", required=True)
    args = vars(parser.parse_args())
    operation = args.pop("operation")
    print(json.dumps(backup(**args) if operation == "backup" else restore(**args)))


if __name__ == "__main__":
    main()
