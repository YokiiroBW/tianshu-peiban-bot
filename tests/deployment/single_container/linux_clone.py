"""Root-reviewed offline-clone operations; no admin, resource creation or activation.

No action on import. The coordinator provides an existing pg2_lifecycle binding,
its state, the explicit verified cold snapshot and PG17 client paths. The caller
owns PG cleanup through that ORIGINAL lifecycle. JSON output never contains URLs,
passwords, source rows, or private configuration contents.
"""

from __future__ import annotations

import argparse
import ast
import base64
from contextlib import closing
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit


RECOVERY_SHA256 = "06bdc127ee205b91f662de392961ea8ac0e55d20067018f714b92cfb1c766ffc"
PROBE_SHA256 = "b663d467a9ab0c793bf4b3636919a65307d695936b48d08e82a1512df9fbfb47"
SERVICES = {"platform", "gateway", "memory", "knowledge", "companion", "actor-memory"}
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")


class CloneError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise CloneError(code)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def relative(root, name):
    part = PurePosixPath(name)
    require(
        isinstance(name, str) and not part.is_absolute() and ".." not in part.parts,
        "snapshot_relative_path_required",
    )
    result = (root / name).resolve()
    require(result.is_relative_to(root), "snapshot_path_escape")
    return result


def load_recovery(bundle):
    """Load the exact reviewed actor-aware recovery sources, without their CLI."""
    bundle = Path(bundle)
    require(sha(bundle) == RECOVERY_SHA256, "reviewed_recovery_bundle_required")
    document = read(bundle)
    names = (
        "__init__",
        "safety",
        "postgres_backend",
        "postgres_container",
        "resident_retention",
        "resident_snapshot",
    )
    package_name = "_ts353_recovery_" + RECOVERY_SHA256[:12]
    if package_name + ".resident_snapshot" in sys.modules:
        return {name: sys.modules[package_name + "." + name] for name in names[1:]}
    package = ModuleType(package_name)
    package.__path__ = []
    package.__package__ = package_name
    package.__file__ = str(bundle) + ":ops/recovery/__init__.py"
    sys.modules[package_name] = package
    modules = {}
    for name in names[1:]:
        module = ModuleType(package_name + "." + name)
        module.__package__ = package_name
        module.__file__ = str(bundle) + ":ops/recovery/" + name + ".py"
        modules[name] = module
        sys.modules[module.__name__] = module
        setattr(package, name, module)
    try:
        for name in names:
            entry = document["files"]["ops/recovery/" + name + ".py"]
            raw = base64.b64decode(entry["base64"], validate=True)
            require(
                hashlib.sha256(raw).hexdigest() == entry["sha256"],
                "recovery_source_changed",
            )
            module = package if name == "__init__" else modules[name]
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
    except BaseException:
        for name in [package_name, *(m.__name__ for m in modules.values())]:
            sys.modules.pop(name, None)
        raise
    return modules


def owned_binding(binding_path, state_path):
    """Validate only supplied scratch files. Never follow admin_binding_file."""
    binding, state = read(binding_path), read(state_path)
    require(
        binding.get("purpose") == "group-modes-isolated-pg2"
        and binding.get("production") is False,
        "original_pg2_binding_required",
    )
    identity = binding.get("isolation_id", "")
    require(bool(HEX32.fullmatch(identity)), "pg2_uuid_required")
    require(
        state.get("version") == 1
        and state.get("purpose") == "isolated-group-modes-release-pg2"
        and state.get("status") == "prepared_not_run",
        "prepared_pg2_state_required",
    )
    resources = state.get("resources")
    require(
        isinstance(resources, list) and len(resources) == 1, "one_owned_pg2_required"
    )
    resource = resources[0]
    token = state.get("token", "")
    role, database = "ts325_role_" + identity, "ts325_" + identity
    require(
        bool(re.fullmatch(r"[0-9a-f]{64}", token))
        and resource.get("kind") == "pg2"
        and resource.get("isolation_id") == identity
        and resource.get("role") == role
        and resource.get("database") == database
        and resource.get("comment") == "tianshu:ts325:" + token + ":pg2:" + role
        and resource.get("role_created") is True
        and resource.get("database_created") is True
        and resource.get("cleaned") is False,
        "owned_pg2_state_mismatch",
    )
    url = binding.get("postgresql_url", "")
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    require(
        parsed.scheme in {"postgresql", "postgres"}
        and bool(parsed.hostname)
        and unquote(parsed.username or "") == role
        and unquote(parsed.path.lstrip("/")) == database
        and not parsed.fragment
        and len({key for key, _ in query}) == len(query)
        and all(
            key in {"host", "port", "sslmode", "connect_timeout", "application_name"}
            for key, _ in query
        ),
        "scratch_url_identity_mismatch",
    )
    return binding, resource


