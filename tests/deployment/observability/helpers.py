"""Synthetic data and temporary certificate authority, only for isolated tests."""

from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import secrets
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy/observability"
sys.path.insert(0, str(PACKAGE))
from configure import TLS_FILES, TOKEN_FILES  # noqa: E402
from policy import canonical  # noqa: E402

SNAPSHOT = json.loads((PACKAGE / "vocabulary.json").read_bytes())


def event(seq=1, service="platform", instance=None, event_id=None):
    return {
        "schema_version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "instance_id": instance or "00000000-0000-4000-8000-000000000001",
        "sequence": seq,
        "event_id": event_id or str(uuid.uuid4()),
        "level": "INFO",
        "event": "runtime.started",
        "outcome": "succeeded",
        "correlation_id": None,
        "duration_ms": None,
        "error_code": None,
    }


def emit(path, records):
    with path.open("ab") as stream:
        for record in records:
            stream.write(canonical(record) + b"\n")
        stream.flush()
        import os

        os.fsync(stream.fileno())


def certificates(root, loki_ip=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    root.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-B SYNTHETIC TEST ONLY")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = ca.public_bytes(serialization.Encoding.PEM)
    (root / "ca.pem").write_bytes(pem)
    (root / "client-ca.pem").write_bytes(pem)
    for role in ("guard", "loki", "grafana", "prometheus", "vector", "client"):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-B synthetic " + role)]
        )
        addresses = [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        if role == "loki" and loki_ip is not None:
            addresses.append(x509.IPAddress(ipaddress.ip_address(loki_ip)))
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(name)
            .public_key(private.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("obs-" + role),
                        x509.DNSName("localhost"),
                        *addresses,
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.CLIENT_AUTH
                        if role == "client"
                        else ExtendedKeyUsageOID.SERVER_AUTH
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        (root / (role + ".pem")).write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (root / (role + ".key")).write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )


def deployment_fixture(root, contract_source):
    certificates(root / "tls")
    for product in ("platform", "companion", "memory", "gateway"):
        (root / "logs" / product).mkdir(parents=True)
    (root / "secrets").mkdir()
    for token in TOKEN_FILES:
        (root / "secrets" / token).write_text(secrets.token_urlsafe(32))
    destination = root / "contracts/diagnostics/v1"
    destination.mkdir(parents=True)
    for name in SNAPSHOT["contract"]["files"]:
        shutil.copyfile(contract_source / name, destination / name)
    manifest = {
        "schema_version": "1.0.0",
        "release_id": "dep-b-synthetic",
        "products": {
            p: {"source": {"repo": value["repo"], "commit": value["commit"]}}
            for p, value in SNAPSHOT["products"].items()
        },
        "contracts": [
            {
                "id": "diagnostics/v1",
                "path": "contracts/diagnostics/v1",
                "manifest_sha256": SNAPSHOT["contract"]["manifest_sha256"],
                "files": [
                    {"path": name, "sha256": digest}
                    for name, digest in SNAPSHOT["contract"]["files"].items()
                ],
            }
        ],
        "volumes": [
            {
                "product": p,
                "category": "logs",
                "mount": True,
                "kind": "directory",
                "host_path": "logs/" + p,
            }
            for p in SNAPSHOT["products"]
        ],
    }
    settings = {
        "tls": {name: "tls/" + name for name in TLS_FILES},
        "secrets": {name: "secrets/" + name for name in TOKEN_FILES},
        "grafana_hostname": "localhost",
        "log_budgets": {p: 1024**3 for p in SNAPSHOT["products"]},
        "ports": {"grafana": 18490, "query": 18491},
    }
    return manifest, settings
