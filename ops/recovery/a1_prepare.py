"""Explicit, one-way static preparation for a fresh A1 recovery scope."""

import argparse
import copy
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .a1_once import (_code_files, _code_tree, _network_plan, _ports,
                      _posix_path_key, _sha, _write_once)
from .a1_code import deploy_module, require_entry_origin
from . import a1_gateway_cli
from .safety import RecoveryError, canonical, file_hash, read_json, require

PREP_KEYS = {"schema_version", "scope_parent", "scope_name", "scope_id",
             "code_root", "code_tree_sha256", "code_lock_sha256", "python", "docker",
             "allocation_file", "allocation_sha256", "execution_id",
             "original_manifest", "original_manifest_sha256", "contracts_root",
             "resource_profile", "resource_profile_sha256",
             "observability_repository", "observability_repository_sha256",
             "projects_root",
             "source_project", "clone_project", "networks", "ports", "run_label",
             "restored_name", "clone_name", "clone_inputs_name", "backup_name",
             "permit_name", "receipt_name", "gateway_pythonpath",
             "gateway_runtime_lock", "gateway_runtime_lock_sha256"}
NETWORK_NAMES = {"source": ("core", "egress", "frontend", "observe", "storage"),
                 "clone": ("core", "egress", "frontend", "access", "observe",
                           "storage", "observability_access")}
PORT_NAMES = ("platform", "companion", "memory", "gateway", "obs-grafana", "obs-guard")
FIXED_ORIGINAL_MANIFEST_SHA256 = (
    "ce68ed58f4dbbaac6b9a09fc3632fd2522afc97501a63b790e1447b0af9fc4e2"
)


def _tree(root):
    root = Path(root)
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: _posix_path_key(root, item)):
        if ".git" in path.parts or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        require(not path.is_symlink(), "a1_prep_repository_linked")
        if path.is_file():
            rows.append((path.relative_to(root).as_posix(), file_hash(path)))
    require(rows, "a1_prep_repository_empty")
    return _sha(canonical(rows))


def _git_commit_digest(repository, commit):
    result = subprocess.run(["git", "-C", str(repository), "cat-file", "-p", commit],
                            capture_output=True, timeout=30)
    require(result.returncode == 0 and result.stdout.startswith(b"tree "),
            "a1_prep_fixed_commit_missing")
    return _sha(result.stdout)