IDENTITY_SQL = (
    "SELECT json_build_array(current_database(),current_user,session_user,"
    "pg_get_userbyid(d.datdba),shobj_description(d.oid,'pg_database'),"
    "r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,"
    "r.rolcanlogin,shobj_description(r.oid,'pg_authid')) "
    "FROM pg_database d JOIN pg_roles r ON r.rolname=current_user "
    "WHERE d.datname=current_database()"
)


def verify_identity(tools, environment, resource):
    expected = [
        resource["database"],
        resource["role"],
        resource["role"],
        resource["role"],
        resource["comment"],
        False,
        False,
        False,
        False,
        False,
        True,
        resource["comment"],
    ]
    require(
        tools.query(environment, IDENTITY_SQL) == [expected],
        "live_owned_pg2_identity_mismatch",
    )


def manifest(snapshot):
    snapshot = Path(snapshot).resolve(strict=True)
    value, receipt = read(snapshot / "manifest.json"), read(snapshot / "receipt.json")
    require(
        value.get("schema_version") == "resident-cold-snapshot/1"
        and receipt.get("status") == "offline_restore_verified"
        and receipt.get("manifest_sha256") == sha(snapshot / "manifest.json")
        and receipt.get("restored_disabled") is True
        and receipt.get("authority_unchanged") is True
        and receipt.get("postgresql_databases_verified") == 25
        and receipt.get("postgresql_services_verified") == 6
        and receipt.get("sqlite_databases_verified") == 39
        and receipt.get("memory_guards_verified") == 3,
        "verified_today_snapshot_required",
    )
    rows = value.get("postgresql", [])
    require(
        len(rows) == 6
        and {row["service"] for row in rows} == SERVICES
        and sum(len(row["schemas"]) for row in rows) == 25,
        "six_services_twenty_five_logical_domains_required",
    )
    all_schemas = [schema for row in rows for schema in row["schemas"].values()]
    require(
        len(set(all_schemas)) == 25
        and all(IDENTIFIER.fullmatch(s) for s in all_schemas),
        "disjoint_original_schemas_required",
    )
    guards = set()
    for row in rows:
        archive = relative(snapshot, row["archive"])
        require(
            archive.is_file()
            and sha(archive) == row["sha256"]
            and archive.stat().st_size == row["bytes"]
            and row["native_restore_verified"] is True,
            "snapshot_pg_archive_changed",
        )
        source = read(relative(snapshot / "backup", row["binding"]))
        require(
            source.get("service") == row["service"]
            and source.get("backend") == "postgresql"
            and source.get("state") == "active"
            and source.get("databases") == row["schemas"]
            and source.get("installation_id") == row["installation_id"],
            "snapshot_binding_mismatch",
        )
        guards.update(row.get("memory_guards", []))
    require(len(guards) == 3, "three_pg_guards_required")
    for guard in guards:
        entry = value["inventory"]["entries"][guard]
        require(
            entry["kind"] == "file"
            and sha(relative(snapshot / "backup", guard)) == entry["sha256"],
            "original_guard_bytes_changed",
        )
    return snapshot, value


def validate_stage(stage, snapshot, value):
    """Protect before even creating clone/; reuse the original mount boundaries."""
    require(
        isinstance(value.get("authority_root"), str)
        and Path(value["authority_root"]).is_absolute(),
        "original_authority_location_required",
    )
    protected = [snapshot, Path(value["authority_root"]).resolve()]
    for owner in value["owners"]:
        for mount in owner.get("mounts", []):
            source = Path(mount.get("Source", ""))
            if source.is_absolute():
                protected.append(source.resolve())
    require(
        all(
            stage != path
            and not stage.is_relative_to(path)
            and not path.is_relative_to(stage)
            for path in protected
        ),
        "independent_clone_stage_required",
    )


