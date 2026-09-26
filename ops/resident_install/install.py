"""Fixed-product first install for an empty resident candidate.

This module consumes the existing deployment bundle contract. It never edits a
product repository or database, and never calls the synthetic bootstrap path.
"""

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "tianshu"
sys.path.insert(0, str(DEPLOY))

from bundle import initialize, permission_checks, verify_integrity  # noqa: E402
from configuration import PRODUCTS, references  # noqa: E402
from manifest import (  # noqa: E402
    Refused,
    digest,
    inside,
    load_manifest,
    no_links,
    read_json,
    require,
)

FIXED_COMMITS = {
    "platform": "c1c7547680911462439ee9c9a09f4e72f44f36a3",
    "companion": "e94b609099365f75ca933d9fed03cdfbc83ec235",
    "memory": "9a3b2bed6aebff9f0677f2c62e979859769e0c9c",
    "gateway": "601974194042641c5a85cc3c061cbd1880d7daf1",
}
FIXED_OBSERVABILITY = "a194fa7b527ac2da0f13b8c3e95e76a1b836b4b3"
FIXED_A3_EXPORT = "8e381646cee06f37a61e80c16e9e2b50cd5984a9"
# Every local module imported by the fixed exporter comes from the same Git
# object. A checkout script or its imports are never executed for this gate.
A3_EXPORT_SOURCES = (
    "resident_export.py", "bundle.py", "manifest.py", "configuration.py",
    "compose.py", "observability_release.py", "observability_contract.py",
    "resource_profile.py", "network_plan.py", "release-manifest.schema.json",
)


def _fixed_git_env():
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


CORE_PROJECT = "tianshu-v2-resident"
OBS_PROJECT = "tianshu-v2-resident-obs"
REF = re.compile(r"origin:[0-9a-f]{32}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")

# The request is framed on stdin. Neither the signed ref nor any provider field
# appears in argv, Docker's command history, or a host-side request file.
LOCAL_CLI = (
    "import json,os,subprocess,sys,tempfile\n"
    "raw=sys.stdin.buffer.readline(1048577)\n"
    "assert raw.endswith(b'\\n') and len(raw)<=1048576\n"
    "with tempfile.NamedTemporaryFile(dir='/tmp',suffix='.json') as f:\n"
    " f.write(raw);f.flush()\n"
    " p=subprocess.run([sys.executable,'-B','-m','services.platform',"
    "'--settings','/etc/tianshu/settings.json','local',"
    "'--credential-env','TS_ADMIN_TOKEN',sys.argv[1],'--input',f.name],"
    "capture_output=True,timeout=30)\n"
    " if p.returncode==0:sys.stdout.buffer.write(p.stdout)\n"
    " sys.exit(p.returncode)\n"
)


def _private(path, *, limit=1048576):
    path = no_links(Path(path))
    require(path.is_file() and path.stat().st_size <= limit, "private_input_invalid")
    if os.name == "posix":
        require(stat.S_IMODE(path.stat().st_mode) == 0o600, "private_input_mode_0600_required")
    return path


def _private_json(path):
    return read_json(_private(path))


def _fixed(manifest):
    require(
        {p: manifest["products"][p]["source"]["commit"] for p in PRODUCTS}
        == FIXED_COMMITS,
        "fixed_product_commit_required",
    )
    require(manifest["status"] == "candidate", "candidate_manifest_required")
    require(
        manifest.get("observability", {}).get("source", {}).get("commit")
        == FIXED_OBSERVABILITY,
        "fixed_observability_commit_required",
    )


def _write_private(path, document):
    raw = (json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _password(path):
    raw = _private(path, limit=1024).read_bytes()
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1, "password_file_one_line_required")
    try:
        value = raw[: -2 if raw.endswith(b"\r\n") else -1].decode("utf-8")
    except UnicodeError:
        raise Refused("password_encoding_invalid") from None
    require(
        12 <= len(value) <= 256 and all(ord(ch) >= 32 for ch in value),
        "admin_password_invalid",
    )
    return value


