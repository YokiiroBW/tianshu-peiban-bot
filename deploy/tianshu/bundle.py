"""Create a new, movable deployment directory; do not start, stop, or migrate anything."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

from compose import compose_document
from configuration import prepare_inputs, tls_check
from manifest import (
    HERE,
    PRODUCTS,
    check_contracts,
    check_evidence,
    digest,
    fresh_target,
    inside,
    load_manifest,
    no_links,
    read_json,
    require,
    write_json,
)

TOOLS = (
    "acceptance.py",
    "release.py",
    "manifest.py",
    "observability_contract.py",
    "observability_release.py",
    "configuration.py",
    "compose.py",
    "bundle.py",
    "runtime_guard.py",
    "release-manifest.schema.json",
    "requirements.txt",
    "linux_validate.py",
    "linux_runtime.py",
    "linux_bootstrap.py",
    "bootstrap.py",
    "runtime_identity.py",
    "runtime-identity.schema.json",
    "container_probe.py",
    "dependency_probe.py",
    "synthetic_model.py",
)
RUNTIME_FILES = {
    "platform": "Dockerfile",
    "companion": "Dockerfile",
    "memory": "infra/container/Dockerfile",
    "gateway": "Dockerfile",
}


def put(root, name, raw, mode=0o640):
    path = inside(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(mode)


def initialize(manifest_path, inputs_path, contracts_root, target, environ=None):
    manifest = load_manifest(manifest_path)
    check_contracts(manifest, contracts_root)
    if manifest["status"] == "verified":
        check_evidence(manifest, manifest_path.parent)
    site, configs, environments, materials = prepare_inputs(
        manifest, inputs_path, environ
    )
    target = fresh_target(target)
    # All external inputs validated before any destination is created. An IO failure leaves an
    # incomplete directory for inspection, never an overwrite or an apparently runnable package.
    target.mkdir(mode=0o700)
    put(
        target,
        "INCOMPLETE",
        b"Initialization has not completed. Do not start services.\n",
        0o600,
    )
    write_json(target / "release-manifest.json", manifest)
    for evidence in manifest["evidence"]:
        put(
            target,
            evidence["path"],
            inside(manifest_path.parent, evidence["path"]).read_bytes(),
            0o644,
        )
    for name in TOOLS:
        put(target, "tools/" + name, (HERE / name).read_bytes(), 0o644)
    for contract in manifest["contracts"]:
        for file in contract["files"]:
            source = inside(inside(contracts_root, contract["id"]), file["path"])
            put(
                target,
                contract["path"] + "/" + file["path"],
                source.read_bytes(),
                0o644,
            )
    for product in PRODUCTS:
        config = inside(target, f"config/{product}/settings.json")
        config.parent.mkdir(parents=True, exist_ok=True)
        write_json(config, configs[product])
        config.chmod(0o640)
        for name, filename in (
            ("certificate", "server.pem"),
            ("key", "server.key"),
            ("ca", "ca.pem"),
        ):
            put(target, f"config/{product}/tls/{filename}", materials[product][name])
        # Single quotes are literal under Compose 2.20 env-file parsing. Unsafe quotes and
        # backslashes were rejected; '$' is preserved without shell or Compose interpolation.
        content = "".join(
            f"{name}='{value}'\n"
            for name, value in sorted(environments[product].items())
        )
        put(target, f"private/{product}.env", content.encode(), 0o600)
        for category in ("data", "logs"):
            inside(target, category + "/" + product).mkdir(parents=True, mode=0o750)
    for directory in target.rglob("*"):
        if directory.is_dir():
            # Published contracts contain no deployment secrets. Their bind-mount root
            # and descendants must remain readable by the runtime when another UID
            # initialized the package. Private/configuration directories stay restricted.
            public = directory.is_relative_to(target / "contracts")
            directory.chmod(0o755 if public else 0o750)
    inside(target, "private").chmod(0o700)
    # Material origin is an operator declaration, not proof that a CA is production approved.
    metadata = {
        "release_id": manifest["release_id"],
        "project_name": site["project_name"],
        "tls_provenance": {p: site["tls"][p]["provenance"] for p in PRODUCTS},
        "web_origin": site["web_origin"],
        "compose_inputs": {
            key: site[key]
            for key in (
                "project_name",
                "web_origin",
                "bind_address",
                "web_port",
                "subnet",
                "service_ips",
            )
        },
        "permissions": "requires_linux_uid_gid_10001_verification",
        "observability": "external_DEP-B_package_not_embedded",
    }
    write_json(target / "deployment.json", metadata)
    write_json(target / "compose.json", compose_document(manifest, site))
    commands = {
        "compose_config": [
            "docker",
            "compose",
            "--project-directory",
            ".",
            "-f",
            "compose.json",
            "config",
            "--quiet",
        ],
        "platform_preflight": [
            "docker",
            "compose",
            "-f",
            "compose.json",
            "run",
            "--rm",
            "--no-deps",
            "--entrypoint",
            "python",
            "platform",
            "-m",
            "services.platform",
            "--settings",
            "/etc/tianshu/settings.json",
            "preflight",
        ],
        "start_after_release_acceptance": [
            "docker",
            "compose",
            "-f",
            "compose.json",
            "up",
            "-d",
            "--no-build",
        ],
        "stop_all_writers": [
            "docker",
            "compose",
            "-f",
            "compose.json",
            "stop",
            "-t",
            "30",
            "companion",
            "gateway",
            "memory",
            "platform",
        ],
        "memory_first_install_schema_2": [
            "docker",
            "compose",
            "-f",
            "compose.json",
            "run",
            "--rm",
            "--no-deps",
            "memory",
            "--config",
            "/etc/tianshu/settings.json",
            "migrate-profiles",
            "--backup",
            "/srv/tianshu/first-install.pre-profiles.sqlite",
        ],
        "memory_first_install_schema_3": [
            "docker",
            "compose",
            "-f",
            "compose.json",
            "run",
            "--rm",
            "--no-deps",
            "memory",
            "--config",
            "/etc/tianshu/settings.json",
            "migrate-sources",
            "--backup",
            "/srv/tianshu/first-install.pre-sources.sqlite",
        ],
    }
    write_json(target / "commands.json", commands)
    # Only the immutable/private configuration set is hashed; mutable DBs/logs are never opened.
    files = {
        f.relative_to(target).as_posix(): digest(f.read_bytes())
        for f in sorted(target.rglob("*"))
        if f.is_file() and f.name != "INCOMPLETE"
    }
    write_json(
        target / "bundle-integrity.json", {"schema_version": "1.0.0", "files": files}
    )
    (target / "bundle-integrity.json").chmod(0o600)
    (target / "INCOMPLETE").unlink()
    return {
        "status": "initialized_candidate",
        "release_id": manifest["release_id"],
        "release_ready": False,
        "services_started": False,
        "checks": ["inputs", "tls", "contracts"],
        "blockers": manifest["blockers"],
    }


def verify_integrity(root):
    no_links(root)
    require(not (root / "INCOMPLETE").exists(), "initialization_incomplete")
    index = read_json(root / "bundle-integrity.json")
    require(
        index.get("schema_version") == "1.0.0" and isinstance(index.get("files"), dict),
        "bundle_inventory_invalid",
    )
    for name, expected in index["files"].items():
        path = inside(root, name)
        require(
            path.is_file() and digest(path.read_bytes()) == expected,
            "bundle_bytes_changed",
        )
    # Extra private files can change application behaviour and must not be silently accepted.
    for directory in (
        "config",
        "private",
        "contracts",
        "tools",
        "observability-input",
        "observability/config",
        "observability/code",
    ):
        for path in inside(root, directory).rglob("*"):
            no_links(path)
            if path.is_file():
                require(
                    path.relative_to(root).as_posix() in index["files"],
                    "unlisted_bundle_file",
                )


def permission_checks(root, *, core_only=False):
    require(os.name == "posix", "linux_permissions_not_verified")

    def runtime_access(path, required, *, directory, private=False):
        st = no_links(path).stat()
        require(
            stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode),
            "runtime_mount_type_mismatch",
        )
        mode = stat.S_IMODE(st.st_mode)
        if private:
            # No other access or group write. A readable private group must be the
            # explicitly configured container GID, never the initializer's default group.
            require(
                mode & 0o027 == 0 and (not mode & 0o070 or st.st_gid == 10001),
                "runtime_config_permissions",
            )
        else:
            require(mode & 0o022 == 0, "runtime_public_input_writable")
        # POSIX selects exactly one class. Owner permissions never fall back to group
        # or other; the Compose runtime has UID/GID 10001 and no supplementary groups.
        shift = 6 if st.st_uid == 10001 else 3 if st.st_gid == 10001 else 0
        available = (mode >> shift) & 0o7
        require(available & required == required, "runtime_mount_access_missing")
        return st

    def readable_tree(name, *, private=False):
        path = inside(root, name)
        runtime_access(path, 0o5, directory=True, private=private)
        for member in path.rglob("*"):
            # Classify by stat, never treat sockets/devices as readable input files.
            st = no_links(member).stat()
            directory = stat.S_ISDIR(st.st_mode)
            runtime_access(
                member, 0o5 if directory else 0o4, directory=directory, private=private
            )

    for product in PRODUCTS:
        for name in (f"data/{product}", f"logs/{product}"):
            path = inside(root, name)
            st = no_links(path).stat()
            require(stat.S_ISDIR(st.st_mode), "runtime_mount_type_mismatch")
            require(
                st.st_uid == 10001 and st.st_gid == 10001, "runtime_ownership_mismatch"
            )
            require(st.st_mode & 0o007 == 0, "runtime_world_permissions")
            require(st.st_mode & 0o700 == 0o700, "runtime_owner_permissions_missing")
        readable_tree(f"config/{product}", private=True)
    manifest = load_manifest(inside(root, "release-manifest.json"))
    if "observability" in manifest and not core_only:
        from observability_contract import COMPONENTS

        for name in COMPONENTS:
            path = inside(root, "observability/data/" + name)
            st = runtime_access(path, 0o7, directory=True, private=True)
            require(
                st.st_uid == 10001 and st.st_gid == 10001, "runtime_ownership_mismatch"
            )
        readable_tree("observability/config", private=True)
        readable_tree("observability/code", private=True)
        readable_tree("observability-input/tls", private=True)
        readable_tree("observability-input/secrets", private=True)
    readable_tree("contracts")
    # This is a direct file bind, opened by Python. It needs read, not execute, and
    # the container does not traverse the host-side tools/ or deployment ancestors.
    runtime_access(inside(root, "tools/runtime_guard.py"), 0o4, directory=False)
    # Compose reads env files on the host; they are not container bind mounts and
    # therefore remain private to the operator rather than requiring runtime ownership.
    private = inside(root, "private")
    mode = private.stat().st_mode
    require(
        stat.S_ISDIR(mode) and mode & 0o077 == 0 and mode & 0o700 == 0o700,
        "private_env_permissions",
    )
    for path in private.iterdir():
        mode = no_links(path).stat().st_mode
        require(
            stat.S_ISREG(mode) and mode & 0o077 == 0 and mode & 0o400 != 0,
            "private_env_permissions",
        )


def preflight(root, release=False, runtime=False, *, core_only=False):
    require(not (release and core_only), "release_requires_all_owners")
    verify_integrity(root)
    manifest = load_manifest(root / "release-manifest.json")
    check_contracts(manifest, root / "contracts")
    from urllib.parse import urlsplit

    metadata = read_json(root / "deployment.json")
    require(
        read_json(root / "compose.json")
        == compose_document(manifest, metadata["compose_inputs"]),
        "composition_changed",
    )
    for service in manifest["services"]:
        product = service["product"]
        if product not in PRODUCTS:
            continue
        names = list(service["tls_server_names"])
        if product == "platform":
            names.append(urlsplit(metadata["web_origin"]).hostname)
        directory = inside(root, f"config/{product}/tls")
        tls_check(
            directory / "server.pem",
            directory / "server.key",
            directory / "ca.pem",
            names,
        )
    for volume in manifest["volumes"]:
        if (
            volume["product"] == "observability"
            and not (root / "observability-release.json").exists()
        ):
            continue
        path = inside(root, volume["host_path"])
        if volume["mount"]:
            require(path.is_dir(), "persistent_mount_missing")
            for member in path.rglob("*"):
                no_links(member)
    # Fresh-install planning reserve, not a quota or a 30-day retention promise. Existing
    # state may need a complete pre-migration backup plus WAL growth. Read metadata only.
    state_bytes = sum(
        path.stat().st_size
        for p in PRODUCTS
        for path in inside(root, "data/" + p).rglob("*")
        if path.is_file()
    )
    configs = {
        p: read_json(inside(root, f"config/{p}/settings.json")) for p in PRODUCTS
    }
    log_budget = (
        configs["platform"]["diagnostics"].get("log_directory_bytes", 1073741824)
        + configs["gateway"]["observability"].get("max_directory_bytes", 1073741824)
        + 2 * 1073741824
    )
    require(
        shutil.disk_usage(root).free >= log_budget + 2 * state_bytes + 268435456,
        "insufficient_free_space_for_logs_and_migration_reserve",
    )
    checks = [
        "bundle_integrity",
        "manifest",
        "contract_bytes",
        "mount_boundaries",
        "tls_chain_and_names",
        "disk_planning_reserve",
    ]
    failures = list(manifest["blockers"])
    if "observability" in manifest:
        if (root / "observability-release.json").exists():
            from observability_release import verify_layout

            verify_layout(root, manifest)
            checks.append("observability_composition")
        else:
            failures.append("observability_not_configured")
    if runtime or release:
        permission_checks(root, core_only=core_only)
        checks.append("linux_permissions")
    if release:
        check_evidence(manifest, root)
        metadata = read_json(root / "deployment.json")
        require(
            all(v == "operator_supplied" for v in metadata["tls_provenance"].values()),
            "test_certificates_not_release_evidence",
        )
        require(not failures, "release_blockers_present")
        # No live readiness/port/resource occupancy is inferred from a configuration package.
        return {
            "status": "release_evidence_valid",
            "release_ready": False,
            "checks": checks,
            "remaining": [
                "target_port_subnet_resource_review",
                "authenticated_live_readiness",
            ],
        }
    return {
        "status": "package_valid",
        "release_ready": False,
        "checks": checks,
        "blockers": failures,
        "linux_runtime": "not_executed",
    }


def export_sources(manifest_path, repos_path, contracts_root, output):
    manifest = load_manifest(manifest_path)
    repos = read_json(repos_path)
    require(set(repos) == set(PRODUCTS), "four_repositories_required")
    check_contracts(manifest, contracts_root)
    output = fresh_target(output)
    archives = {}
    for product in PRODUCTS:
        repo = no_links(Path(repos[product]))
        commit = manifest["products"][product]["source"]["commit"]
        actual = (
            subprocess.run(
                ["git", "-C", str(repo), "rev-parse", commit + "^{commit}"],
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        require(actual == commit, "source_commit_unavailable")
        raw = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            capture_output=True,
            check=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for item in archive.getmembers():
                # Reject links, devices and sparse members, never follow an archive path.
                inside(output / product, item.name.rstrip("/"))
                require(item.isdir() or item.isfile(), "unsupported_archive_member")
                require(not item.issym() and not item.islnk(), "archive_link_refused")
        archives[product] = raw
    output.mkdir(mode=0o750)
    result = {
        "release_id": manifest["release_id"],
        "products": {},
        "images_built": False,
    }
    for product, raw in archives.items():
        context = inside(output, product)
        context.mkdir()
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(context, filter="data")
        if product == "platform":
            # The product Dockerfile explicitly COPYs contracts, which are not in its git repo.
            # This adds verified build inputs, never modifies any product source/Dockerfile.
            require(
                not (context / "contracts").exists(), "source_contract_overlay_conflict"
            )
            for contract in manifest["contracts"]:
                for file in contract["files"]:
                    put(
                        context,
                        contract["path"] + "/" + file["path"],
                        inside(
                            inside(contracts_root, contract["id"]), file["path"]
                        ).read_bytes(),
                        0o644,
                    )
        dockerfile = inside(context, RUNTIME_FILES[product])
        require(dockerfile.is_file(), "product_dockerfile_missing")
        inputs = {
            p.relative_to(context).as_posix(): digest(p.read_bytes())
            for p in sorted(context.rglob("*"))
            if p.is_file()
        }
        result["products"][product] = {
            "source": manifest["products"][product]["source"],
            "archive_sha256": digest(raw),
            "files": inputs,
            "git_files": {
                name: value
                for name, value in inputs.items()
                if not (product == "platform" and name.startswith("contracts/"))
            },
            "injected_contract_files": {
                name: value
                for name, value in inputs.items()
                if product == "platform" and name.startswith("contracts/")
            },
            "build_command": [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                product + "/" + RUNTIME_FILES[product],
                "--tag",
                manifest["products"][product]["image"]["reference"],
                product,
            ],
        }
    write_json(output / "source-inventory.json", result)
    return {
        "status": "sources_exported",
        "images_built": False,
        "products": {p: manifest["products"][p]["source"]["commit"] for p in PRODUCTS},
    }


def runtime_available():
    executable = shutil.which("docker")
    if executable is None:
        return {
            "status": "not_available",
            "reason": "docker_executable_missing",
            "executed": False,
        }
    # Inventory only. Never creates a container, pulls an image, or alters another project.
    result = subprocess.run(
        [executable, "info", "--format", "{{.OSType}}"], capture_output=True, timeout=15
    )
    return {
        "status": "available"
        if result.returncode == 0 and result.stdout.strip() == b"linux"
        else "not_available",
        "executed": True,
        "reason": "local_runtime_inventory_only",
    }