def context(stage, binding_path, state_path, snapshot, recovery_bundle, seconds):
    stage = Path(stage).resolve(strict=True)
    modules = load_recovery(recovery_bundle)
    backend, resident = modules["postgres_backend"], modules["resident_snapshot"]
    snapshot, value = manifest(snapshot)
    binding, resource = owned_binding(binding_path, state_path)
    validate_stage(stage, snapshot, value)
    paths = backend.deployment_tools(stage)
    require(paths is not None, "explicit_pg17_tools_required")
    tools = backend.Tools(paths, resident.Deadline(seconds))
    environment = backend.connection_environment(binding["postgresql_url"])
    readonly = backend.connection_environment(binding["postgresql_url"], read_only=True)
    for name in ("psql", "pg_restore", "pg_dump"):
        version = tools.run(name, ["--version"], readonly).decode("ascii").strip()
        require(
            bool(re.search(r"\(PostgreSQL\) 17\.\d+", version)), "pg17_client_required"
        )
    verify_identity(tools, readonly, resource)
    return SimpleNamespace(
        stage=stage,
        root=stage / "clone/root",
        modules=modules,
        backend=backend,
        resident=resident,
        snapshot=snapshot,
        manifest=value,
        binding=binding,
        resource=resource,
        tools=tools,
        environment=environment,
        readonly=readonly,
        binding_sha256=sha(binding_path),
        state_sha256=sha(state_path),
        paths=paths,
    )


def verify_files(ctx):
    """Every frozen file stays exact, except the original allowed binding epoch."""
    current = ctx.resident.inventory(ctx.root, ctx.tools.check)
    expected = ctx.manifest["inventory"]
    require(
        set(current["entries"]) == set(expected["entries"]), "clone_file_set_changed"
    )
    changed = []
    epochs = set()
    for name, old in expected["entries"].items():
        new = current["entries"][name]
        if old == new:
            continue
        require(
            old["kind"] == new["kind"] == "file"
            and re.fullmatch(
                r"\.tianshu-storage-[a-z][a-z0-9_-]*\.json", Path(name).name
            ),
            "clone_frozen_file_changed",
        )
        before, after = (
            read(relative(ctx.snapshot / "backup", name)),
            read(relative(ctx.root, name)),
        )
        before_epoch, after_epoch = before.pop("epoch"), after.pop("epoch")
        require(
            before == after
            and bool(HEX32.fullmatch(after_epoch))
            and before_epoch != after_epoch
            and all(old[key] == new[key] for key in ("mode", "uid", "gid")),
            "clone_binding_changed_beyond_epoch",
        )
        changed.append(name)
        epochs.add(after_epoch)
    expected_bindings = [
        name
        for name, item in expected["entries"].items()
        if item["kind"] == "file"
        and re.fullmatch(r"\.tianshu-storage-[a-z][a-z0-9_-]*\.json", Path(name).name)
    ]
    require(
        set(changed) == set(expected_bindings) and len(epochs) == 1,
        "clone_epoch_not_advanced_once",
    )
    return current, {
        "frozen_files_unchanged": True,
        "binding_epochs_advanced": len(changed),
        "cache_generation": epochs.pop(),
    }