def prepare(manifest_path, site_path, contracts_root, credentials_path,
            password_path, admin_username, bundle_root, *,
            isolated_windows_fixture=False):
    """Build a fresh real candidate through the existing four-product adapter."""
    manifest = load_manifest(manifest_path)
    _fixed(manifest)
    site = read_json(site_path)
    origin = urlsplit(site["web_origin"])
    auxiliary = site.get("auxiliary_subnets")
    require(
        site["project_name"] == "tianshu-v2-resident"
        and isinstance(site.get("resource_profile"), dict)
        and site["resource_profile"].get("kind") == "nas-cpuset-resident-v1"
        and isinstance(auxiliary, dict)
        and set(auxiliary) == {"egress", "frontend"}
        and site.get("a1_loopback_api_ports") is None
        and site.get("public_web") is True
        and origin.scheme == "http"
        and origin.hostname == site["bind_address"]
        and ipaddress.ip_address(origin.hostname).is_private
        and all(site["tls"][p]["provenance"] == "operator_supplied" for p in PRODUCTS),
        "resident_lan_and_tls_inputs_required",
    )
    settings = read_json(inside(site_path.parent, site["config_files"]["platform"]))
    require(
        isinstance(admin_username, str)
        and 1 <= len(admin_username) <= 128
        and all(ord(ch) >= 32 for ch in admin_username)
        and not admin_username.startswith("synthetic-")
        and admin_username != "replace-with-admin-name"
        and settings["web"]["username"] == admin_username,
        "admin_username_mismatch",
    )
    require(
        settings["principals"][settings["web"]["principal"]]["token_env"]
        == "TS_ADMIN_TOKEN"
        and settings["entries"]["config-entry"]["ttl_seconds"] == 300
        and "expires_at" not in settings["entries"]["config-entry"],
        "admin_credential_binding_changed",
    )
    require(
        os.name == "posix" or isolated_windows_fixture,
        "linux_private_input_permissions_required",
    )
    password_env = settings["web"]["password_hash"]["$password_env"]
    credentials = _private_json(credentials_path)
    require(
        isinstance(credentials, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in credentials.items())
        and password_env not in credentials,
        "credential_input_invalid",
    )
    required = set()
    for product in PRODUCTS:
        config = read_json(inside(site_path.parent, site["config_files"][product]))
        required |= references(config) - {"TIANSHU_DIAGNOSTICS_TOKEN", password_env}
        required.add(site["diagnostics_env"][product])
    require(set(credentials) == required, "credential_names_mismatch")
    credentials[password_env] = _password(password_path)
    try:
        result = initialize(
            Path(manifest_path), Path(site_path), Path(contracts_root),
            Path(bundle_root), credentials,
        )
    finally:
        credentials.pop(password_env, None)
    require(result["status"] == "initialized_candidate", "bundle_initialize_failed")
    return {
        "status": "resident_candidate_prepared",
        "bundle_root": str(Path(bundle_root).absolute()),
        "release_ready": False,
        "provider": "configured" if settings["providers"] else "not_configured",
        "next_stage": "export_fixed_sources_and_resident_compose",
    }


def _compose(root, compose_path):
    compose_path = no_links(Path(compose_path or root / "compose.json"))
    require(
        compose_path.is_file()
        and compose_path.resolve() == (root / "compose.json").resolve(),
        "prepared_bundle_compose_required",
    )
    doc = read_json(compose_path)
    deployment = read_json(root / "deployment.json")
    require(
        doc.get("name") == deployment["project_name"]
        and set(doc.get("services", {})) == set(PRODUCTS),
        "compose_project_or_services_mismatch",
    )
    return compose_path


def _gateway_origin_precheck(root, gateway):
    name = gateway["platform_origin_env"]
    require(ENV_NAME.fullmatch(name) is not None, "gateway_origin_env_invalid")
    path = root / "private" / "gateway.env"
    raw = _private(path).read_bytes()
    try:
        lines = raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeError:
        raise Refused("gateway_environment_invalid") from None
    matches = [i for i, line in enumerate(lines) if line.startswith(name + "=")]
    require(len(matches) == 1, "gateway_origin_placeholder_required")
    require(
        REF.fullmatch(lines[matches[0]].split("=", 1)[1].strip().strip("'")) is None,
        "gateway_origin_already_issued",
    )
    return path, lines, matches[0], name


def _clean_install(root):
    for category, owners in (
        ("data", set(PRODUCTS)),
        ("logs", set(PRODUCTS)),
        ("observability/data", {"vector", "loki", "grafana", "prometheus", "guard"}),
    ):
        base = inside(root, category)
        require(
            base.is_dir() and {item.name for item in base.iterdir()} == owners,
            "first_install_mutable_layout_changed",
        )
        for owner in owners:
            folder = no_links(base / owner)
            require(
                folder.is_dir() and not any(folder.iterdir()),
                "nonempty_first_install_mutable_state_refused",
            )
    require(
        not (root / "reports" / "resident-install").exists()
        and not (root / "INCOMPLETE").exists(),
        "installation_already_attempted",
    )


