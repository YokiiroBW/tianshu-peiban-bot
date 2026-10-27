"""Generate narrowly owned Docker PG17 clients for the original frozen Tools.

No action on import. Only the coordinator runs prepare/cleanup on Linux; this
module never reads a binding URL, admin file, creates a database, or runs SSH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from uuid import uuid4

import linux_clone as clone


IMAGE = "sha256:ca95f67ffb26270b70c5bc95dfaecc1a58bd08cab56ba6aeb051acb2163d2aab"
PG_HOST, PG_PORT = "192.168.31.210", "25008"
LABEL = "tianshu.clone-client"
QUERY_PREFIX = (
    "-X",
    "--no-password",
    "--no-align",
    "--tuples-only",
    "--set=ON_ERROR_STOP=1",
    "--command",
)
RESTORE_FLAGS = (
    "--no-password",
    "--no-owner",
    "--no-privileges",
    "--single-transaction",
    "--exit-on-error",
)


def sql_digest(statement):
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def allowed_queries(manifest):
    """Exact existing Tools/clone query shapes; this is not a SQL runner."""
    statements = {clone.IDENTITY_SQL, clone.EMPTY_DATABASE_SQL}

    def backend_quote(value):
        return '"' + value + '"'

    for row in manifest["postgresql"]:
        schemas = sorted(row["schemas"].values())
        literals = ",".join("'" + schema + "'" for schema in schemas)
        statements.add(
            "SELECT json_build_array(n.nspname,c.relname,c.relkind) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            f"WHERE n.nspname IN ({literals}) AND c.relkind IN ('r','p','S') "
            "ORDER BY n.nspname,c.relname"
        )
        for schema in schemas:
            clone.require(
                bool(clone.IDENTIFIER.fullmatch(schema)), "schema_identifier_invalid"
            )
            statements.add(
                "SELECT json_build_array(installation_id,logical_name,state) FROM "
                + backend_quote(schema)
                + ".__tianshu_storage_binding WHERE singleton=1"
            )
        for qualified, value in row["logical_rows"].items():
            parts = qualified.split(".")
            clone.require(
                len(parts) == 2
                and parts[0] in schemas
                and bool(clone.IDENTIFIER.fullmatch(parts[1])),
                "relation_identifier_invalid",
            )
            relation = ".".join(backend_quote(part) for part in parts)
            statements.add(
                "SELECT json_build_array(last_value,is_called)::text FROM " + relation
                if value["kind"] == "S"
                else "SELECT row_to_json(t)::text FROM "
                + relation
                + ' t ORDER BY row_to_json(t)::text COLLATE "C"'
            )
        if row["service"] in {"memory", "knowledge", "actor-memory"}:
            schema = backend_quote(row["schemas"]["main"])
            statements.add(
                "SELECT json_object_agg(key,value) FROM "
                + schema
                + (
                    ".actor_metadata WHERE key IN ('schema','instance','revision','recovery')"
                    if row["service"] == "actor-memory"
                    else ".metadata WHERE key IN ('schema','source_instance','source_revision','source_recovery')"
                )
            )
    return sorted(sql_digest(statement) for statement in statements)


def docker_prefix(docker):
    clone.require(PurePosixPath(docker).is_absolute(), "explicit_docker_path_required")
    return [docker, "--host", "unix:///var/run/docker.sock"]


def docker_environment():
    # Docker target/options are explicit; no caller DOCKER_CONTEXT/TLS/config or
    # libpq service/password-file variables can redirect a client.
    return {"PATH": os.defpath, "HOME": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def inspect_json(docker, arguments):
    result = subprocess.run(
        [*docker_prefix(docker), *arguments],
        env=docker_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    clone.require(result.returncode == 0, "local_docker_metadata_unavailable")
    return json.loads(result.stdout)


def inspect_runtime(docker, network):
    clone.require(
        isinstance(network, str)
        and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", network)),
        "explicit_existing_network_required",
    )
    image = inspect_json(
        docker, ["image", "inspect", "--format", "{{json .Id}}", IMAGE]
    )
    clone.require(image == IMAGE, "existing_fixed_pg17_image_required")
    value = inspect_json(
        docker, ["network", "inspect", "--format", "{{json .}}", network]
    )
    clone.require(
        value["Name"] == network
        and bool(re.fullmatch(r"[0-9a-f]{64}", value["Id"]))
        and (network != "host" or value["Driver"] == "host"),
        "existing_network_identity_required",
    )
    return {"name": network, "id": value["Id"]}


def new_copy(source, destination, expected_hash):
    with source.open("rb") as reader:
        before = os.fstat(reader.fileno())
        clone.require(before.st_nlink == 1, "independent_archive_required")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        after = os.fstat(reader.fileno())

    def identity(item):
        return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns

    clone.require(
        identity(before) == identity(after) and clone.sha(destination) == expected_hash,
        "archive_copy_not_verified",
    )
    destination.chmod(0o440)
    if os.name == "posix":
        os.chown(destination, 0, 10001)


def prepare_clients(stage, state, *, snapshot, network, docker, python):
    """Read explicit ownership only; copy archive bytes, never source permissions.

    Return an original storage-backup.json-shaped file. The coordinator installs
    it at stage/config/storage-backup.json, retaining any previous tools evidence.
    """
    stage, state = Path(stage).resolve(strict=True), Path(state).resolve(strict=True)
    snapshot, manifest = clone.manifest(snapshot)
    clone.validate_stage(stage, snapshot, manifest)
    resource = clone.owned_resource(clone.read(state))
    clone.require(
        PurePosixPath(python).is_absolute() and not any(c in python for c in "\r\n "),
        "explicit_linux_python_required",
    )
    clone.require(not any(c in str(stage) for c in ",\r\n"), "docker_bind_path_invalid")
    runtime_network = inspect_runtime(docker, network)
    folder = stage / "pg17-clients"
    clone.require(not folder.exists(), "new_clients_directory_required")
    folder.mkdir(mode=0o700)
    archives = folder / "archives"
    archives.mkdir(mode=0o750)
    if os.name == "posix":
        os.chown(archives, 0, 10001)
    records = []
    for index, row in enumerate(manifest["postgresql"]):
        source = clone.relative(snapshot, row["archive"])
        destination = archives / (str(index) + ".dump")
        new_copy(source, destination, row["sha256"])
        records.append(
            {
                "original": str(source),
                "copy": str(destination),
                "container": "/archives/" + destination.name,
                "sha256": row["sha256"],
                "bytes": row["bytes"],
            }
        )
    evidence = folder / "source"
    evidence.mkdir(mode=0o700)
    source_hashes = {}
    for source in (Path(__file__).resolve(), Path(clone.__file__).resolve()):
        destination = evidence / source.name
        raw = source.read_bytes()
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        source_hashes[source.name] = hashlib.sha256(raw).hexdigest()
    context = {
        "version": 1,
        "purpose": "owned-pg17-clone-clients",
        "run_id": uuid4().hex,
        "docker": str(docker),
        "network": runtime_network,
        "image": IMAGE,
        "state_file": str(state),
        "state_sha256": clone.sha(state),
        "resource": {
            key: resource[key] for key in ("isolation_id", "role", "database")
        },
        "archive_directory": str(archives),
        "archives": records,
        "query_sha256": allowed_queries(manifest),
        "sources": source_hashes,
        "snapshot_manifest_sha256": clone.sha(snapshot / "manifest.json"),
    }
    context_path = folder / "context.json"
    clone.write_new(context_path, context)
    tools = {}
    for name in ("psql", "pg_dump", "pg_restore"):
        wrapper = folder / name
        wrapper_source = (
            "#!"
            + python
            + "\nimport hashlib,sys\nfrom pathlib import Path\n"
            + "p=Path("
            + repr(str(evidence))
            + ")\n"
            + "expected="
            + repr(source_hashes)
            + "\n"
            + "if any(hashlib.sha256((p/n).read_bytes()).hexdigest()!=v for n,v in expected.items()):\n"
            + "    raise SystemExit(2)\n"
            + "sys.path.insert(0,str(p))\nimport linux_clone_clients\n"
            + "raise SystemExit(linux_clone_clients.wrapper_main("
            + repr(str(context_path))
            + ","
            + repr(name)
            + ",sys.argv[1:]))\n"
        )
        descriptor = os.open(wrapper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(wrapper_source)
        tools[name] = str(wrapper)
    configuration = folder / "storage-backup.json"
    clone.write_new(configuration, {"postgresql_tools": tools})
    return {
        "status": "owned_pg17_clients_prepared",
        "tools_configuration": str(configuration),
        "context": str(context_path),
        "run_id": context["run_id"],
        "archives": len(records),
        "archive_bytes_verified": True,
        "source_archive_permissions_changed": False,
        "database_created_or_dropped": False,
        "postgresql_tools": tools,
    }


def load_context(path, *, check_state=True):
    path = Path(path).resolve(strict=True)
    value = clone.read(path)
    clone.require(
        value.get("version") == 1
        and value.get("purpose") == "owned-pg17-clone-clients"
        and value.get("image") == IMAGE
        and bool(clone.HEX32.fullmatch(value.get("run_id", ""))),
        "owned_client_context_required",
    )
    if check_state:
        clone.require(
            clone.sha(value["state_file"]) == value["state_sha256"],
            "owned_state_changed",
        )
        resource = clone.owned_resource(clone.read(value["state_file"]))
        clone.require(
            value["resource"]
            == {key: resource[key] for key in ("isolation_id", "role", "database")},
            "owned_context_resource_changed",
        )
    for name, expected in value["sources"].items():
        clone.require(
            name in {"linux_clone.py", "linux_clone_clients.py"}
            and clone.sha(path.parent / "source" / name) == expected,
            "frozen_client_source_changed",
        )
    return value


def client_arguments(context, tool, arguments):
    clone.require(tool in {"psql", "pg_dump", "pg_restore"}, "client_tool_invalid")
    if arguments == ["--version"]:
        return arguments, False
    if tool == "psql":
        clone.require(
            len(arguments) == 7
            and tuple(arguments[:6]) == QUERY_PREFIX
            and sql_digest(arguments[6]) in context["query_sha256"],
            "readonly_tools_query_required",
        )
        return arguments, False
    clone.require(
        tool == "pg_restore"
        and len(arguments) == 7
        and arguments[0] == RESTORE_FLAGS[0]
        and arguments[1] == "--dbname=" + context["resource"]["database"]
        and tuple(arguments[2:6]) == RESTORE_FLAGS[1:],
        "owned_restore_arguments_required",
    )
    matches = [row for row in context["archives"] if arguments[-1] == row["original"]]
    clone.require(len(matches) == 1, "frozen_archive_required")
    record = matches[0]
    copied = Path(record["copy"])
    clone.require(
        copied.parent == Path(context["archive_directory"])
        and record["container"] == "/archives/" + copied.name
        and copied.stat().st_size == record["bytes"]
        and clone.sha(copied) == record["sha256"],
        "frozen_archive_copy_changed",
    )
    return [*arguments[:-1], record["container"]], True


def client_environment(context, tool, arguments, inherited):
    if arguments == ["--version"]:
        return docker_environment()
    resource = context["resource"]
    clone.require(
        inherited.get("PGHOST") == PG_HOST
        and inherited.get("PGPORT") == PG_PORT
        and inherited.get("PGHOSTADDR", "") in {"", PG_HOST}
        and inherited.get("PGUSER") == resource["role"]
        and inherited.get("PGDATABASE") == resource["database"]
        and isinstance(inherited.get("PGPASSWORD"), str)
        and bool(inherited["PGPASSWORD"])
        and "\x00" not in inherited["PGPASSWORD"],
        "fixed_owned_connection_required",
    )
    value = docker_environment()
    value.update(
        PGHOST=PG_HOST,
        PGPORT=PG_PORT,
        PGUSER=resource["role"],
        PGDATABASE=resource["database"],
        PGPASSWORD=inherited["PGPASSWORD"],
        PGCLIENTENCODING="UTF8",
        PGCONNECT_TIMEOUT="5",
        PGTZ="UTC",
        PGDATESTYLE="ISO",
        PGOPTIONS="-c extra_float_digits=3",
    )
    if tool == "psql":
        value["PGOPTIONS"] += " -c default_transaction_read_only=on"
    if "PGSSLMODE" in inherited:
        clone.require(
            inherited["PGSSLMODE"]
            in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"},
            "postgresql_sslmode_invalid",
        )
        value["PGSSLMODE"] = inherited["PGSSLMODE"]
    return value


def docker_command(context, tool, arguments, environment, mount_archive, name):
    command = [
        *docker_prefix(context["docker"]),
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--label",
        LABEL + "=" + context["run_id"],
        "--network",
        context["network"]["name"],
        "--read-only",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        "-e",
        "HOME=/tmp",
    ]
    if mount_archive:
        command.extend(
            [
                "--mount",
                "type=bind,src="
                + context["archive_directory"]
                + ",dst=/archives,readonly",
            ]
        )
    for key in sorted(key for key in environment if key.startswith("PG")):
        command.extend(["-e", key])
    return [*command, "--entrypoint", tool, IMAGE, *arguments]


def owned_container(context, identifier):
    value = inspect_json(
        context["docker"],
        [
            "inspect",
            "--format",
            '{"id":{{json .Id}},"image":{{json .Image}},"labels":{{json .Config.Labels}},"network":{{json .HostConfig.NetworkMode}}}',
            identifier,
        ],
    )
    clone.require(
        value["id"] == identifier
        and value["image"] == IMAGE
        and value["labels"].get(LABEL) == context["run_id"]
        and value["network"] == context["network"]["name"],
        "client_container_identity_changed",
    )
    return identifier


def cleanup_clients(stage):
    """Root finally: remove only this run's fixed-image clients, never PG objects."""
    context = load_context(Path(stage) / "pg17-clients/context.json", check_state=False)
    prefix = docker_prefix(context["docker"])
    result = subprocess.run(
        [
            *prefix,
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=" + LABEL + "=" + context["run_id"],
        ],
        env=docker_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    clone.require(result.returncode == 0, "client_cleanup_unavailable")
    identifiers = result.stdout.decode("ascii").split()
    clone.require(
        all(re.fullmatch(r"[0-9a-f]{64}", value) for value in identifiers),
        "client_id_invalid",
    )
    # Validate all selected clients before removing the first one.
    for identifier in identifiers:
        owned_container(context, identifier)
    for identifier in identifiers:
        result = subprocess.run(
            [*prefix, "rm", "--force", identifier],
            env=docker_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        clone.require(result.returncode == 0, "client_cleanup_failed")
    result = subprocess.run(
        [
            *prefix,
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=" + LABEL + "=" + context["run_id"],
        ],
        env=docker_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    clone.require(
        result.returncode == 0 and not result.stdout.strip(),
        "client_cleanup_not_confirmed",
    )
    return {
        "status": "owned_pg17_clients_absent",
        "containers_removed": len(identifiers),
        "database_created_or_dropped": False,
    }


def run_client(context_path, tool, arguments, *, output=None):
    context = load_context(context_path)
    arguments, mount_archive = client_arguments(context, tool, arguments)
    environment = client_environment(context, tool, arguments, os.environ)
    # Confirm the explicit named network still has the identity root selected.
    network = inspect_json(
        context["docker"],
        ["network", "inspect", "--format", "{{json .Id}}", context["network"]["name"]],
    )
    clone.require(
        network == context["network"]["id"], "client_network_identity_changed"
    )
    name = "tianshu-clone-" + context["run_id"][:10] + "-" + uuid4().hex[:12]
    command = docker_command(context, tool, arguments, environment, mount_archive, name)
    output = sys.stdout.buffer if output is None else output
    # Child CLI must not inherit Tools' stdout fd. On outer deadline/SIGKILL,
    # the wrapper's own pipe closes immediately; root finally removes any daemon
    # container through cleanup_clients before original PG owner cleanup.
    with subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            while block := process.stdout.read1(65536):
                output.write(block)
                output.flush()
            return process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=30)


def wrapper_main(context, tool, arguments):
    try:
        return run_client(context, tool, arguments)
    except Exception:
        # Never corrupt Tools' JSON-lines/fingerprint stdout with status text.
        print("owned_pg17_client_not_verified", file=sys.stderr)
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare = subparsers.add_parser("prepare")
    for name in ("stage", "state", "snapshot"):
        prepare.add_argument("--" + name, required=True, type=Path)
    for name in ("network", "docker", "python"):
        prepare.add_argument("--" + name, required=True)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--stage", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        clone.require(
            sys.platform == "linux" and os.geteuid() == 0,
            "linux_root_coordinator_required",
        )
        result = (
            cleanup_clients(args.stage)
            if args.action == "cleanup"
            else prepare_clients(
                args.stage,
                args.state,
                snapshot=args.snapshot,
                network=args.network,
                docker=args.docker,
                python=args.python,
            )
        )
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, clone.CloneError)
            else "owned_pg17_clients_not_verified"
        )
        print(json.dumps({"status": "refused", "reason": reason}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
