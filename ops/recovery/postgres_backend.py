"""Native PG exports and disabled restore checks for resident cold snapshots.

Only public binding metadata enters receipts. Connection URLs are read from named
private environment variables and passed to child tools through their environment.
The original service and its media library are never restored, altered or started.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import uuid4

from .safety import (
    RecoveryError,
    canonical,
    file_hash,
    read_json,
    require,
    sync_tree,
    write_new,
)

IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,95}\Z")
PG_ENV = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
    "application_name": "PGAPPNAME",
    "sslmode": "PGSSLMODE",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcrl": "PGSSLCRL",
    "sslcrldir": "PGSSLCRLDIR",
    "sslsni": "PGSSLSNI",
    "channel_binding": "PGCHANNELBINDING",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
    "gssencmode": "PGGSSENCMODE",
    "krbsrvname": "PGKRBSRVNAME",
    "require_auth": "PGREQUIREAUTH",
    "load_balance_hosts": "PGLOADBALANCEHOSTS",
    "sslnegotiation": "PGSSLNEGOTIATION",
    "passfile": "PGPASSFILE",
}


def connection_environment(url, *, database=None, read_only=False):
    """Same URI facts as libpq; never put passwords or complete URLs in argv."""
    parsed = urlsplit(url)
    require(
        parsed.scheme in {"postgresql", "postgres"}, "postgresql_backup_url_invalid"
    )
    values = {
        "host": parsed.hostname or "",
        "port": str(parsed.port or 5432),
        "dbname": unquote(parsed.path.lstrip("/")),
    }
    if parsed.username is not None:
        values["user"] = unquote(parsed.username)
    if parsed.password is not None:
        values["password"] = unquote(parsed.password)
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    require(
        len({key for key, _ in query}) == len(query), "postgresql_backup_url_invalid"
    )
    values.update(query)
    require(set(values) <= PG_ENV.keys(), "postgresql_backup_parameter_unsupported")
    require(
        all(
            isinstance(value, str) and "\x00" not in value for value in values.values()
        ),
        "postgresql_backup_url_invalid",
    )
    if database is not None:
        require(bool(IDENTIFIER.fullmatch(database)), "postgresql_scratch_name_invalid")
        values["dbname"] = database
    require(bool(values.get("dbname")), "postgresql_backup_database_required")
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PG")
    }
    environment.update({PG_ENV[key]: value for key, value in values.items()})
    environment.update(
        PGCLIENTENCODING="UTF8", PGCONNECT_TIMEOUT="5", PGTZ="UTC", PGDATESTYLE="ISO"
    )
    options = values.get("options", "") + " -c extra_float_digits=3"
    if read_only:
        options += " -c default_transaction_read_only=on"
    environment["PGOPTIONS"] = options
    return environment


def sql_identifier(value):
    require(
        isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)),
        "postgresql_binding_identifier_invalid",
    )
    return '"' + value + '"'


class Tools:
    def __init__(self, paths, check):
        require(
            isinstance(paths, dict) and set(paths) == {"psql", "pg_dump", "pg_restore"},
            "postgresql_backup_tools_required",
        )
        require(
            all(isinstance(value, str) and value for value in paths.values()),
            "postgresql_backup_tools_required",
        )
        self.paths, self.check = paths, check

    def run(self, tool, arguments, environment, *, capture=True):
        self.check()
        result = subprocess.run(
            [self.paths[tool], *arguments],
            check=False,
            env=environment,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1, self.check.deadline - time.monotonic()),
        )
        require(result.returncode == 0, "postgresql_" + tool + "_failed")
        self.check()
        return result.stdout if capture else b""

    def query(self, environment, statement):
        raw = self.run(
            "psql",
            [
                "-X",
                "--no-password",
                "--no-align",
                "--tuples-only",
                "--set=ON_ERROR_STOP=1",
                "--command",
                statement,
            ],
            environment,
        )
        return [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]

    def statement(self, environment, statement):
        self.run(
            "psql",
            ["-X", "--no-password", "--set=ON_ERROR_STOP=1", "--command", statement],
            environment,
            capture=False,
        )

    def fingerprint(self, environment, schemas):
        literals = ",".join("'" + schema + "'" for schema in schemas)
        names = self.query(
            environment,
            "SELECT json_build_array(n.nspname,c.relname,c.relkind) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            f"WHERE n.nspname IN ({literals}) AND c.relkind IN ('r','p','S') "
            "ORDER BY n.nspname,c.relname",
        )
        result = {}
        for schema, table, kind in names:
            qualified = sql_identifier(schema) + "." + sql_identifier(table)
            statement = (
                f"SELECT json_build_array(last_value,is_called)::text FROM {qualified}"
                if kind == "S"
                else f'SELECT row_to_json(t)::text FROM {qualified} t ORDER BY row_to_json(t)::text COLLATE "C"'
            )
            self.check()
            with subprocess.Popen(
                [
                    self.paths["psql"],
                    "-X",
                    "--no-password",
                    "--no-align",
                    "--tuples-only",
                    "--set=ON_ERROR_STOP=1",
                    "--command",
                    statement,
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as process:
                # Read on a helper thread so the same operation deadline also bounds
                # a connection that stops returning rows while a pipe is open.
                def consume():
                    digest, count = hashlib.sha256(), 0
                    for line in process.stdout:
                        self.check()
                        digest.update(line.rstrip(b"\r\n") + b"\n")
                        count += 1
                    return count, digest.hexdigest()

                executor = ThreadPoolExecutor(max_workers=1)
                pending = executor.submit(consume)
                try:
                    count, digest = pending.result(
                        timeout=max(1, self.check.deadline - time.monotonic())
                    )
                    code = process.wait(
                        timeout=max(1, self.check.deadline - time.monotonic())
                    )
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                finally:
                    executor.shutdown(wait=True)
            require(code == 0, "postgresql_fingerprint_failed")
            result[schema + "." + table] = {
                "kind": kind,
                "rows": count,
                "sha256": digest,
            }
        return result


def binding_files(root, recorded):
    bindings = []
    for name, item in recorded["entries"].items():
        path = Path(name)
        if item["kind"] != "file" or not re.fullmatch(
            r"\.tianshu-storage-[a-z][a-z0-9_-]*\.json", path.name
        ):
            continue
        state = read_json(root / name)
        require(
            isinstance(state, dict)
            and state.get("version") == 1
            and state.get("backend") in {"sqlite", "postgresql"},
            "storage_binding_invalid",
        )
        if state["backend"] == "postgresql":
            require(
                state.get("state") == "active"
                and isinstance(state.get("databases"), dict)
                and bool(state["databases"]),
                "postgresql_activation_incomplete",
            )
            service = state.get("service")
            require(
                isinstance(service, str)
                and bool(re.fullmatch("[a-z][a-z0-9_-]{0,63}", service))
                and path.name == ".tianshu-storage-" + service + ".json"
                and bool(
                    re.fullmatch("[0-9a-f]{32}", state.get("installation_id", ""))
                ),
                "storage_binding_invalid",
            )
            for schema in state["databases"].values():
                sql_identifier(schema)
            bindings.append((name, state))
    return bindings


def required_file_controls(root, owners):
    """Include configured Knowledge files even when an external data bind is excluded.

    File roots inside the authority are already inventoried. Only the exact configured
    root is copied from an existing service bind; media/log mounts stay excluded.
    """
    controls = []
    for service in ("memory", "knowledge"):
        path = root / "config" / service / "settings.json"
        if not path.is_file():
            continue
        config = read_json(path)
        directory = config.get("knowledge_files_directory")
        if not directory:
            database = config.get("database_path")
            if not database:
                continue
            directory = database + ".files"
        require(isinstance(directory, str), "knowledge_files_root_invalid")
        container = PurePosixPath(directory)
        require(
            container.is_absolute() and ".." not in container.parts,
            "knowledge_files_root_invalid",
        )
        candidates = []
        for owner in owners:
            if owner["service"] != service:
                continue
            for mount in owner["mounts"]:
                destination = PurePosixPath(mount.get("Destination", ""))
                if mount.get("Type") == "bind" and container.is_relative_to(
                    destination
                ):
                    host = Path(mount["Source"]) / str(
                        container.relative_to(destination)
                    )
                    candidates.append((len(destination.parts), host))
        require(bool(candidates), "knowledge_files_mount_missing")
        host = max(candidates, key=lambda row: row[0])[1]
        # New services can have no files yet. After files exist, do not omit them.
        if host.exists():
            from .safety import safe_path

            host = safe_path(host)
            require(host.is_dir(), "knowledge_files_root_invalid")
            if not host.is_relative_to(root):
                require(not root.is_relative_to(host), "knowledge_files_root_invalid")
                controls.append(host)
    return list(dict.fromkeys(controls))


def actor_memory_domain(root, owners):
    """Locate the optional actor domain through its real Memory owner mounts."""
    from .safety import safe_path

    config_path = root / "config/memory/settings.json"
    if not config_path.is_file():
        return None
    config = read_json(config_path)
    if "actor_memory" not in config:
        return None
    section = config["actor_memory"]
    require(isinstance(section, dict), "actor_memory_configuration_invalid")
    owner = [row for row in owners if row["service"] == "memory"]
    require(len(owner) == 1, "actor_memory_owner_missing")

    def locate(value):
        require(isinstance(value, str), "actor_memory_path_invalid")
        container = PurePosixPath(value)
        require(container.is_absolute() and ".." not in container.parts
                and str(container) == value, "actor_memory_path_invalid")
        candidates = []
        for mount in owner[0]["mounts"]:
            destination = PurePosixPath(mount.get("Destination", ""))
            if (mount.get("Type") == "bind" and destination.is_absolute()
                    and container.is_relative_to(destination)):
                host = Path(mount["Source"]).joinpath(*container.relative_to(destination).parts)
                candidates.append((len(destination.parts), host, mount.get("RW")))
        require(bool(candidates), "actor_memory_mount_missing")
        longest = max(row[0] for row in candidates)
        selected = [row for row in candidates if row[0] == longest]
        require(len(selected) == 1 and selected[0][2] is True,
                "actor_memory_mount_mismatch")
        host = safe_path(selected[0][1], exists=False)
        require(host != root and host.is_relative_to(root),
                "actor_memory_outside_snapshot_authority")
        return host

    database_name = section.get("database_path")
    require(isinstance(database_name, str), "actor_memory_path_invalid")
    database = locate(database_name)
    state_directory = locate(section.get("state_directory", database_name + ".storage"))
    guard = locate(section.get("guard_path", database_name + ".guard.json"))
    user_name = config.get("database_path")
    require(isinstance(user_name, str), "actor_memory_configuration_invalid")
    user_database = locate(user_name)
    user_guard = locate(config.get("source_sync", {}).get("recovery_path", user_name + ".source-guard.json"))
    require(database not in {user_database, user_guard}
            and guard not in {database, user_database, user_guard}
            and state_directory != locate(user_name + ".storage"),
            "actor_memory_user_domain_overlap")
    require(guard.is_file(), "actor_memory_guard_missing")
    connection = _deployment_connection(root, "memory")
    postgres = bool(connection and connection.get("postgresql_url"))
    binding = state_directory / ".tianshu-storage-actor-memory.json"
    if binding.exists():
        state = read_json(safe_path(binding))
        require(isinstance(state, dict) and state.get("version") == 1
                and state.get("service") == "actor-memory"
                and state.get("state") == "active"
                and isinstance(state.get("namespace"), str) and bool(state["namespace"])
                and bool(re.fullmatch("[0-9a-f]{32}", state.get("installation_id", "")))
                and bool(re.fullmatch("[0-9a-f]{32}", state.get("epoch", "")))
                and state.get("backend") == ("postgresql" if postgres else "sqlite"),
                "actor_memory_binding_mismatch")
        if postgres:
            require(set(state.get("databases", {})) == {"main"},
                    "actor_memory_binding_mismatch")
    else:
        # Pure SQLite without Redis does not persist a StorageRuntime binding.
        require(not postgres and not config.get("storage_backends", {}).get("redis_url"),
                "actor_memory_binding_missing")
    require(postgres or database.is_file(), "actor_memory_database_missing")
    return {"database": database.relative_to(root).as_posix(),
            "guard": guard.relative_to(root).as_posix(),
            "binding": binding.relative_to(root).as_posix() if binding.exists() else None,
            "backend": "postgresql" if postgres else "sqlite"}


def actor_checkpoint(metadata):
    require(isinstance(metadata, dict) and metadata.get("schema") == "2",
            "unsupported_actor_memory_schema")
    try:
        head = {"domain": "actor-memory", "schema": 2,
                "instance": metadata["instance"], "revision": int(metadata["revision"]),
                "recovery": metadata["recovery"]}
    except (KeyError, TypeError, ValueError):
        raise RecoveryError("invalid_actor_memory_metadata") from None
    require(head["revision"] >= 0 and isinstance(head["instance"], str) and bool(head["instance"])
            and isinstance(head["recovery"], str) and bool(head["recovery"]),
            "invalid_actor_memory_metadata")
    return head


def verify_actor_guard(root, domain, metadata):
    require(canonical(read_json(root / domain["guard"])) == canonical(actor_checkpoint(metadata)),
            "actor_memory_guard_mismatch")


def _deployment_connection(root, service):
    path = root / "config" / service / "settings.json"
    if not path.is_file():
        return None
    config = read_json(path)
    backend = config.get("storage_backends", {})
    require(isinstance(backend, dict), "postgresql_backup_configuration_required")
    url = backend.get("postgresql_url", "")
    env_file = root / "private" / (service + ".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("TIANSHU_POSTGRES_URL="):
                value = line.split("=", 1)[1]
                if len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                if value:
                    url = value
    result = {"postgresql_url": url}
    maintenance = "TIANSHU_" + service.upper().replace("-", "_") + "_BACKUP_POSTGRES_URL"
    if os.environ.get(maintenance):
        result["maintenance_url_env"] = maintenance
    elif os.environ.get("TIANSHU_BACKUP_POSTGRES_URL"):
        result["maintenance_url_env"] = "TIANSHU_BACKUP_POSTGRES_URL"
    return result


def deployment_connections(root, bindings):
    """Read only storage fields of private deployment inputs; return no public URL."""
    result = {}
    for _, state in bindings:
        service = state["service"]
        connection = _deployment_connection(root, "memory" if service == "actor-memory" else service)
        if connection is not None:
            result[service] = connection
    return result


def deployment_tools(root):
    """Optional private host wrappers; explicit snapshot paths retain precedence."""
    path = Path(root) / "config/storage-backup.json"
    if not path.exists():
        return None
    document = read_json(path)
    require(isinstance(document, dict), "postgresql_backup_tools_required")
    if "postgresql_tools" not in document:
        return None
    paths = document.get("postgresql_tools")
    require(
        isinstance(paths, dict)
        and set(paths) == {"psql", "pg_dump", "pg_restore"}
        and all(
            isinstance(value, str) and Path(value).is_absolute()
            for value in paths.values()
        ),
        "postgresql_backup_tools_required",
    )
    return paths


def export_and_verify(
    root,
    operation,
    recorded,
    connections,
    paths,
    check,
    *,
    memory_guards=(),
    actor_memory=None,
    restored_root=None,
    environ=None,
):
    """One native archive per active service, restored only to a fresh owned DB."""
    bindings = binding_files(root, recorded)
    actor_bindings = [(name, state) for name, state in bindings if state["service"] == "actor-memory"]
    if actor_memory and actor_memory["backend"] == "postgresql":
        require(len(actor_bindings) == 1 and actor_bindings[0][0] == actor_memory["binding"],
                "actor_memory_binding_missing")
        actor_state = actor_bindings[0][1]
        for _, state in bindings:
            if state["service"] == "memory":
                require(actor_state["namespace"] == state.get("namespace"),
                        "actor_memory_binding_mismatch")
            if state["service"] != "actor-memory":
                require(actor_state["installation_id"] != state["installation_id"]
                        and not set(actor_state["databases"].values()).intersection(state["databases"].values()),
                        "actor_memory_user_domain_overlap")
    else:
        require(not actor_bindings, "actor_memory_configuration_required")
    if not bindings:
        return [], set()
    if connections is None:
        connections = deployment_connections(root, bindings)
    require(isinstance(connections, dict), "postgresql_backup_configuration_required")
    if actor_bindings:
        owner_connection = connections.get("memory") or _deployment_connection(root, "memory")
        require(isinstance(owner_connection, dict), "postgresql_backup_configuration_required")
        require("actor-memory" not in connections or connections["actor-memory"] == owner_connection,
                "actor_memory_connection_mismatch")
        connections = {**connections, "actor-memory": owner_connection}
    environment = os.environ if environ is None else environ
    tools = Tools(
        (deployment_tools(root) if paths is None else paths)
        or {name: shutil.which(name) for name in ("psql", "pg_dump", "pg_restore")},
        check,
    )
    folder = operation / "postgresql"
    folder.mkdir(mode=0o700)
    results, matched_guards = [], set()
    for index, (name, state) in enumerate(bindings):
        service = state["service"]
        config = connections.get(service)
        require(
            isinstance(config, dict)
            and set(config) <= {"url_env", "postgresql_url", "maintenance_url_env"},
            "postgresql_backup_configuration_required",
        )
        source_url = config.get("postgresql_url", "")
        if config.get("url_env"):
            require(
                bool(ENV_NAME.fullmatch(config["url_env"])),
                "postgresql_backup_configuration_required",
            )
            source_url = environment.get(config["url_env"], "")
        maintenance_name = config.get("maintenance_url_env")
        require(
            maintenance_name is None or bool(ENV_NAME.fullmatch(maintenance_name)),
            "postgresql_backup_configuration_required",
        )
        maintenance_url = (
            environment.get(maintenance_name, "") if maintenance_name else source_url
        )
        source_env = connection_environment(source_url, read_only=True)
        maintenance_env = connection_environment(maintenance_url)
        schemas = sorted(state["databases"].values())
        for logical, schema in state["databases"].items():
            rows = tools.query(
                source_env,
                f"SELECT json_build_array(installation_id,logical_name,state) FROM {sql_identifier(schema)}.__tianshu_storage_binding WHERE singleton=1",
            )
            require(
                rows == [[state["installation_id"], logical, "ready"]],
                "postgresql_binding_mismatch",
            )
        before = tools.fingerprint(source_env, schemas)
        guards = []
        if service == "actor-memory":
            schema = sql_identifier(state["databases"]["main"])
            metadata = tools.query(source_env,
                f"SELECT json_object_agg(key,value) FROM {schema}.actor_metadata "
                "WHERE key IN ('schema','instance','revision','recovery')")[0]
            verify_actor_guard(root, actor_memory, metadata)
            guards = [actor_memory["guard"]]
            matched_guards.update(guards)
        if service in {"memory", "knowledge"} and "main" in state["databases"]:
            schema = sql_identifier(state["databases"]["main"])
            metadata = tools.query(
                source_env,
                f"SELECT json_object_agg(key,value) FROM {schema}.metadata "
                "WHERE key IN ('schema','source_instance','source_revision','source_recovery')",
            )[0]
            if metadata.get("schema") == "3":
                head = {
                    "schema": 3,
                    "instance": metadata["source_instance"],
                    "revision": int(metadata["source_revision"]),
                    "recovery": metadata["source_recovery"],
                }
                guards = [
                    guard
                    for guard in memory_guards
                    if canonical(read_json(root / guard)) == canonical(head)
                ]
                require(len(guards) == 1, "postgresql_memory_guard_mismatch")
                matched_guards.update(guards)
        archive = folder / (str(index) + ".dump")
        tools.run(
            "pg_dump",
            [
                "--no-password",
                "--format=custom",
                "--strict-names",
                "--file=" + str(archive),
                *("--schema=" + schema for schema in schemas),
            ],
            source_env,
            capture=False,
        )
        archive.chmod(0o600)
        # Never restore into a supplied existing database. The UUID name is generated
        # here; only this newly created scratch database is removed in finally.
        scratch = "ts_restore_" + uuid4().hex
        created = False
        try:
            tools.statement(
                maintenance_env,
                "CREATE DATABASE " + sql_identifier(scratch) + " TEMPLATE template0",
            )
            created = True
            restored_env = connection_environment(maintenance_url, database=scratch)
            tools.run(
                "pg_restore",
                [
                    "--no-password",
                    "--dbname=" + scratch,
                    "--no-owner",
                    "--no-privileges",
                    "--single-transaction",
                    "--exit-on-error",
                    str(archive),
                ],
                restored_env,
                capture=False,
            )
            restored = tools.fingerprint(
                connection_environment(
                    maintenance_url, database=scratch, read_only=True
                ),
                schemas,
            )
            if service == "actor-memory":
                metadata = tools.query(restored_env,
                    f"SELECT json_object_agg(key,value) FROM {schema}.actor_metadata "
                    "WHERE key IN ('schema','instance','revision','recovery')")[0]
                verify_actor_guard(restored_root or root, actor_memory, metadata)
            require(
                before == restored == tools.fingerprint(source_env, schemas),
                "postgresql_restore_mismatch",
            )
        finally:
            if created:
                # Use a fresh deadline for cleanup if the verification itself timed out.
                previous = check.deadline
                try:
                    check.deadline = max(previous, time.monotonic() + 10)
                    tools.statement(
                        maintenance_env, "DROP DATABASE " + sql_identifier(scratch)
                    )
                finally:
                    check.deadline = previous
        result = {
            "service": service,
            "binding": name,
            "schemas": state["databases"],
            "installation_id": state["installation_id"],
            "archive": archive.relative_to(operation).as_posix(),
            "sha256": file_hash(archive),
            "bytes": archive.stat().st_size,
            "logical_rows": before,
            "memory_guards": guards,
            "native_restore_verified": True,
            "scratch_removed": True,
        }
        results.append(result)
    write_new(
        folder / "manifest.json",
        canonical({"version": 1, "databases": results}) + b"\n",
    )
    sync_tree(folder)
    return results, matched_guards


def prepare_restored_files(snapshot_path, target):
    """Prepare a NEW disabled file tree and advance only disposable cache epochs."""
    from . import resident_retention, resident_snapshot
    from .safety import digest, safe_path

    snapshot_path = safe_path(Path(snapshot_path))
    target = safe_path(Path(target), exists=False)
    require(
        not target.exists()
        and target != snapshot_path
        and not target.is_relative_to(snapshot_path)
        and not snapshot_path.is_relative_to(target),
        "independent_new_operation_required",
    )
    manifest = read_json(snapshot_path / "manifest.json")
    receipt = read_json(snapshot_path / "receipt.json")
    require(
        manifest.get("schema_version") == "resident-cold-snapshot/1"
        and receipt.get("status") == "offline_restore_verified"
        and receipt.get("manifest_sha256")
        == file_hash(snapshot_path / "manifest.json"),
        "unverified_restore_input",
    )
    require(
        isinstance(manifest.get("authority_root"), str)
        and Path(manifest["authority_root"]).is_absolute(),
        "restore_authority_location_required",
    )
    authority = Path(manifest["authority_root"]).resolve()
    require(
        target != authority
        and not target.is_relative_to(authority)
        and not authority.is_relative_to(target),
        "restore_live_mount_overlap",
    )
    for owner in manifest["owners"]:
        for mount in owner.get("mounts", []):
            source = Path(mount.get("Source", ""))
            if source.is_absolute():
                require(
                    target != source
                    and not target.is_relative_to(source)
                    and not source.is_relative_to(target),
                    "restore_live_mount_overlap",
                )
    resident_retention.verify_kept_copies(snapshot_path, manifest, resident_snapshot)
    check = resident_snapshot.Deadline(600)
    resident_snapshot.copy_tree(
        snapshot_path / "backup", target, manifest["inventory"], check
    )
    generation = uuid4().hex
    changes = []
    for name, item in manifest["inventory"]["entries"].items():
        if item["kind"] != "file" or not re.fullmatch(
            r"\.tianshu-storage-[a-z][a-z0-9_-]*\.json", Path(name).name
        ):
            continue
        path = target / name
        state = read_json(path)
        require(
            state.get("version") == 1
            and bool(re.fullmatch("[0-9a-f]{32}", state.get("epoch", ""))),
            "storage_binding_invalid",
        )
        previous = state["epoch"]
        state["epoch"] = generation
        temporary = path.with_name(path.name + ".restore-" + uuid4().hex)
        write_new(temporary, canonical(state) + b"\n")
        temporary.chmod(item["mode"])
        if os.name == "posix":
            os.chown(temporary, item["uid"], item["gid"])
        os.replace(temporary, path)
        changes.append(
            {"binding": name, "previous_epoch": previous, "epoch": generation}
        )
    sync_tree(target)
    report = {
        "status": "files_prepared_disabled",
        "target": str(target),
        "source_manifest_sha256": digest(
            (snapshot_path / "manifest.json").read_bytes()
        ),
        "cache_generation": generation,
        "bindings": changes,
        "postgresql_archives": [
            str(snapshot_path / row["archive"])
            for row in manifest.get("postgresql", [])
        ],
        "database_restore_or_activation": False,
        "live_authority_changed": False,
    }
    return report


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_restored_files(args.snapshot, args.target)
    except (
        RecoveryError,
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        print(json.dumps({"status": "refused", "reason": "restore_files_not_verified"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