def _export_lock(root, lock_path, manifest, compose):
    lock_path = _private(lock_path)
    lock = read_json(lock_path)
    inventory = root / "bundle-integrity.json"
    indexed = read_json(inventory)["files"]
    protected = {
        name: value
        for name, value in indexed.items()
        if name.startswith(
            ("config/", "private/", "observability/config/", "observability-input/")
        )
    }
    output = lock_path.parent
    require(
        isinstance(lock, dict)
        and lock.get("schema_version") == "nas-a3-r1-resident-export/1"
        and lock.get("status") == "resident_candidate"
        and lock.get("acceptance") == "pending_live_acceptance"
        and lock.get("release_ready") is False
        and lock.get("projects") == [CORE_PROJECT, OBS_PROJECT]
        and not (output / "INCOMPLETE").exists()
        and isinstance(lock.get("deployment_root"), str)
        and isinstance(lock.get("observability_source"), dict)
        and lock.get("manifest_sha256") == digest(
            (root / "release-manifest.json").read_bytes()
        )
        and lock.get("bundle_integrity_sha256") == digest(inventory.read_bytes())
        and Path(lock.get("deployment_root", "")).resolve() == root.resolve()
        and lock.get("product_sources")
        == {p: manifest["products"][p]["source"] for p in PRODUCTS}
        and lock.get("observability_source", {}).get("commit")
        == FIXED_OBSERVABILITY
        and lock.get("config_and_private_sha256") == protected,
        "resident_export_lock_mismatch",
    )
    hashes = lock.get("compose_sha256")
    stack_files = {
        project: no_links(output / project / "compose.yaml")
        for project in (CORE_PROJECT, OBS_PROJECT)
    }
    require(
        isinstance(hashes, dict)
        and set(hashes) == {CORE_PROJECT, OBS_PROJECT}
        and all(path.is_file() for path in stack_files.values())
        and all(
            digest(stack_files[project].read_bytes()) == hashes[project]
            for project in (CORE_PROJECT, OBS_PROJECT)
        ),
        "resident_export_compose_changed",
    )
    exported_core = read_json(stack_files[CORE_PROJECT])
    exported_obs = read_json(stack_files[OBS_PROJECT])
    require(
        exported_core.get("name") == CORE_PROJECT
        and exported_obs.get("name") == OBS_PROJECT
        and set(exported_core.get("services", {})) == set(PRODUCTS)
        and set(exported_obs.get("services", {}))
        == {"obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard"},
        "resident_export_service_names_changed",
    )
    images = lock.get("images")
    require(
        isinstance(images, dict)
        and set(images)
        == set(PRODUCTS)
        | {"obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard"}
        and all(
            isinstance(value, str)
            and re.fullmatch(r".+@sha256:[0-9a-f]{64}", value) is not None
            for value in images.values()
        ),
        "resident_export_images_incomplete",
    )
    for product in PRODUCTS:
        image = manifest["products"][product]["image"]
        require(
            isinstance(image.get("reference"), str)
            and isinstance(image.get("digest"), str),
            "resident_image_digest_required",
        )
        expected = (
            image["reference"] + "@" + image["digest"]
        )
        require(
            images[product] == expected
            and compose["services"][product]["image"] == expected
            and re.fullmatch(r".+@sha256:[0-9a-f]{64}", expected) is not None,
            "resident_image_digest_mismatch",
        )
        require(
            exported_core["services"][product]["image"] == expected,
            "resident_export_core_image_changed",
        )
    for product in ("obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard"):
        require(
            exported_obs["services"][product]["image"] == images[product],
            "resident_export_observability_image_changed",
        )
    return images, stack_files, hashes


def _trusted_export(root, repository, output, *, deployment_root=None):
    """Recompute an A3 export using only code from its fixed Git commit."""
    with tempfile.TemporaryDirectory(prefix="tianshu-a3-source-") as temporary:
        source = Path(temporary)
        for name in A3_EXPORT_SOURCES:
            raw = _run(
                ["git", "-C", str(repository), "show",
                 f"{FIXED_A3_EXPORT}:deploy/tianshu/{name}"],
                cwd=root, seconds=30, env=_fixed_git_env(),
            )
            (source / name).write_bytes(raw)
        _run(
            [sys.executable, "-E", "-s", "-B", str(source / "resident_export.py"),
             "--bundle", str(root), "--repository", str(repository),
             "--deployment-root", deployment_root or str(root),
             "--output", str(output)],
            cwd=root, seconds=120, env=_fixed_git_env(),
        )


def _trusted_export_matches(root, lock_path, stacks, repository, *, compare_lock):
    """Compare executable stacks with a fresh export from fixed A3 Git code."""
    lock_path = _private(lock_path)
    repository = no_links(Path(repository))
    require(repository.is_dir(), "fixed_export_repository_required")
    with tempfile.TemporaryDirectory(prefix="tianshu-a3-recompute-") as temporary:
        output = Path(temporary) / "export"
        _trusted_export(
            root, repository, output,
            deployment_root=read_json(lock_path).get("deployment_root"),
        )
        if compare_lock:
            expected_lock = output / "resident-export.lock.json"
            require(
                lock_path.read_bytes() == expected_lock.read_bytes(),
                "resident_export_not_trusted",
            )
        for project in (CORE_PROJECT, OBS_PROJECT):
            require(
                stacks[project].read_bytes()
                == (output / project / "compose.yaml").read_bytes(),
                "resident_export_not_trusted",
            )


def _trusted_first_export(root, lock_path, stacks, repository):
    """Reject changed first execution input before any Docker command can run."""
    _trusted_export_matches(
        root, lock_path, stacks, repository, compare_lock=True,
    )


