"""Prepare a NEW synthetic scope and private bundle; default plan, never starts Docker."""

import argparse
import ipaddress
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from bundle import initialize
from configuration import references
from manifest import (
    HERE,
    PRODUCTS,
    fresh_target,
    load_manifest,
    read_json,
    require,
    write_json,
)


def tls(directory):
    directory.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-G ISOLATED SYNTHETIC CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for role in (
        *PRODUCTS,
        "logs",
        "obs-guard",
        "obs-loki",
        "obs-vector",
        "obs-prometheus",
        "obs-client",
    ):
        path = directory / role
        path.mkdir(mode=0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        names = [
            x509.DNSName(role if role.startswith("obs-") else role + ".internal"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
        if role == "platform":
            names.append(x509.DNSName("console.synthetic.test"))
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name(
                    [x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC " + role)]
                )
            )
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .sign(ca_key, hashes.SHA256())
        )
        (path / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private = path / "server.key"
        private.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        private.chmod(0o600)
    # CA signing key is never persisted and cannot sign another real service.


def create(scope, manifest_path, contracts, project, subnet, web_port):
    import re

    require(
        re.fullmatch(r"tianshu-qa-[a-z0-9-]+", project), "isolated_qa_project_required"
    )
    net = ipaddress.ip_network(subnet, strict=True)
    require(
        net.version == 4 and net.is_private and 24 <= net.prefixlen <= 28,
        "synthetic_subnet_invalid",
    )
    require(1024 <= web_port <= 65535, "synthetic_web_port_invalid")
    load_manifest(manifest_path)
    scope = fresh_target(scope)
    scope.mkdir(mode=0o700)
    inputs = scope / "inputs"
    shutil.copytree(HERE / "templates", inputs)
    tls(inputs / "tls")
    sitepath = inputs / "deployment-input.example.json"
    site = read_json(sitepath)
    site.update(
        project_name=project,
        subnet=str(net),
        web_port=web_port,
        bind_address="127.0.0.1",
        web_origin="https://console.synthetic.test:" + str(web_port),
        service_ips={
            p: str(net.network_address + 10 + i) for i, p in enumerate(PRODUCTS)
        },
    )
    values = {}
    for p in PRODUCTS:
        site["tls"][p]["provenance"] = "isolated_test"
        config_path = inputs / (p + ".json")
        config = read_json(config_path)
        if p == "platform":
            config["web"].update(origin=site["web_origin"], username="synthetic-dep-g")
        if p == "gateway":
            config["targets"][0]["addresses"] = [site["service_ips"]["platform"]]
        write_json(config_path, config)
        for name in references(config):
            actual = (
                site["diagnostics_env"][p]
                if name == "TIANSHU_DIAGNOSTICS_TOKEN"
                else name
            )
            values.setdefault(actual, secrets.token_urlsafe(32))
    write_json(sitepath, site)
    (scope / "deployments").mkdir(mode=0o700)
    initialize(manifest_path, sitepath, contracts, scope / "deployments/source", values)
    # No passwords/tokens printed or persisted outside the already protected bundle.
    return scope / "deployments/source"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--contracts-root", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--web-port", required=True, type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        create(
            args.scope.absolute(),
            args.manifest,
            args.contracts_root,
            args.project,
            args.subnet,
            args.web_port,
        )
        print("synthetic_bundle_initialized_not_started")
    else:
        fresh_target(args.scope.absolute())
        print(
            "plan: create new scope/inputs and scope/deployments/source; no services started"
        )


if __name__ == "__main__":
    main()
