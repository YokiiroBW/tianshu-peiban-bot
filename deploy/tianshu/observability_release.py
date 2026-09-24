"""Compose the pinned DEP-B package via its public CLIs, without changing its source."""

import json
import subprocess
import sys
from pathlib import Path

from manifest import digest, inside, load_manifest, read_json, require, write_json
from observability_contract import COMPONENTS, legacy_view


def export_package(repository, manifest, destination):
    spec = manifest["observability"]
    commit, prefix = spec["source"]["commit"], spec["package_path"]
    require(not destination.exists(), "observability_code_target_exists")
    names = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(repository),
                "ls-tree",
                "-r",
                "--name-only",
                commit,
                prefix,
            ],
            timeout=30,
        )
        .decode()
        .splitlines()
    )
    require(bool(names), "observability_source_missing")
    destination.mkdir(parents=True)
    hashes = {}
    for name in names:
        member = name.removeprefix(prefix + "/")
        target = inside(destination, member)
        raw = subprocess.check_output(
            ["git", "-C", str(repository), "show", commit + ":" + name], timeout=30
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        hashes[member] = digest(raw)
    return hashes


def verify_layout(root, manifest):
    directory = inside(root, manifest["observability"]["output_relative"])
    stack = read_json(directory / "compose.yaml")
    from resource_profile import validate, constrain

    profile = validate(
        read_json(root / "deployment.json")["compose_inputs"].get("resource_profile")
    )
    if profile is not None:
        for service in stack["services"].values():
            require(
                service == constrain(dict(service), profile),
                "observability_resource_profile_mismatch",
            )
    require(
        set(stack["services"]) == {"obs-" + c for c in COMPONENTS},
        "observability_composition_mismatch",
    )
    expected = {
        (v["owner_service"], str(inside(root, v["host_path"])), v["container_path"])
        for v in manifest["volumes"]
        if v["product"] == "observability"
    }
    actual = {
        (name, str(Path(v["source"]).absolute()), v["target"])
        for name, service in stack["services"].items()
        for v in service["volumes"]
        if v.get("read_only", False) is not True
    }
    require(actual == expected, "observability_write_mount_mismatch")
    for _, source, _ in actual:
        require(Path(source).is_dir(), "observability_storage_missing")
    binding = read_json(directory / "binding.json")
    require(
        binding["release_id"] == manifest["release_id"]
        and binding["manifest_sha256"]
        == digest(json.dumps(legacy_view(manifest), sort_keys=True).encode()),
        "observability_legacy_binding_mismatch",
    )
    return stack


def apply_resource_profile(compose_path, profile):
    """Apply the deployment CPU-set policy to an older pinned DEP-B Compose output."""
    from resource_profile import constrain, validate

    profile = validate(profile)
    if profile is None:
        return
    document = read_json(compose_path)
    require(
        isinstance(document, dict) and isinstance(document.get("services"), dict),
        "observability_composition_invalid",
    )
    for service in document["services"].values():
        require(isinstance(service, dict), "observability_service_invalid")
        constrain(service, profile)
    write_json(compose_path, document)


def configure(root, settings_path, repository, projects=None):
    from bundle import verify_integrity

    root = root.absolute()
    verify_integrity(root)
    from bundle import preflight

    preflight(root)
    profile = read_json(root / "deployment.json")["compose_inputs"].get(
        "resource_profile"
    )
    manifest = load_manifest(root / "release-manifest.json")
    require(manifest["schema_version"] == "1.1.0", "observability_manifest_required")
    settings = read_json(settings_path)
    require(
        settings.get("resource_profile", profile) == profile,
        "observability_resource_profile_override",
    )
    if profile is not None:
        settings["resource_profile"] = profile
    require(
        settings["grafana_hostname"] == "logs.internal",
        "manifest_grafana_name_mismatch",
    )
    # Every input is copied under this new bundle; secrets never enter a report.
    source_root = settings_path.absolute().parent
    material = {}
    for group in ("tls", "secrets"):
        for name, relative in settings[group].items():
            raw = inside(source_root, relative).read_bytes()
            target = "observability-input/" + group + "/" + name
            material[target] = raw
            settings[group][name] = target
    require(not (root / "observability-input").exists(), "observability_input_exists")
    require(not (root / "observability").exists(), "observability_output_exists")
    (root / "INCOMPLETE").write_text("Observability composition in progress\n")
    package = root / "tools/observability"
    hashes = export_package(repository, manifest, package)
    for name, raw in material.items():
        file = inside(root, name)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
        file.chmod(0o640)
    settings_file = root / "observability-input/settings.json"
    write_json(settings_file, settings)
    view_file = root / "observability-input/legacy-view.json"
    write_json(view_file, legacy_view(manifest))
    snapshot = package / "vocabulary.json"
    if projects is not None:
        commits = root / "observability-input/commits.json"
        write_json(
            commits, {p: v["source"]["commit"] for p, v in manifest["products"].items()}
        )
        snapshot = root / "observability-input/vocabulary.json"
        invoke(
            [
                package / "snapshot.py",
                "--projects",
                projects,
                "--commits",
                commits,
                "--contract",
                root / "contracts/diagnostics/v1",
                "--output",
                snapshot,
            ]
        )
    invoke(
        [
            package / "configure.py",
            "--deployment-root",
            root,
            "--release-manifest",
            view_file,
            "--settings",
            settings_file,
            "--snapshot",
            snapshot,
            "--output-relative",
            "observability",
            "--candidate",
        ]
    )
    apply_resource_profile(root / "observability/compose.yaml", profile)
    verify_layout(root, manifest)
    project = read_json(root / "deployment.json")["project_name"] + "-obs"
    binding = {
        "schema_version": "dep-e-observability/1",
        "source": manifest["observability"]["source"],
        "release_manifest_sha256": digest(
            (root / "release-manifest.json").read_bytes()
        ),
        "legacy_view_sha256": digest(view_file.read_bytes()),
        "code_sha256": hashes,
        "snapshot_sha256": digest(snapshot.read_bytes()),
        "project_name": project,
        "compose_command": [
            "docker",
            "compose",
            "-p",
            project,
            "--project-directory",
            str(root / "observability"),
            "-f",
            str(root / "observability/compose.yaml"),
        ],
        "status": "configured_not_started",
        "application_reclamation_authorized": False,
    }
    write_json(root / "observability-release.json", binding)
    inventory = read_json(root / "bundle-integrity.json")
    for folder in ("tools/observability", "observability-input", "observability"):
        for file in (root / folder).rglob("*"):
            if file.is_dir():
                file.chmod(0o750)
            elif not file.is_relative_to(root / "observability/data"):
                inventory["files"][file.relative_to(root).as_posix()] = digest(
                    file.read_bytes()
                )
    inventory["files"]["observability-release.json"] = digest(
        (root / "observability-release.json").read_bytes()
    )
    write_json(root / "bundle-integrity.json", inventory)
    (root / "INCOMPLETE").unlink()
    return {
        "status": "observability_configured",
        "release_ready": False,
        "started_services": False,
    }


def invoke(arguments):
    result = subprocess.run(
        [sys.executable, "-B", *map(str, arguments)], capture_output=True, timeout=60
    )
    require(result.returncode == 0, "observability_public_cli_failed")


def reconcile(root, generated, url, ca, token, start_ns, end_ns, report):
    """Only explicit synthetic loopback queries; result remains separate from release proof."""
    import ipaddress
    from urllib.parse import urlsplit
    from bundle import verify_integrity

    verify_integrity(root)
    parsed = urlsplit(url)
    require(
        parsed.scheme == "https"
        and ipaddress.ip_address(parsed.hostname).is_loopback
        and not parsed.username
        and not parsed.password,
        "synthetic_loopback_query_required",
    )
    require(not report.exists(), "new_report_required")
    manifest = load_manifest(root / "release-manifest.json")
    verify_layout(root, manifest)
    roots = {
        v["product"]: str(inside(root, v["host_path"]))
        for v in manifest["volumes"]
        if v["category"] == "logs" and v["mount"]
    }
    # Keep compatibility input outside the immutable bundle, beside the new evidence.
    roots_path = report.with_suffix(".roots.json")
    require(not roots_path.exists(), "new_report_required")
    write_json(roots_path, roots)
    snapshot = root / "observability-input/vocabulary.json"
    if not snapshot.exists():
        snapshot = root / "tools/observability/vocabulary.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tools/observability/reconcile.py"),
            "--generated",
            str(generated),
            "--roots",
            str(roots_path),
            "--snapshot",
            str(snapshot),
            "--loki-url",
            url,
            "--ca",
            str(ca),
            "--token-file",
            str(token),
            "--start-ns",
            str(start_ns),
            "--end-ns",
            str(end_ns),
            "--output",
            str(report),
        ],
        capture_output=True,
        timeout=60,
    )
    value = read_json(report)
    require(
        value.get("application_reclamation_authorized") is False,
        "reclamation_not_authorized",
    )
    return {
        "status": "log_reconciled"
        if result.returncode == 0
        else "log_reconciliation_failed",
        "release_ready": False,
        "application_reclamation_authorized": False,
        "report_sha256": digest(report.read_bytes()),
        "release_manifest_sha256": digest(
            (root / "release-manifest.json").read_bytes()
        ),
    }