def _repo_digest(reference):
    repository_and_tag, image_digest = reference.rsplit("@", 1)
    tail = repository_and_tag.rsplit("/", 1)[-1]
    repository = (
        repository_and_tag.rsplit(":", 1)[0]
        if ":" in tail else repository_and_tag
    )
    return repository + "@" + image_digest


def _local_images_present(images, root):
    for expected in images.values():
        repo_digest = _repo_digest(expected)
        raw = _run(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", repo_digest],
            cwd=root, seconds=20,
        )
        try:
            available = json.loads(raw)
        except (UnicodeError, ValueError):
            raise Refused("docker_image_digest_inventory_invalid") from None
        require(
            isinstance(available, list) and repo_digest in available,
            "fixed_product_image_digest_not_local",
        )


def _root_mount(container, root):
    """Inspect real Docker mount sources, including created containers."""
    sources = []
    for mount in container.get("Mounts", []):
        sources.append(mount.get("Source"))
    host = container.get("HostConfig", {})
    for mount in host.get("Mounts") or []:
        if mount.get("Type") == "bind":
            sources.append(mount.get("Source"))
    for bind in host.get("Binds") or []:
        if isinstance(bind, str):
            sources.append(bind.split(":", 1)[0])
    for source in sources:
        if isinstance(source, str) and source.startswith("/"):
            path = Path(source).resolve()
            if path.is_relative_to(root) or root.is_relative_to(path):
                return True
    return False


def _docker_occupants(root):
    root = Path(root)
    # The first live preflight requires a fresh, absent deployment root. Docker
    # inventory itself is read-only and must run from an existing directory.
    require(root.is_absolute() and (root.is_dir() or not root.exists()),
            "docker_inventory_root_invalid")
    inventory_cwd = root if root.is_dir() else root.parent
    require(inventory_cwd.is_dir(), "docker_inventory_workdir_missing")
    raw = _run(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}"],
        cwd=inventory_cwd, seconds=20,
    )
    try:
        ids = raw.decode("ascii").splitlines()
    except UnicodeError:
        raise Refused("docker_container_inventory_invalid") from None
    require(
        len(ids) == len(set(ids))
        and all(re.fullmatch(r"[0-9a-f]{64}", item) for item in ids),
        "docker_container_inventory_invalid",
    )
    if not ids:
        return []
    # Select only ownership and mount metadata. A full inspect includes
    # Config.Env from every unrelated container on the host.
    selected = (
        '{"Id":{{json .Id}},'
        '"Config":{"Image":{{json .Config.Image}},"Labels":{{json .Config.Labels}}},'
        '"State":{"Running":{{json .State.Running}},"Status":{{json .State.Status}}},'
        '"HostConfig":{"Privileged":{{json .HostConfig.Privileged}},'
        '"Binds":{{json .HostConfig.Binds}},"Mounts":{{json .HostConfig.Mounts}}},'
        '"Mounts":{{json .Mounts}}}'
    )
    raw = _run(["docker", "inspect", "--format", selected, *ids],
               cwd=inventory_cwd, seconds=30)
    try:
        containers = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    except (UnicodeError, ValueError):
        raise Refused("docker_container_inventory_invalid") from None
    require(
        isinstance(containers, list)
        and len(containers) == len(ids)
        and all(isinstance(item, dict) for item in containers)
        and {item.get("Id") for item in containers} == set(ids),
        "docker_container_inventory_invalid",
    )
    return [
        item for item in containers
        if (item.get("Config", {}).get("Labels") or {}).get("com.docker.compose.project")
        in (CORE_PROJECT, OBS_PROJECT)
        or _root_mount(item, root)
    ]


def _projects_empty(root):
    require(not _docker_occupants(root), "resident_deployment_root_occupied")


def _exporter_ready(root, repository, output):
    repository = no_links(Path(repository))
    output = no_links(Path(output))
    require(
        repository.is_absolute()
        and repository.is_dir()
        and output.is_absolute()
        and output.parent.is_dir()
        and not output.exists()
        and not output.is_relative_to(root)
        and not root.is_relative_to(output),
        "fresh_final_export_target_required",
    )
    for commit in (FIXED_A3_EXPORT, FIXED_OBSERVABILITY):
        kind = _run(
            ["git", "-C", str(repository), "cat-file", "-t", commit],
            cwd=root, seconds=20, env=_fixed_git_env(),
        )
        require(kind.strip() == b"commit", "fixed_export_source_unavailable")
    return repository, output


