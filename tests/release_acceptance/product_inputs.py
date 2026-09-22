"""Explicit local paths and synthetic credentials over the shipped release templates."""

import ipaddress
import json
import os
import secrets
import socket
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

ROLES = ("platform", "companion", "memory", "gateway")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o600)


def tls(root):
    root.mkdir()
    key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-E SYNTHETIC ONLY")]
    )
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for role in (
        *ROLES,
        "model",
        "vector",
        "loki",
        "grafana",
        "prometheus",
        "guard",
        "client",
    ):
        leafkey = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name(
                    [x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC " + role)]
                )
            )
            .issuer_name(issuer)
            .public_key(leafkey.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.DNSName(role + ".internal"),
                        x509.DNSName("obs-" + role),
                        x509.DNSName("logs.internal"),
                    ]
                ),
                False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.CLIENT_AUTH
                        if role == "client"
                        else ExtendedKeyUsageOID.SERVER_AUTH
                    ]
                ),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        (root / (role + ".pem")).write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (root / (role + ".key")).write_bytes(
            leafkey.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    (root / "client-ca.pem").write_bytes((root / "ca.pem").read_bytes())


def inputs(root, snapshots, manifest, templates):
    from configuration import references, resolve_config

    tls(root / "tls")
    # Reserve unique ports together, release just before owned children bind; bind failures fail closed.
    sockets = [socket.socket() for _ in range(5)]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    ports = dict(zip((*ROLES, "model"), [s.getsockname()[1] for s in sockets]))
    for sock in sockets:
        sock.close()
    urls = {r: "https://127.0.0.1:" + str(p) for r, p in ports.items()}
    configs = {r: json.loads((templates / (r + ".json")).read_bytes()) for r in ROLES}
    credentials = {
        name: secrets.token_urlsafe(32)
        for c in configs.values()
        for name in references(c)
    }
    credentials["TS_ADMIN_PASSWORD"] = secrets.token_urlsafe(24)
    credentials["TS_SYNTHETIC_MODEL"] = secrets.token_urlsafe(32)
    environments = {}
    for role, config in configs.items():
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "COMSPEC",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            }
        }
        env.update({name: credentials[name] for name in references(config)})
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        if role == "gateway":
            env["TS_SYNTHETIC_MODEL"] = credentials["TS_SYNTHETIC_MODEL"]
        env["TIANSHU_DIAGNOSTICS_TOKEN"] = secrets.token_urlsafe(32)
        env["SSL_CERT_FILE"] = str(root / "tls/ca.pem")
        env["NO_PROXY"] = "*"
        env["PYTHONPATH"] = str(
            snapshots / role / ("" if role == "platform" else "src")
        )
        db = (
            root
            / "data"
            / role
            / ("companion.db" if role == "companion" else role + ".sqlite")
        )
        db.parent.mkdir(parents=True)
        logs = root / "logs" / role
        logs.mkdir(parents=True)
        env["TIANSHU_LOG_DIR"] = str(logs)
        replacements = {
            "/contracts/text-dialogue/v1": str(
                snapshots / "contracts/text-dialogue/v1"
            ),
            "/contracts/diagnostics/v1": str(snapshots / "contracts/diagnostics/v1"),
            "/etc/tianshu/tls/ca.pem": str(root / "tls/ca.pem"),
        }

        def replace(value):
            if isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace(v) for v in value]
            if isinstance(value, str):
                if value in replacements:
                    return replacements[value]
                for peer in ROLES:
                    prefix = (
                        "https://"
                        + peer
                        + ".internal:"
                        + str(
                            {
                                "platform": 8443,
                                "companion": 8765,
                                "memory": 8130,
                                "gateway": 8443,
                            }[peer]
                        )
                    )
                    if value.startswith(prefix):
                        return urls[peer] + value[len(prefix) :]
            return value

        config = replace(config)
        config = resolve_config(config, env)
        if role != "gateway":
            config["database_path"] = str(db)
        if role == "platform":
            config["diagnostics"]["log_directory"] = str(logs)
            config["web"].update(
                username="synthetic-admin",
                origin=urls[role],
                static_directory=str(root / "web"),
            )
            config["web"]["dialogue_enabled"] = next(
                f["enabled"]
                for f in manifest["features"]
                if f["id"] == "web_text_dialogue"
            )
            config["tls"] = {
                "certificate_file": str(root / "tls/platform.pem"),
                "private_key_file": str(root / "tls/platform.key"),
            }
        elif role == "memory":
            config["log_directory"] = str(logs)
            config["source_sync"]["recovery_path"] = str(db) + ".source-guard.json"
            env["TIANSHU_MEMORY_CONFIG"] = str(root / "config/memory.json")
        elif role == "companion":
            # Startup-only selection is read by TS-108; old baselines remain gated by the manifest.
            config["automatic_memory_candidates"] = False
            config["policy"]["silence_ms"] = 0
            # This load exercise targets the global disabled outbox, without context replay.
            config["short_context"]["max_turns"] = 0
        else:
            config["diagnostics_path"] = str(db)
            config["observability"]["log_directory"] = str(logs)
            config["targets"][0]["addresses"] = ["127.0.0.1"]
            config["targets"].append(
                {
                    "base_url": urls["model"] + "/v1",
                    "addresses": ["127.0.0.1"],
                    "allow_private_http": False,
                }
            )
            config["secret_references"]["secret-ref:synthetic"] = "TS_SYNTHETIC_MODEL"
            config["clients"][0]["provider_id"] = "provider-synthetic"
        configs[role], environments[role] = config, env
    model = {
        "provider_id": "provider-synthetic",
        "base_url": urls["model"] + "/v1",
        "credential_ref": "secret-ref:synthetic",
        "credential_namespace": "synthetic-local",
        "protocol": "openai-chat-completions",
        "model_id": "synthetic-recorded-text",
        "capability_verification": "fixture_only",
        "verified_capabilities": ["text", "stream"],
        "model_policy": {"mode": "preserve_client", "fields": {}},
        "reasoning_policy": {"mode": "preserve_client", "fields": {}},
    }
    configs["platform"]["providers"] = {
        "provider-synthetic": {
            **{
                k: model[k]
                for k in (
                    "base_url",
                    "credential_ref",
                    "credential_namespace",
                    "protocol",
                    "capability_verification",
                    "verified_capabilities",
                )
            },
            "model_ids": [model["model_id"]],
            "reviewed_addresses": ["127.0.0.1"],
        }
    }
    now = datetime.now(timezone.utc)

    def stamp(t):
        return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    publication = {
        "schema_version": 1,
        "request_id": "dep-e-bootstrap",
        "config_version": 1,
        "status": "published",
        "published_at": stamp(now),
        "usable_until": stamp(now + timedelta(minutes=15)),
        "providers": [model],
        "bindings": [
            {
                "workload": "companion.text",
                "provider_id": "provider-synthetic",
                "model_id": model["model_id"],
                "timeout_ms": 30000,
                "fallback": "disabled",
            }
        ],
    }
    for role, config in configs.items():
        write(root / "config" / (role + ".json"), config)
    (root / "web/assets").mkdir(parents=True)
    (root / "web/index.html").write_text(
        '<!doctype html><script src="/assets/synthetic.js"></script>', encoding="utf-8"
    )
    (root / "web/assets/synthetic.js").write_text(
        "/* Synthetic static fixture; no browser claim. */", encoding="utf-8"
    )
    return configs, environments, urls, ports, publication
