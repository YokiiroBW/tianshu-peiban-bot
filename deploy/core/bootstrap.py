"""Create one new explicit installation directory; never overwrite another deployment."""

import argparse
import base64
from datetime import UTC, datetime, timedelta
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import quote
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tianshu.runtime.access import canonical_origin


HERE = Path(__file__).resolve().parent


def write(path, raw, *, mode=0o600, gid=None):
    if path.exists() or path.is_symlink():
        raise ValueError("bootstrap_would_overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)
    if gid is not None and os.name == "posix":
        os.chown(path, 0, gid)


def certificate(root, hostname):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    try:
        alternative = x509.IPAddress(ipaddress.ip_address(hostname))
    except ValueError:
        alternative = x509.DNSName(hostname)
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([alternative]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    write(
        root / "config/tls/server.key",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        mode=0o640,
        gid=10001,
    )
    write(
        root / "config/tls/server.crt",
        cert.public_bytes(serialization.Encoding.PEM),
        mode=0o644,
        gid=10001,
    )


def bootstrap(
    root,
    *,
    origin,
    mode,
    image,
    source_sha,
    database_host,
    database_password_file,
    database_port=25018,
):
    raw = Path(root)
    if (
        not raw.is_absolute()
        or raw.exists()
        or raw.is_symlink()
        or raw.resolve() == Path(raw.anchor)
    ):
        raise ValueError("new_absolute_installation_directory_required")
    if os.name != "posix" or os.geteuid() != 0:
        raise ValueError("linux_installation_owner_required")
    parts = canonical_origin(origin)
    if mode not in {"http", "https", "proxy"} or mode != "proxy" and mode != parts.scheme:
        raise ValueError("access_mode_origin_mismatch")
    if not re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", image) or not re.fullmatch(
        r"[0-9a-f]{40}", source_sha
    ):
        raise ValueError("immutable_image_and_source_required")
    if (
        not 1 <= database_port <= 65535
        or not database_host
        or any(character.isspace() for character in database_host)
    ):
        raise ValueError("invalid_database_address")
    password_path = Path(database_password_file)
    password = password_path.read_text().strip()
    if not password or len(password) > 4096 or "\n" in password:
        raise ValueError("database_password_file_invalid")
    raw.mkdir(mode=0o700)
    root = raw.resolve()
    write(root / "BOOTSTRAP_INCOMPLETE", b"Installation preparation has not completed.\n")
    for directory in ("config", "data", "artifacts", "guard"):
        (root / directory).mkdir(mode=0o750)
    for directory in (
        "files",
        "logs",
        "state",
        "channels",
        "plugins",
        "capabilities",
        "observability",
    ):
        path = root / "data" / directory
        path.mkdir(mode=0o750)
        os.chown(path, 10001, 10001)
    os.chown(root / "data", 10001, 10001)
    os.chown(root / "config", 0, 10001)
    for source in ("compose.yaml", "seccomp-chromium.json", "capacity.py", "health.py"):
        write(root / source, (HERE / source).read_bytes(), mode=0o640)
    for source in (HERE / "observability").rglob("*"):
        if source.is_file():
            write(
                root / "config/observability" / source.relative_to(HERE / "observability"),
                source.read_bytes(),
                mode=0o640,
                gid=10001,
            )
    config = json.loads((HERE / "settings.example.json").read_text())
    config["http"]["access"]["initial"] = {
        "mode": mode,
        "origin": origin,
        "certificate": "local" if mode == "https" else None,
    }
    config["http"]["tls"]["enabled"] = mode == "https"
    config["identity"]["cookie_secure"] = parts.scheme == "https"
    certificate(root, parts.hostname)
    write(
        root / "config/settings.json",
        (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode(),
        mode=0o640,
        gid=10001,
    )
    # Docker Compose reads this owner-only env file. Values never appear on stdout.
    host = (
        "[" + database_host + "]"
        if ":" in database_host and not database_host.startswith("[")
        else database_host
    )
    values = {
        "TIANSHU_IMAGE": image,
        "TIANSHU_RELEASE_ID": source_sha,
        "TIANSHU_DATABASE_URL": f"postgresql://tianshu_core:{quote(password, safe='')}@{host}:{database_port}/tianshu_core",
        "TIANSHU_MASTER_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        **{
            name: secrets.token_urlsafe(48)
            for name in (
                "TIANSHU_BROKER_TOKEN",
                "TIANSHU_CHANNEL_OWNER_TOKEN",
                "TIANSHU_SETUP_TOKEN",
                "TIANSHU_GRAFANA_ADMIN_PASSWORD",
            )
        },
    }
    write(
        root / ".env",
        "".join(key + "=" + json.dumps(value) + "\n" for key, value in values.items()).encode(),
    )
    manifest = {
        "schema_version": "tianshu.installation.v1",
        "installation_id": str(uuid4()),
        "deployment_root": str(root),
        "source_sha": source_sha,
        "image": image,
        "database": {
            "host": database_host,
            "port": database_port,
            "name": "tianshu_core",
            "user": "tianshu_core",
        },
        "external_binds": [],
        "created_at": datetime.now(UTC).isoformat(),
        "credential_anchor": ".env:TIANSHU_MASTER_KEY",
    }
    write(root / "installation.json", (json.dumps(manifest, indent=2) + "\n").encode())
    (root / "BOOTSTRAP_INCOMPLETE").unlink()
    return {
        "deployment_root": str(root),
        "source_sha": source_sha,
        "setup_token_file": str(root / ".env"),
        "origin": origin,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--mode", choices=("http", "https", "proxy"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--database-host", required=True)
    parser.add_argument("--database-port", type=int, default=25018)
    parser.add_argument("--database-password-file", required=True)
    print(json.dumps(bootstrap(**vars(parser.parse_args())), ensure_ascii=False))


if __name__ == "__main__":
    main()