def load(path, *, phase="source"):
    path = Path(path)
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),
            "a1_prep_config_path_invalid")
    c = read_json(path)
    require(type(c) is dict and set(c) == PREP_KEYS and
            c["schema_version"] == "a1-preparation/1", "a1_prep_schema_invalid")
    parent = Path(c["scope_parent"])
    code = Path(c["code_root"])
    require(parent.is_absolute() and parent.is_dir() and not parent.is_symlink() and
            code.is_absolute() and code.is_dir() and not code.is_symlink() and
            parent.resolve(strict=True) == parent and code.resolve(strict=True) == code and
            code == parent / "tooling" and
            path == parent / "preparations" / (c["scope_name"] + ".json"),
            "a1_prep_path_invalid")
    require(c["scope_name"].startswith("scope-a1-") and
            all(char.islower() or char.isdigit() or char == "-" for char in c["scope_name"]) and
            str(uuid.UUID(c["scope_id"])) == c["scope_id"], "a1_prep_scope_invalid")
    scope = parent / c["scope_name"]
    require(not scope.exists() if phase == "source" else scope.is_dir(),
            "a1_prep_scope_state_invalid")
    require(_code_tree(code) == c["code_tree_sha256"] and
            file_hash(parent / "preparations" / (c["scope_name"] + ".code-lock.json")) ==
            c["code_lock_sha256"] and
            read_json(parent / "preparations" / (c["scope_name"] + ".code-lock.json")) ==
            {"schema_version": "a1-code-lock/1", "tree_sha256": c["code_tree_sha256"],
             "files": dict(_code_files(code))}, "a1_prep_code_lock_mismatch")
    for name in ("python", "docker", "original_manifest", "resource_profile"):
        item = Path(c[name])
        require(item.is_absolute() and item.is_file() and not item.is_symlink(),
                "a1_prep_input_missing")
    for name in ("contracts_root", "observability_repository", "projects_root"):
        item = Path(c[name])
        require(item.is_absolute() and item.is_dir() and not item.is_symlink(),
                "a1_prep_input_missing")
    require(type(c["gateway_pythonpath"]) is dict and c["gateway_pythonpath"] and
            all(Path(item).is_absolute() and Path(item).is_dir() and
                _tree(item) == checksum
                for item, checksum in c["gateway_pythonpath"].items()),
            "a1_prep_gateway_code_mismatch")
    a1_gateway_cli.verify_preparation(c)
    original = read_json(c["original_manifest"])
    require(file_hash(c["original_manifest"]) == c["original_manifest_sha256"] and
            file_hash(c["resource_profile"]) == c["resource_profile_sha256"] and
            _git_commit_digest(c["observability_repository"],
                original["observability"]["source"]["commit"]) ==
                c["observability_repository_sha256"],
            "a1_prep_input_changed")
    _network_plan(c["networks"], c)
    _ports(c["ports"])
    require(c["source_project"].startswith("tianshu-qa-a1-") and
            c["clone_project"].startswith("tianshu-qa-a1-") and
            c["source_project"] != c["clone_project"] and
            c["run_label"].startswith("a1-") and
            all(char.islower() or char.isdigit() or char == "-" for char in c["run_label"]),
            "a1_prep_names_invalid")
    from .a1_once import NAME
    for name in ("restored_name", "clone_name", "clone_inputs_name", "backup_name",
                 "permit_name", "receipt_name"):
        require(type(c[name]) is str and NAME.fullmatch(c[name]) is not None,
                "a1_prep_name_invalid")
    derive_manifest(c)
    manifest = original
    repositories = [(Path(c["observability_repository"]),
                     manifest["observability"]["source"]["repo"],
                     manifest["observability"]["source"]["commit"])]
    repositories += [(Path(c["projects_root"]) / row["source"]["repo"],
                      row["source"]["repo"], row["source"]["commit"])
                     for row in manifest["products"].values()]
    for repository, name, commit in repositories:
        require(repository.is_dir() and not repository.is_symlink() and
                repository.name == name and repository.resolve(strict=True) == repository,
                "a1_prep_repository_invalid")
        result = subprocess.run(["git", "-C", str(repository), "cat-file", "-t", commit],
                                capture_output=True, timeout=30)
        require(result.returncode == 0 and result.stdout.strip() == b"commit",
                "a1_prep_fixed_commit_missing")
    return c


def derive_manifest(c):
    """Only web_text_dialogue.enabled may differ from the pinned original."""
    require(file_hash(c["original_manifest"]) == FIXED_ORIGINAL_MANIFEST_SHA256,
            "a1_prep_original_manifest_mismatch")
    original = read_json(c["original_manifest"])
    require(type(original) is dict and original.get("status") == "candidate" and
            original.get("evidence") == [], "a1_prep_manifest_invalid")
    features = original["features"]
    target = [row for row in features if row.get("id") == "web_text_dialogue"]
    require(len(target) == 1 and target[0]["enabled"] is True,
            "a1_prep_original_feature_invalid")
    derived = copy.deepcopy(original)
    next(row for row in derived["features"] if row["id"] == "web_text_dialogue")[
        "enabled"] = False
    check = copy.deepcopy(derived)
    next(row for row in check["features"] if row["id"] == "web_text_dialogue")[
        "enabled"] = True
    require(check == original, "a1_prep_manifest_not_single_field")
    return derived