def _provider(root, publication_path):
    platform = read_json(root / "config" / "platform" / "settings.json")
    gateway = read_json(root / "config" / "gateway" / "settings.json")
    require(
        isinstance(platform["web"]["username"], str)
        and not platform["web"]["username"].startswith("synthetic-")
        and platform["principals"][platform["web"]["principal"]]["token_env"]
        == "TS_ADMIN_TOKEN"
        and platform["entries"]["config-entry"]["ttl_seconds"] == 300
        and "expires_at" not in platform["entries"]["config-entry"],
        "real_admin_binding_required",
    )
    configured = bool(platform["providers"])
    require(
        platform["web"]["dialogue_enabled"] is configured,
        "provider_dialogue_configuration_mismatch",
    )
    if not configured:
        require(publication_path is None, "unexpected_provider_publication")
        require(
            len(gateway["targets"]) == 1
            and gateway["targets"][0]["base_url"] == gateway["platform_base_url"],
            "unconfigured_provider_target_present",
        )
        return None
    require(publication_path is not None, "provider_publication_required")
    publication = _private_json(publication_path)
    require(
        isinstance(publication, dict)
        and publication.get("status") == "published"
        and isinstance(publication.get("providers"), list)
        and bool(publication["providers"])
        and all(
            isinstance(p, dict)
            and all(isinstance(p.get(k), str) for k in
                    ("provider_id", "base_url", "credential_ref"))
            for p in publication["providers"]
        ),
        "provider_publication_invalid",
    )
    require(
        {p["provider_id"] for p in publication["providers"]} == set(platform["providers"])
        and all(not p["provider_id"].startswith("synthetic") for p in publication["providers"]),
        "provider_registration_mismatch",
    )
    try:
        published = datetime.fromisoformat(
            publication["published_at"].replace("Z", "+00:00")
        )
        usable_until = datetime.fromisoformat(
            publication["usable_until"].replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("provider_publication_window_invalid") from None
    now = datetime.now(timezone.utc)
    require(
        published.tzinfo is not None
        and usable_until.tzinfo is not None
        and published <= now
        and 0 < (usable_until - published).total_seconds()
        <= platform.get("config_max_lifetime_seconds", 3600)
        and (usable_until - now).total_seconds() >= 120,
        "provider_publication_window_insufficient",
    )
    target_urls = {target["base_url"] for target in gateway["targets"]}
    env_lines = (root / "private" / "gateway.env").read_text(encoding="utf-8").splitlines()
    serialized = json.dumps(publication, ensure_ascii=False, sort_keys=True)
    require(
        all(
            line.split("=", 1)[1].strip("'") not in serialized
            for line in env_lines if "=" in line
        ),
        "provider_publication_contains_credential",
    )
    for provider in publication["providers"]:
        registration = platform["providers"][provider["provider_id"]]
        secret_name = gateway["secret_references"].get(provider["credential_ref"])
        require(
            provider["base_url"] == registration["base_url"]
            and provider["base_url"] in target_urls
            and isinstance(secret_name, str)
            and sum(line.startswith(secret_name + "=") for line in env_lines) == 1,
            "provider_route_or_secret_missing",
        )
    return publication


def _run(command, *, cwd, input_bytes=None, seconds=90, env=None):
    try:
        result = subprocess.run(
            command, cwd=cwd, input=input_bytes, capture_output=True,
            timeout=seconds, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("product_command_unavailable_or_timeout") from None
    require(result.returncode == 0, "product_command_failed")
    return result.stdout


def _step(work, name, command, *, cwd, input_bytes=None, seconds=90):
    _write_private(work / (name + "-attempt.json"), {
        "stage": name, "state": "started", "automatic_retry": False,
    })
    return _run(command, cwd=cwd, input_bytes=input_bytes, seconds=seconds)


def _local(base, work, action, document, root, *, marker=None):
    frame = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    require(len(frame) <= 1048576, "publication_too_large")
    raw = _step(
        work, marker or action,
        [*base, "run", "--pull", "never", "--rm", "--no-deps", "-T", "--entrypoint",
         "python", "platform", "-c", LOCAL_CLI, action],
        cwd=root, input_bytes=frame, seconds=60,
    )
    try:
        return json.loads(raw)
    except (UnicodeError, ValueError):
        raise Refused("product_cli_receipt_invalid") from None


def _install_ref(root, receipt, origin):
    ref = receipt.get("assertion_ref")
    require(isinstance(ref, str) and REF.fullmatch(ref) is not None,
            "product_issue_ref_invalid")
    try:
        expiry = datetime.fromisoformat(receipt["expires_at"].replace("Z", "+00:00"))
        remaining = expiry.timestamp() - datetime.now(timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("product_issue_expiry_invalid") from None
    require(expiry.tzinfo is not None and remaining >= 120, "product_issue_budget_insufficient")
    path, lines, index, name = origin
    lines[index] = name + "='" + ref + "'\n"
    raw = "".join(lines).encode("utf-8")
    index_path = root / "bundle-integrity.json"
    inventory = read_json(index_path)
    inventory["files"]["private/gateway.env"] = digest(raw)
    marker = root / "INCOMPLETE"
    _write_private(marker, {"reason": "resident_install_private_ref_update"})
    temp = path.with_name(path.name + ".resident-tmp")
    with temp.open("xb") as stream:
        os.chmod(temp, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    temp_index = index_path.with_name(index_path.name + ".resident-tmp")
    _write_private(temp_index, inventory)
    os.replace(temp_index, index_path)
    marker.unlink()
    verify_integrity(root)
    return receipt["expires_at"]


def _platform_only(root, stacks, *, expected_id=None):
    occupants = _docker_occupants(root)
    require(len(occupants) == 1, "resident_start_stage_changed")
    container = occupants[0]
    labels = container.get("Config", {}).get("Labels") or {}
    state = container.get("State", {})
    container_id = container.get("Id")
    service = read_json(stacks[CORE_PROJECT])["services"]["platform"]
    expected_mounts = {
        (volume["source"], volume["target"], volume.get("read_only") is not True)
        for volume in service.get("volumes", [])
    }
    actual_mounts = {
        (mount.get("Source"), mount.get("Destination"), mount.get("RW"))
        for mount in container.get("Mounts", []) if mount.get("Type") == "bind"
    }
    require(
        isinstance(container_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", container_id) is not None
        and (expected_id is None or container_id == expected_id)
        and labels.get("com.docker.compose.project") == CORE_PROJECT
        and labels.get("com.docker.compose.service") == "platform"
        and labels.get("com.docker.compose.project.working_dir") == str(root)
        and labels.get("com.docker.compose.project.config_files")
        == str(stacks[CORE_PROJECT])
        and state.get("Running") is True
        and state.get("Status") == "running"
        and container.get("Config", {}).get("Image") == service["image"]
        and container.get("HostConfig", {}).get("Privileged") is False
        and actual_mounts == expected_mounts,
        "platform_container_identity_changed",
    )
    return container_id


def _current_ref(root, expected_hash):
    gateway = read_json(root / "config" / "gateway" / "settings.json")
    name = gateway["platform_origin_env"]
    path = root / "private" / "gateway.env"
    lines = _private(path).read_text(encoding="utf-8").splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(name + "=")]
    require(len(matches) == 1, "issued_gateway_ref_changed")
    ref = lines[matches[0]].split("=", 1)[1].strip().strip("'")
    require(
        REF.fullmatch(ref) is not None and digest(ref.encode()) == expected_hash,
        "issued_gateway_ref_changed",
    )
    return ref, (path, lines, matches[0], name)


def activate(bundle_root, *, export_lock_path, export_repository,
             final_export_output, publication_path=None):
    """One attempt: real Memory migrations, Platform readiness and public CLI authority."""
    require(os.name == "posix", "linux_activation_required")
    root = no_links(Path(bundle_root))
    require(root.is_absolute() and root.is_dir(), "bundle_root_invalid")
    verify_integrity(root)
    manifest = read_json(root / "release-manifest.json")
    _fixed(manifest)
    prepared_compose = _compose(root, None)
    _clean_install(root)
    permission_checks(root, core_only=True)
    images, stacks, hashes = _export_lock(
        root, export_lock_path, manifest, read_json(prepared_compose)
    )
    publication = _provider(root, publication_path)
    gateway = read_json(root / "config" / "gateway" / "settings.json")
    origin = _gateway_origin_precheck(root, gateway)
    repository, final_output = _exporter_ready(
        root, export_repository, final_export_output
    )
    _trusted_first_export(root, export_lock_path, stacks, repository)
    _local_images_present(images, root)
    _projects_empty(root)
    compose = stacks[CORE_PROJECT]
    work = root / "reports" / "resident-install"
    work.mkdir(parents=True, mode=0o700)
    os.chmod(work, 0o700)
    _write_private(work / "attempt.json", {
        "state": "started", "automatic_retry": False,
        "release_id": manifest["release_id"],
        "compose_sha256": digest(compose.read_bytes()),
        "export_compose_sha256": hashes,
        "export_repository": str(repository),
        "provider": "configured" if publication else "not_configured",
    })
    base = ["docker", "compose", "--project-directory", str(root), "-f", str(compose)]
    try:
        _step(work, "compose_config", [*base, "config", "--quiet"], cwd=root)
        for stage, operation, backup in (
            ("memory_schema_2", "migrate-profiles", "first-install.pre-profiles.sqlite"),
            ("memory_schema_3", "migrate-sources", "first-install.pre-sources.sqlite"),
        ):
            _step(
                work, stage,
                [*base, "run", "--pull", "never", "--rm", "--no-deps",
                 "memory", "--config",
                 "/etc/tianshu/settings.json", operation, "--backup",
                 "/srv/tianshu/" + backup],
                cwd=root, seconds=120,
            )
        _step(
            work, "platform_start",
            [*base, "up", "--pull", "never", "-d", "--no-deps",
             "--wait", "--wait-timeout", "120", "platform"],
            cwd=root, seconds=180,
        )
        _step(
            work, "platform_preflight",
            [*base, "run", "--pull", "never", "--rm", "--no-deps",
             "--entrypoint", "python",
             "platform", "-B", "-m", "services.platform", "--settings",
             "/etc/tianshu/settings.json", "preflight"],
            cwd=root,
        )
        platform_id = _platform_only(root, stacks)
        if publication is not None:
            _local(base, work, "publish", publication, root)
        receipt = _local(base, work, "issue", {"entry_id": "config-entry"}, root)
        expiry = _install_ref(root, receipt, origin)
        _write_private(work / "result.json", {
            "state": "authority_initialized_pending_final_export",
            "issuer": "fixed_product_public_cli",
            "gateway_ref": "private_file_only",
            "gateway_ref_sha256": digest(receipt["assertion_ref"].encode()),
            "expires_at": expiry,
            "provider": "configured" if publication else "not_configured",
            "functional_limit": None if publication else "model_dialogue_unavailable",
            "running_services": ["platform"],
            "platform_container_id": platform_id,
            "automatic_retry": False,
            "new_export_lock_required": True,
            "release_ready": False,
        })
        _write_private(work / "final_export-attempt.json", {
            "stage": "final_export", "state": "started", "automatic_retry": False,
        })
        _trusted_export(root, repository, final_output)
        final = finalize(
            root, export_lock_path=final_output / "resident-export.lock.json"
        )
        return {
            "status": "resident_first_install_export_verified",
            "provider": "configured" if publication else "not_configured",
            "gateway_ref": "private_file_only",
            "expires_at": expiry,
            "running_services": ["platform"],
            "platform_container_id": platform_id,
            "final_export": str(final_output),
            "remaining_seconds_at_check": final["remaining_seconds_at_check"],
            "release_ready": False,
        }
    except Exception as exc:
        code = str(exc) if isinstance(exc, Refused) else "unexpected_install_failure"
        _write_private(work / "failure.json", {
            "state": "needs_diagnosis", "code": code, "automatic_retry": False,
        })
        raise Refused(code) from None


def reauthorize(bundle_root, *, first_export_lock_path, export_repository,
                final_export_output):
    """Manual one-shot reissue only after expiry, before final import."""
    require(os.name == "posix", "linux_activation_required")
    root = no_links(Path(bundle_root))
    require(root.is_absolute() and root.is_dir(), "bundle_root_invalid")
    verify_integrity(root)
    work = root / "reports" / "resident-install"
    require(
        (work / "result.json").is_file()
        and not (work / "reauthorization-attempt.json").exists(),
        "manual_reauthorization_stage_invalid",
    )
    initial = read_json(work / "result.json")
    attempt = read_json(work / "attempt.json")
    require(
        initial.get("state") == "authority_initialized_pending_final_export"
        and attempt.get("state") == "started",
        "authority_result_invalid",
    )
    try:
        expiry = datetime.fromisoformat(initial["expires_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("product_issue_expiry_invalid") from None
    require(
        expiry.tzinfo is not None and expiry <= datetime.now(timezone.utc),
        "gateway_origin_not_yet_expired",
    )
    old_ref, origin = _current_ref(root, initial.get("gateway_ref_sha256"))
    lock_path = _private(first_export_lock_path)
    lock = read_json(lock_path)
    require(
        lock.get("manifest_sha256")
        == digest((root / "release-manifest.json").read_bytes())
        and lock.get("deployment_root") == str(root)
        and lock.get("compose_sha256") == attempt.get("export_compose_sha256"),
        "first_export_lock_changed",
    )
    stacks = {
        project: no_links(lock_path.parent / project / "compose.yaml")
        for project in (CORE_PROJECT, OBS_PROJECT)
    }
    require(
        all(
            stack.is_file() and digest(stack.read_bytes())
            == attempt["export_compose_sha256"][project]
            for project, stack in stacks.items()
        ),
        "first_export_compose_changed",
    )
    repository, final_output = _exporter_ready(
        root, export_repository, final_export_output
    )
    require(str(repository) == attempt.get("export_repository"),
            "fixed_export_repository_changed")
    _trusted_export_matches(
        root, lock_path, stacks, repository, compare_lock=False,
    )
    _platform_only(root, stacks, expected_id=initial.get("platform_container_id"))
    _write_private(work / "reauthorization-attempt.json", {
        "state": "started", "automatic_retry": False,
        "old_expires_at": initial["expires_at"],
    })
    base = ["docker", "compose", "--project-directory", str(root),
            "-f", str(stacks[CORE_PROJECT])]
    try:
        receipt = _local(
            base, work, "issue", {"entry_id": "config-entry"}, root,
            marker="reauthorization-issue",
        )
        require(
            receipt.get("assertion_ref") != old_ref,
            "new_gateway_origin_required",
        )
        new_expiry = _install_ref(root, receipt, origin)
        _write_private(work / "reauthorization-result.json", {
            "state": "manual_reauthorized",
            "issuer": "fixed_product_public_cli",
            "gateway_ref_sha256": digest(receipt["assertion_ref"].encode()),
            "expires_at": new_expiry,
            "automatic_retry": False,
            "new_export_lock_required": True,
            "release_ready": False,
        })
        _write_private(work / "reauthorization-final-export-attempt.json", {
            "stage": "reauthorization-final-export", "state": "started",
            "automatic_retry": False,
        })
        _trusted_export(root, repository, final_output)
        final = finalize(
            root, export_lock_path=final_output / "resident-export.lock.json"
        )
        return {
            "status": "manual_reauthorized_export_verified",
            "expires_at": new_expiry,
            "final_export": str(final_output),
            "remaining_seconds_at_check": final["remaining_seconds_at_check"],
            "release_ready": False,
        }
    except Exception as exc:
        code = str(exc) if isinstance(exc, Refused) else "unexpected_reauthorization_failure"
        _write_private(work / "reauthorization-failure.json", {
            "state": "needs_diagnosis", "code": code, "automatic_retry": False,
        })
        raise Refused(code) from None


def finalize(bundle_root, *, export_lock_path):
    """Read back the post-issue A3 export while only Platform remains running."""
    require(os.name == "posix", "linux_activation_required")
    root = no_links(Path(bundle_root))
    require(root.is_absolute() and root.is_dir(), "bundle_root_invalid")
    verify_integrity(root)
    manifest = read_json(root / "release-manifest.json")
    _fixed(manifest)
    prepared = _compose(root, None)
    work = root / "reports" / "resident-install"
    renewed = (work / "reauthorization-result.json").is_file()
    final_path = work / (
        "final-export-after-renewal.json" if renewed else "final-export.json"
    )
    require(
        (work / "result.json").is_file()
        and not final_path.exists(),
        "final_export_stage_invalid",
    )
    result = read_json(work / "result.json")
    attempt = read_json(work / "attempt.json")
    require(
        result.get("state") == "authority_initialized_pending_final_export"
        and attempt.get("state") == "started",
        "authority_result_invalid",
    )
    require(
        not (work / "reauthorization-attempt.json").exists()
        or (work / "reauthorization-result.json").is_file(),
        "manual_reauthorization_uncertain",
    )
    current = (
        read_json(work / "reauthorization-result.json")
        if (work / "reauthorization-result.json").is_file()
        else result
    )
    _current_ref(root, current.get("gateway_ref_sha256"))
    _, stacks, hashes = _export_lock(
        root, export_lock_path, manifest, read_json(prepared)
    )
    _trusted_export_matches(
        root, export_lock_path, stacks, attempt.get("export_repository"),
        compare_lock=True,
    )
    require(
        hashes == attempt["export_compose_sha256"],
        "post_issue_stack_configuration_changed",
    )
    try:
        expiry = datetime.fromisoformat(current["expires_at"].replace("Z", "+00:00"))
        remaining = expiry.timestamp() - datetime.now(timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Refused("product_issue_expiry_invalid") from None
    require(
        expiry.tzinfo is not None and remaining >= 120,
        "post_issue_origin_budget_insufficient",
    )
    _platform_only(
        root, stacks, expected_id=result.get("platform_container_id")
    )
    report = {
        "state": "final_export_verified",
        "running_services": ["platform"],
        "remaining_seconds_at_check": int(remaining),
        "source_expires_at": current["expires_at"],
        "gateway_ref_sha256": current["gateway_ref_sha256"],
        "export_lock_sha256": digest(_private(export_lock_path).read_bytes()),
        "manual_reauthorization": renewed,
        "provider_configured": result["provider"] == "configured",
        "release_ready": False,
    }
    _write_private(final_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    for option in ("manifest", "site", "contracts", "credentials",
                   "admin-password-file", "bundle-root"):
        prep.add_argument("--" + option, required=True, type=Path)
    prep.add_argument("--admin-username", required=True)
    run = sub.add_parser("activate")
    run.add_argument("--bundle-root", required=True, type=Path)
    run.add_argument("--export-lock", required=True, type=Path)
    run.add_argument("--export-repository", required=True, type=Path)
    run.add_argument("--final-export-output", required=True, type=Path)
    run.add_argument("--provider-publication-file", type=Path)
    finish = sub.add_parser("finalize")
    finish.add_argument("--bundle-root", required=True, type=Path)
    finish.add_argument("--export-lock", required=True, type=Path)
    renew = sub.add_parser("reauthorize")
    renew.add_argument("--bundle-root", required=True, type=Path)
    renew.add_argument("--first-export-lock", required=True, type=Path)
    renew.add_argument("--export-repository", required=True, type=Path)
    renew.add_argument("--final-export-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare(args.manifest, args.site, args.contracts,
                             args.credentials, args.admin_password_file,
                             args.admin_username, args.bundle_root)
        elif args.action == "activate":
            result = activate(args.bundle_root, export_lock_path=args.export_lock,
                              export_repository=args.export_repository,
                              final_export_output=args.final_export_output,
                              publication_path=args.provider_publication_file)
        elif args.action == "finalize":
            result = finalize(args.bundle_root, export_lock_path=args.export_lock)
        else:
            result = reauthorize(
                args.bundle_root, first_export_lock_path=args.first_export_lock,
                export_repository=args.export_repository,
                final_export_output=args.final_export_output,
            )
    except Refused as exc:
        print(json.dumps({"status": "refused", "code": str(exc)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({"status": "refused", "code": "unexpected_install_failure"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
