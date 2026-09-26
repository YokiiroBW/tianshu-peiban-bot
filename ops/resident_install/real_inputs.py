"""Prepare operator-owned resident inputs; never invent image digests.

``seed`` creates long-lived private material once. ``render`` binds that
material to a reviewed NAS plan in a new directory. A pending image pin never
produces an executable manifest or OBS settings file.
"""

import argparse
import ipaddress
import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "tianshu"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEPLOY))

from configuration import load_inputs, references, tls_check  # noqa: E402
from manifest import PRODUCTS, load_manifest, no_links  # noqa: E402
from ops.resident_install.install import FIXED_COMMITS, FIXED_OBSERVABILITY  # noqa: E402

OBS_NAMES = ("vector", "loki", "grafana", "prometheus", "guard")
OBS_SECRETS = (
    "writer_token", "query_token", "metrics_token", "grafana_admin_password",
)
PIN_NAMES = (*PRODUCTS, *OBS_NAMES)
PIN = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _put(path, raw, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        os.chmod(path, mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path, value, mode=0o600):
    _put(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(), mode)


def _fresh(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("fresh_absolute_output_required")
    path = no_links(path)
    if not path.parent.is_dir() or path.exists():
        raise ValueError("fresh_absolute_output_required")
    path.mkdir(mode=0o700)
    os.chmod(path, 0o700)
    return path


def _templates():
    return {
        name: _read(DEPLOY / "templates" / (name + ".json"))
        for name in PRODUCTS
    }


def _ca():
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Tianshu resident operator"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Tianshu resident private root CA"),
    ])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _key_bytes(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _cert_bytes(certificate):
    return certificate.public_bytes(serialization.Encoding.PEM)


def seed(output):
    output = _fresh(output)
    required = set().union(*(references(config) for config in _templates().values()))
    required -= {"TIANSHU_DIAGNOSTICS_TOKEN", "TS_ADMIN_PASSWORD"}
    required.update("TS_DIAG_" + name.upper() for name in PRODUCTS)
    credentials = {name: secrets.token_urlsafe(48) for name in sorted(required)}
    # This is deliberately not an origin assertion. The fixed Platform CLI
    # replaces it during activate; no pre-issued authority is represented.
    credentials["TS_GATEWAY_ORIGIN"] = (
        "PENDING_PLATFORM_ISSUE_" + secrets.token_urlsafe(24)
    )
    _json(output / "credentials.json", credentials)
    _put(output / "admin-password.txt", (secrets.token_urlsafe(36) + "\n").encode())
    for name in OBS_SECRETS:
        _put(output / "obs-secrets" / name, secrets.token_urlsafe(48).encode())
    key, certificate = _ca()
    _put(output / "ca.key", _key_bytes(key))
    _put(output / "ca.pem", _cert_bytes(certificate), 0o644)
    return {"status": "private_seed_created", "output": str(output)}


def _plan(value):
    required = {
        "deployment_root", "bind_address", "web_port", "subnets",
        "service_ips", "obs_ports", "image_pins",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("plan_shape_invalid")
    root = value["deployment_root"]
    if (not isinstance(root, str) or not root.startswith("/")
            or "//" in root or any(part in {"", ".", ".."} for part in root[1:].split("/"))
            or len(root.split("/")) < 3):
        raise ValueError("deployment_root_invalid")
    ip = ipaddress.ip_address(value["bind_address"])
    if ip.version != 4 or not ip.is_private:
        raise ValueError("private_bind_address_required")
    port = value["web_port"]
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("web_port_invalid")
    if set(value["subnets"]) != {
        "core", "egress", "frontend", "observe", "storage", "access",
    }:
        raise ValueError("six_subnets_required")
    networks = [ipaddress.ip_network(item, strict=True) for item in value["subnets"].values()]
    if not all(n.version == 4 and n.is_private and 24 <= n.prefixlen <= 28 for n in networks):
        raise ValueError("private_subnet_required")
    if any(a.overlaps(b) for i, a in enumerate(networks) for b in networks[i + 1:]):
        raise ValueError("subnets_overlap")
    core = ipaddress.ip_network(value["subnets"]["core"])
    if set(value["service_ips"]) != set(PRODUCTS):
        raise ValueError("service_ip_set_invalid")
    addresses = [ipaddress.ip_address(value["service_ips"][name]) for name in PRODUCTS]
    if (len(set(addresses)) != 4 or any(address not in core for address in addresses)
            or any(address in {core.network_address, core.network_address + 1,
                               core.broadcast_address} for address in addresses)):
        raise ValueError("service_ips_invalid")
    ports = value["obs_ports"]
    if (set(ports) != {"grafana", "query"}
            or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports.values())
            or len(set(ports.values()) | {port}) != 3):
        raise ValueError("observability_ports_invalid")
    if set(value["image_pins"]) != set(PIN_NAMES):
        raise ValueError("nine_image_pins_required")
    return value


def _pins(plan, manifest, versions):
    missing = []
    for name in PIN_NAMES:
        pin = plan["image_pins"][name]
        if pin is None:
            missing.append(name)
            continue
        if not isinstance(pin, dict) or set(pin) != {"reference", "digest", "repo_digest"}:
            raise ValueError("image_pin_shape_invalid:" + name)
        reference, digest, observed = (pin[k] for k in ("reference", "digest", "repo_digest"))
        if not isinstance(reference, str) or not isinstance(digest, str) or not PIN.fullmatch(digest):
            raise ValueError("image_pin_invalid:" + name)
        repository = reference.rsplit(":", 1)[0] if ":" in reference.rsplit("/", 1)[-1] else reference
        if observed != repository + "@" + digest:
            raise ValueError("observed_repo_digest_mismatch:" + name)
        if name in PRODUCTS:
            original = manifest["products"][name]["image"]["reference"]
            expected = "127.0.0.1:19550/" + original + "-resident1"
            if reference != expected:
                raise ValueError("product_image_reference_changed:" + name)
        elif reference != versions["images"][name]["reference"]:
            raise ValueError("observability_image_reference_changed:" + name)
    return missing


def _leaf(ca_key, ca, common_name, dns_names, ip_names, *, client=False):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    names = [x509.DNSName(name) for name in sorted(set(dns_names))]
    names += [x509.IPAddress(ipaddress.ip_address(name)) for name in sorted(set(ip_names))]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=180))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([
            ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH,
        ]), critical=False)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return key, certificate