def verify_pg(ctx):
    results = []
    guards = set()
    for row in ctx.manifest["postgresql"]:
        for logical, schema in row["schemas"].items():
            bound = ctx.tools.query(
                ctx.readonly,
                "SELECT json_build_array(installation_id,logical_name,state) FROM "
                + ctx.backend.sql_identifier(schema)
                + ".__tianshu_storage_binding WHERE singleton=1",
            )
            require(
                bound == [[row["installation_id"], logical, "ready"]],
                "restored_domain_binding_mismatch",
            )
        fingerprint = ctx.tools.fingerprint(
            ctx.readonly, sorted(row["schemas"].values())
        )
        require(fingerprint == row["logical_rows"], "restored_pg_rows_mismatch")
        row_guards = row.get("memory_guards", [])
        if row_guards:
            require(
                len(row_guards) == 1
                and row["service"] in {"memory", "knowledge", "actor-memory"},
                "unexpected_guard_owner",
            )
            schema = ctx.backend.sql_identifier(row["schemas"]["main"])
            actor = row["service"] == "actor-memory"
            metadata = ctx.tools.query(
                ctx.readonly,
                "SELECT json_object_agg(key,value) FROM "
                + schema
                + (
                    ".actor_metadata WHERE key IN ('schema','instance','revision','recovery')"
                    if actor
                    else ".metadata WHERE key IN ('schema','source_instance','source_revision','source_recovery')"
                ),
            )[0]
            if actor:
                ctx.backend.verify_actor_guard(
                    ctx.root, {"guard": row_guards[0]}, metadata
                )
            else:
                head = {
                    "schema": 3,
                    "instance": metadata["source_instance"],
                    "revision": int(metadata["source_revision"]),
                    "recovery": metadata["source_recovery"],
                }
                require(
                    metadata.get("schema") == "3"
                    and head["revision"] >= 0
                    and read(relative(ctx.root, row_guards[0])) == head,
                    "restored_memory_guard_mismatch",
                )
            guards.update(row_guards)
        tables = [item for item in fingerprint.values() if item["kind"] != "S"]
        results.append(
            {
                "service": row["service"],
                "logical_domains": len(row["schemas"]),
                "tables": len(tables),
                "nonempty_tables": sum(item["rows"] > 0 for item in tables),
                "rows_read_and_hashed": sum(item["rows"] for item in tables),
                "fingerprint_matches_original_manifest": True,
            }
        )
    require(len(guards) == 3, "restored_three_guards_not_verified")
    return results


def installed_native_probe(ctx):
    """Storage wheel's public readonly readiness of all 25 existing domains."""
    from tianshu_storage import StorageOptions
    from tianshu_storage.runtime import StorageRuntime

    module = importlib.import_module("tianshu_storage.runtime")
    require(
        Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
        "installed_storage_wheel_required",
    )
    result = []
    for row in ctx.manifest["postgresql"]:
        marker = relative(ctx.root, row["binding"])
        state = read(marker)
        options = StorageOptions(
            postgresql_url=ctx.binding["postgresql_url"],
            redis_url="",
            namespace=state["namespace"],
        )
        probe = StorageRuntime.inspect(
            options, service=row["service"], state_directory=marker.parent
        )
        require(
            probe
            == {
                "backend": "postgresql",
                "state": "active",
                "postgresql": "ok",
                "databases": sorted(row["schemas"]),
            },
            "native_domain_readiness_failed",
        )
        result.append(
            {
                "service": row["service"],
                "logical_domains": len(probe["databases"]),
                "existing_binding_postgresql": "ok",
            }
        )
    return result


def verify_contents(ctx):
    current, files = verify_files(ctx)
    sqlite_scratch = ctx.stage / "sqlite-verification" / os.urandom(16).hex()
    sqlite_scratch.mkdir(mode=0o700, parents=True)
    # All three guards are PG-backed in this snapshot. Verify those from native
    # PG metadata below, rather than mislabelling a SQLite-only check as a guard check.
    count, sqlite_guards = ctx.resident.verify_sqlite(
        ctx.root, current, sqlite_scratch, ctx.tools.check
    )
    require(count == 39 and sqlite_guards == 0, "thirty_nine_sqlite_databases_required")
    pg = verify_pg(ctx)
    native = installed_native_probe(ctx)
    verify_identity(ctx.tools, ctx.readonly, ctx.resource)
    return {
        **files,
        "sqlite_databases_verified": count,
        "postgresql": pg,
        "guards_verified": 3,
        "native_domain_probe": native,
        "product_app_readiness_claimed": False,
    }