def make_tls(directory, *, lan_address=None):
    """Isolated CA with complete synthetic identifiers; no CA key persisted."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                                            "DEP-G ISOLATED SYNTHETIC CA")])
    ca = (x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
              key_encipherment=False, data_encipherment=False, key_agreement=False,
              key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None),
              critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                         critical=False).sign(ca_key, hashes.SHA256()))
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for role in ("platform", "companion", "memory", "gateway", "logs",
                 "obs-guard", "obs-loki", "obs-vector", "obs-prometheus", "obs-client"):
        dest = directory / role
        dest.mkdir(mode=0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        names = [x509.DNSName(role if role.startswith("obs-") else role + ".internal"),
                 x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        if role == "platform":
            names.append(x509.DNSName("console.synthetic.test"))
            if lan_address is not None:
                names.append(x509.IPAddress(ipaddress.ip_address(lan_address)))
        cert = (x509.CertificateBuilder().subject_name(x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC " + role)]))
                .issuer_name(issuer).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                               critical=True)
                .add_extension(x509.SubjectAlternativeName(names), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .add_extension(x509.ExtendedKeyUsage([
                    ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
                    critical=False).sign(ca_key, hashes.SHA256()))
        (dest / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private = dest / "server.key"
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()))
        private.chmod(0o600)


def _obs_settings(scope, c):
    inputs = scope / "inputs"
    base = inputs / "a1-observability"
    base.mkdir(mode=0o700)
    tls = base / "tls"
    tls.mkdir(mode=0o700)
    source = inputs / "tls"
    mapping = {"ca.pem": "ca.pem", "client-ca.pem": "ca.pem",
               "guard.pem": "obs-guard/server.pem", "guard.key": "obs-guard/server.key",
               "loki.pem": "obs-loki/server.pem", "loki.key": "obs-loki/server.key",
               "vector.pem": "obs-vector/server.pem", "vector.key": "obs-vector/server.key",
               "prometheus.pem": "obs-prometheus/server.pem",
               "prometheus.key": "obs-prometheus/server.key",
               "grafana.pem": "logs/server.pem", "grafana.key": "logs/server.key",
               "client.pem": "obs-client/server.pem", "client.key": "obs-client/server.key"}
    for name, relative in mapping.items():
        target = tls / name
        target.write_bytes((source / relative).read_bytes())
        target.chmod(0o600)
    private = base / "secrets"
    private.mkdir(mode=0o700)
    for name in ("writer_token", "query_token", "metrics_token",
                 "grafana_admin_password"):
        target = private / name
        target.write_text(secrets.token_urlsafe(48) + "\n")
        target.chmod(0o600)
    settings = {"grafana_hostname": "logs.internal",
        "ports": {"grafana": c["ports"]["source"][4],
                  "query": c["ports"]["source"][5]},
        "log_budgets": {name: 1073741824 for name in
                        ("platform", "companion", "memory", "gateway")},
        "tls": {name: "tls/" + name for name in mapping},
        "secrets": {name: "secrets/" + name for name in
                    ("writer_token", "query_token", "metrics_token",
                     "grafana_admin_password")},
        "image_digests": {},
        "network_subnets": {"observe": c["networks"]["source"][3],
                            "storage": c["networks"]["source"][4]}}
    path = base / "settings.json"
    path.write_bytes(json.dumps(settings, sort_keys=True, indent=2).encode() + b"\n")
    path.chmod(0o600)
    return path


def prepare_source(c, *, initializer=None, observability=None, synthetic=None,
                   runtime_checker=None):
    """Create a fresh source bundle; failures leave partial artifacts for review."""
    parent = Path(c["scope_parent"])
    scope = parent / c["scope_name"]
    require(not scope.exists(), "a1_prep_scope_not_empty")
    runtime = (runtime_checker or a1_gateway_cli.verify_runtime)(c)
    require(type(runtime) is dict and runtime.get("status") == "gateway_runtime_ready",
            "a1_prep_gateway_runtime_preflight_invalid")
    derived = derive_manifest(c)
    manifest_file = tempfile.NamedTemporaryFile(mode="wb", prefix="a1-manifest-",
        suffix=".json", dir=parent / "preparations", delete=False)
    manifest_path = Path(manifest_file.name)
    try:
        with manifest_file as stream:
            stream.write(json.dumps(derived, ensure_ascii=False, sort_keys=True,
                                    indent=2).encode() + b"\n")
        manifest_path.chmod(0o600)
        source_nets = c["networks"]["source"]
        if initializer is None:
            synthetic_init = deploy_module(c["code_root"], "synthetic_init")
            old_tls = synthetic_init.tls
            synthetic_init.tls = make_tls
            try:
                result = synthetic_init.create(scope, manifest_path,
                    Path(c["contracts_root"]), c["source_project"], source_nets[0],
                    c["ports"]["source"][0],
                    resource_profile=read_json(c["resource_profile"]),
                    auxiliary_subnets={"egress": source_nets[1],
                                       "frontend": source_nets[2]},
                    recovery_scope_id=c["scope_id"])
            finally:
                synthetic_init.tls = old_tls
        else:
            result = initializer(scope, manifest_path, c)
        source = scope / "deployments/source"
        require(Path(result) == source and source.is_dir(),
                "a1_prep_source_initialization_failed")
    finally:
        manifest_path.unlink(missing_ok=True)
    settings = _obs_settings(scope, c)
    if observability is None:
        observability = deploy_module(c["code_root"], "observability_release").configure
    observed = observability(source, settings, Path(c["observability_repository"]),
                             projects=Path(c["projects_root"]))
    require(observed.get("status") == "observability_configured",
            "a1_prep_observability_failed")
    if synthetic is None:
        from .a1_acceptance import build_synthetic_inputs, prepare_synthetic
        synthetic = (prepare_synthetic, build_synthetic_inputs)
    prepared = synthetic[0](source,
        api_ports=dict(zip(PORT_NAMES[1:4], c["ports"]["source"][1:4])),
        tooling_root=c["code_root"])
    require(prepared.get("status") == "prepared" and
            prepared.get("synthetic_only") is True and
            type(prepared.get("publication")) is dict and
            prepared["publication"].get("config_version") == 1 and
            type(prepared["publication"].get("providers")) is list and
            len(prepared["publication"]["providers"]) == 1 and
            prepared["publication"]["providers"][0].get("provider_id") ==
                "provider-synthetic",
            "a1_prep_synthetic_configuration_failed")
    _write_once(scope / "inputs/model-publication-template.json",
                prepared["publication"])
    report = source / "reports" / c["run_label"]
    generated = synthetic[1](source, report)
    require(generated.get("synthetic_only") is True and
            len(generated.get("inputs", [])) == 9,
            "a1_prep_synthetic_inputs_failed")
    config_path = Path(c["scope_parent"]) / "preparations" / (c["scope_name"] + ".json")
    return {"status": "static_source_prepared", "scope_id": c["scope_id"],
            "preparation_config_sha256": file_hash(config_path),
            "allocation_sha256": c["allocation_sha256"],
            "source_manifest_sha256": file_hash(source / "release-manifest.json"),
            "synthetic_input_count": 9,
            "gateway_runtime_preflight": runtime,
            "source_directory": str(source)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--phase", choices=("source",), default="source")
    args = parser.parse_args(argv)
    require_entry_origin(read_json(args.config)["code_root"], __file__,
                         "ops/recovery/a1_prepare.py")
    c = load(args.config, phase=args.phase)
    if not args.execute:
        result = {"status": "planned", "scope_id": c["scope_id"],
                  "preparation_config_sha256": file_hash(args.config),
                  "allocation_sha256": c["allocation_sha256"],
                  "derived_manifest_sha256": _sha(canonical(derive_manifest(c))),
                  "source_directory": str(Path(c["scope_parent"]) /
                                          c["scope_name"] / "deployments/source")}
    else:
        result = prepare_source(c)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "rejected", "reason": "invalid_or_unavailable_input"}))
        raise SystemExit(2)