def _write_leaf(folder, ca_key, ca, common_name, dns_names, ip_names, *, client=False,
                prefix="server"):
    key, certificate = _leaf(ca_key, ca, common_name, dns_names, ip_names, client=client)
    _put(folder / (prefix + ".key"), _key_bytes(key))
    _put(folder / (prefix + ".pem"), _cert_bytes(certificate), 0o644)


def render(plan_path, seed_root, output):
    plan = _plan(_read(plan_path))
    seed_root = no_links(Path(seed_root))
    ca_key = serialization.load_pem_private_key((seed_root / "ca.key").read_bytes(), password=None)
    ca = x509.load_pem_x509_certificate((seed_root / "ca.pem").read_bytes())
    if ca_key.public_key().public_numbers() != ca.public_key().public_numbers():
        raise ValueError("private_ca_key_mismatch")
    template = _templates()
    manifest = _read(DEPLOY / "release-manifest.example.json")
    versions = _read(ROOT / "deploy" / "observability" / "versions.json")
    if {name: manifest["products"][name]["source"]["commit"] for name in PRODUCTS} != FIXED_COMMITS:
        raise ValueError("fixed_product_sources_changed")
    missing = _pins(plan, manifest, versions)
    output = _fresh(output)
    site_dir = output / "site"
    site_dir.mkdir(mode=0o700)
    obs_dir = output / "obs-input"
    obs_dir.mkdir(mode=0o700)
    # Only runtime credentials are copied into the transferable output. The
    # CA signing key remains in the local seed directory and must not go to NAS.
    for name in ("credentials.json", "admin-password.txt"):
        _put(output / "runtime-secrets" / name, (seed_root / name).read_bytes())
    site = _read(DEPLOY / "templates" / "deployment-input.example.json")
    site.update(
        project_name="tianshu-v2-resident",
        bind_address=plan["bind_address"], web_port=plan["web_port"],
        web_origin=f"http://{plan['bind_address']}:{plan['web_port']}",
        subnet=plan["subnets"]["core"], service_ips=plan["service_ips"],
        public_web=True,
        auxiliary_subnets={name: plan["subnets"][name] for name in ("egress", "frontend")},
        resource_profile={"kind": "nas-cpuset-resident-v1", "cpus": [6, 7],
                          "pid_limit": "unsupported"},
    )
    template["platform"]["web"]["origin"] = site["web_origin"]
    template["platform"]["web"]["username"] = "admin"
    template["platform"]["web_access"] = {
        "host": "0.0.0.0", "port": 8080,
        "initial": {"mode": "http", "origin": site["web_origin"], "certificate": None},
        "certificates": {"nas-web": template["platform"]["tls"]},
    }
    template["gateway"]["targets"][0]["addresses"] = [plan["service_ips"]["platform"]]
    for name in PRODUCTS:
        _json(site_dir / (name + ".json"), template[name], 0o640)
        _write_leaf(
            site_dir / "tls" / name, ca_key, ca, name + ".internal",
            [name + ".internal"],
            [plan["service_ips"][name],
             *([plan["bind_address"]] if name == "platform" else [])],
        )
    _put(site_dir / "tls" / "ca.pem", _cert_bytes(ca), 0o644)
    _json(site_dir / "deployment-input.json", site, 0o640)
    load_inputs(site_dir / "deployment-input.json")
    for name in PRODUCTS:
        names = [name + ".internal"]
        names.append(plan["service_ips"][name])
        if name == "platform":
            names.append(plan["bind_address"])
        tls_check(
            site_dir / "tls" / name / "server.pem",
            site_dir / "tls" / name / "server.key",
            site_dir / "tls" / "ca.pem", names,
        )
    _put(obs_dir / "tls" / "ca.pem", _cert_bytes(ca), 0o644)
    _put(obs_dir / "tls" / "client-ca.pem", _cert_bytes(ca), 0o644)
    for name in (*OBS_NAMES, "client"):
        dns = ["obs-" + name]
        if name == "grafana":
            dns.append("logs.internal")
        _write_leaf(
            obs_dir / "tls", ca_key, ca, dns[0], dns,
            ["127.0.0.1"] if name in {"grafana", "guard"} else [],
            client=name == "client", prefix=name,
        )
    for name in OBS_SECRETS:
        _put(obs_dir / "secrets" / name, (seed_root / "obs-secrets" / name).read_bytes())
    if missing:
        _json(output / "pending.json", {
            "status": "pending_real_repo_digests", "missing": missing,
            "executable_manifest": False,
        }, 0o644)
        return {"status": "pending_real_repo_digests", "missing": missing,
                "output": str(output)}
    manifest["observability"]["source"]["commit"] = FIXED_OBSERVABILITY
    for name in PRODUCTS:
        pin = plan["image_pins"][name]
        manifest["products"][name]["image"]["reference"] = pin["reference"]
        manifest["products"][name]["image"]["digest"] = pin["digest"]
    _json(output / "release-manifest.json", manifest, 0o644)
    load_manifest(output / "release-manifest.json")
    settings = {
        "grafana_hostname": "logs.internal",
        "ports": plan["obs_ports"],
        "log_budgets": dict.fromkeys(PRODUCTS, 1073741824),
        "network_subnets": {name: plan["subnets"][name]
                            for name in ("observe", "storage", "access")},
        "image_digests": {name: plan["image_pins"][name]["digest"] for name in OBS_NAMES},
        "tls": {name: "tls/" + name for name in (
            "ca.pem", "client-ca.pem", *(item + suffix for item in
                (*OBS_NAMES, "client") for suffix in (".pem", ".key")))},
        "secrets": {name: "secrets/" + name for name in OBS_SECRETS},
    }
    _json(obs_dir / "settings.json", settings, 0o640)
    _json(output / "ready.json", {
        "status": "real_candidate_inputs_ready", "executable_manifest": True,
        "deployment_root": plan["deployment_root"],
        "provider": "not_configured", "release_ready": False,
    }, 0o644)
    return {"status": "real_candidate_inputs_ready", "output": str(output)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("seed")
    create.add_argument("--output", type=Path, required=True)
    bind = commands.add_parser("render")
    bind.add_argument("--plan", type=Path, required=True)
    bind.add_argument("--seed", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = seed(args.output) if args.action == "seed" else render(
            args.plan, args.seed, args.output,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        code = str(error)
        if re.fullmatch(r"[a-z0-9_:]+", code) is None:
            code = "invalid_or_unavailable_input"
        print(json.dumps({"status": "refused", "code": code,
                          "release_ready": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] != "pending_real_repo_digests" else 3


if __name__ == "__main__":
    raise SystemExit(main())