def prepare_clone(
    stage, new_owned_binding, state, *, snapshot, recovery_bundle, seconds=1800
):
    ctx = context(stage, new_owned_binding, state, snapshot, recovery_bundle, seconds)
    folder = ctx.stage / "clone"
    require(not folder.exists(), "new_clone_directory_required")
    # Empty means no non-system schema or relation, including user relations in public.
    empty = ctx.tools.query(
        ctx.readonly,
        "SELECT json_build_array((SELECT count(*) FROM pg_namespace WHERE nspname NOT IN "
        "('public','information_schema') AND nspname NOT LIKE 'pg\\_%' ESCAPE '\\'),"
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('information_schema') AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'))",
    )
    require(empty == [[0, 0]], "fresh_empty_owned_pg2_required")
    folder.mkdir(mode=0o700)
    prepared = ctx.backend.prepare_restored_files(ctx.snapshot, ctx.root)
    # No automatic retry: a partial restore requires original cleanup/new UUID.
    for row in ctx.manifest["postgresql"]:
        verify_identity(ctx.tools, ctx.readonly, ctx.resource)
        ctx.tools.run(
            "pg_restore",
            [
                "--no-password",
                "--dbname=" + ctx.resource["database"],
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                str(relative(ctx.snapshot, row["archive"])),
            ],
            ctx.environment,
            capture=False,
        )
    proof = verify_contents(ctx)
    receipt = {
        "version": 1,
        "status": "clone_restored_disabled_verified_before_product_migration",
        "snapshot_manifest_sha256": sha(ctx.snapshot / "manifest.json"),
        "recovery_bundle_sha256": RECOVERY_SHA256,
        "isolation_id": ctx.binding["isolation_id"],
        "binding_input_sha256": ctx.binding_sha256,
        "state_input_sha256": ctx.state_sha256,
        "tools_config_sha256": sha(ctx.stage / "config/storage-backup.json"),
        "files_prepared": prepared,
        **proof,
        "database_created_or_dropped": False,
        "production_restored": False,
        "worker_started": False,
        "redis_used": False,
        "external_controls_copied": False,
        "external_controls_require_separate_explicit_mapping": len(
            ctx.manifest.get("external_controls", {})
        ),
        "external_media_mounted": False,
    }
    write_new(folder / "receipt.json", receipt)
    return receipt


def verify_clone(
    stage, new_owned_binding, state, *, snapshot, recovery_bundle, seconds=1800
):
    ctx = context(stage, new_owned_binding, state, snapshot, recovery_bundle, seconds)
    receipt = read(ctx.stage / "clone/receipt.json")
    require(
        receipt.get("status")
        == "clone_restored_disabled_verified_before_product_migration"
        and receipt.get("snapshot_manifest_sha256")
        == sha(ctx.snapshot / "manifest.json")
        and receipt.get("binding_input_sha256") == ctx.binding_sha256
        and receipt.get("state_input_sha256") == ctx.state_sha256
        and receipt.get("tools_config_sha256")
        == sha(ctx.stage / "config/storage-backup.json"),
        "clone_receipt_inputs_changed",
    )
    return {"status": "clone_verified_before_product_migration", **verify_contents(ctx)}


def migrate_platform_clone(
    stage,
    new_owned_binding,
    state,
    *,
    snapshot,
    recovery_bundle,
    settings,
    readonly_probe,
    observation_sha256,
    seconds=1800,
):
    """Original narrow group DDL, twice; invoke BEFORE app/worker startup only.

    Settings must already be root's cloned/rebased settings with scratch URL and
    Redis blank. Global process environment is never substituted. The initial
    full fingerprint check precedes this additive product migration.
    """
    verify_clone(
        stage,
        new_owned_binding,
        state,
        snapshot=snapshot,
        recovery_bundle=recovery_bundle,
        seconds=seconds,
    )
    ctx = context(stage, new_owned_binding, state, snapshot, recovery_bundle, seconds)
    from services.platform import backends
    from services.platform.bot_observation import BotObservation
    from tianshu_storage import StorageOptions
    from tianshu_storage.runtime import StorageRuntime, state_filename

    config = read(settings)
    options = StorageOptions.from_settings(config, environ={})
    require(
        options.postgresql_url == ctx.binding["postgresql_url"]
        and options.redis_url == "",
        "clone_settings_scratch_pg_and_empty_redis_required",
    )
    directory = Path(config["database_path"]).resolve().parent
    require(
        directory.is_relative_to(ctx.root), "clone_platform_state_directory_required"
    )
    marker = directory / state_filename("platform")
    platform = next(
        row for row in ctx.manifest["postgresql"] if row["service"] == "platform"
    )
    require(
        marker == relative(ctx.root, platform["binding"]),
        "original_platform_marker_required",
    )
    marker_before = marker.read_bytes()
    anchor = config["database_path"] + ".bot-observations.sqlite"
    require(sha(readonly_probe) == PROBE_SHA256, "original_readonly_probe_required")
    probe = {"__name__": "ts353_original_readonly_probe"}
    exec(compile(Path(readonly_probe).read_bytes(), str(readonly_probe), "exec"), probe)
    origin = Path(
        importlib.import_module("services.platform.bot_observation").__file__
    ).resolve()
    require(
        origin.is_relative_to(Path(sys.prefix).resolve())
        and sha(origin) == observation_sha256,
        "pinned_installed_platform_wheel_required",
    )
    tree = ast.parse(origin.read_bytes())
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "BotObservation"
    )
    constructor = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    ddl = [
        n.args[0].value
        for n in ast.walk(constructor)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "executescript"
        and len(n.args) == 1
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    ]
    require(len(ddl) == 1, "accepted_constructor_ddl_literal_required")

    def readonly():
        with closing(
            StorageRuntime.open_existing(
                options,
                service="platform",
                state_directory=directory,
                name="bot_observations",
                path=anchor,
                dialect=backends.dialect,
                timeout=5,
            )
        ) as db:
            db.native("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return probe["snapshot"](db)

    before = readonly()
    proofs = []
    for _ in range(2):
        verify_identity(ctx.tools, ctx.readonly, ctx.resource)
        raw = StorageRuntime(options, service="platform", state_directory=directory)
        runtime = backends.PlatformRuntime(raw)
        try:
            raw.start()
            database = backends.database(runtime, "bot_observations", anchor)
            with closing(database.connect(timeout=5)) as db:
                db.executescript(ddl[0])
                BotObservation._migrate_reply_generations(
                    SimpleNamespace(database=database), db
                )
            # Never finalize a one-domain registry as the seventeen-domain owner.
        finally:
            runtime.close()
        after = readonly()
        proofs.append(probe["compare"](before, after))
        before = after
        require(
            marker.read_bytes() == marker_before, "migration_changed_platform_marker"
        )
    result = {
        "status": "clone_platform_group_migration_verified_idempotent",
        "passes": proofs,
        "historical_rows_unchanged": True,
        "binding_unchanged": True,
        "native_domain_probe": installed_native_probe(ctx),
        "registry_finalized": False,
        "worker_started": False,
        "product_migration_method_used": True,
        "observation_source_sha256": observation_sha256,
    }
    write_new(ctx.stage / "clone/platform-migration.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "migrate-platform"))
    for name in ("stage", "binding", "state", "snapshot", "recovery-bundle"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--readonly-probe", type=Path)
    parser.add_argument("--observation-sha256")
    args = parser.parse_args(argv)
    options = {
        "snapshot": args.snapshot,
        "recovery_bundle": args.recovery_bundle,
        "seconds": args.seconds,
    }
    try:
        if args.action == "migrate-platform":
            require(
                args.settings and args.readonly_probe and args.observation_sha256,
                "migration_inputs_required",
            )
            result = migrate_platform_clone(
                args.stage,
                args.binding,
                args.state,
                **options,
                settings=args.settings,
                readonly_probe=args.readonly_probe,
                observation_sha256=args.observation_sha256,
            )
        else:
            function = prepare_clone if args.action == "prepare" else verify_clone
            result = function(args.stage, args.binding, args.state, **options)
    except Exception as exc:
        # Foreign exceptions can embed credentials or database contents. No traceback.
        reason = (
            str(exc) if isinstance(exc, CloneError) else "clone_operation_not_verified"
        )
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": reason,
                    "exception_type": type(exc).__name__,
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
