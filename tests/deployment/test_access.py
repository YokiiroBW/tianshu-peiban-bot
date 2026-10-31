"""Saved transport settings actually affect the next process, with real CAS and authority."""

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from tianshu.identity.module import create_module
from tianshu.identity.service import IdentityService
from tianshu.identity.sessions import SessionService
from tianshu.runtime.access import AccessService, canonical_origin, certificate_context
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Settings, Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import migrate


async def test_access_save_is_pending_until_restart_and_replay_rechecks_authority(
    database, tmp_path
):
    settings = Settings(
        {
            "identity": {"setup_token_ref": None, "installation_seal": str(tmp_path / "installed")},
            "http": {
                "port": 18446,
                "tls": {"port": 18443},
                "access": {
                    "initial": {
                        "mode": "http",
                        "origin": "http://example.invalid:18446",
                        "certificate": None,
                    },
                    "certificates": {},
                },
            },
        },
        Secrets({}),
        tmp_path,
    )
    commands = CommandStore(database)
    identity = IdentityService(database, commands, sessions=SessionService(database, settings))
    await migrate(database, create_module(identity).migrations)
    result, _ = await identity.sessions.setup(
        {"username": "operator", "password": "isolated-password", "client_request_id": "setup"}
    )
    context = await identity.context(result.data.principal.principal_id)
    access = AccessService(database, commands, settings)
    active = await access.load()
    assert active["identity"]["cookie_secure"] is False
    body = {
        "client_request_id": "access",
        "expected_revision": 0,
        "value": {"mode": "proxy", "origin": "https://public.example.invalid", "certificate": None},
    }
    saved = await access.save(context, body, identity=identity)
    assert (
        saved.data["restart_required"]
        and saved.data["active"]["mode"] == "http"
        and saved.data["saved"]["mode"] == "proxy"
    )
    restarted = await AccessService(database, commands, settings).load()
    assert (
        restarted["identity"]["cookie_secure"] is True
        and restarted["http"]["tls"]["enabled"] is False
    )
    assert restarted["http"]["public_origin"] == body["value"]["origin"]
    with pytest.raises(AppError, match="revision_conflict"):
        await access.save(context, {**body, "client_request_id": "stale"}, identity=identity)
    async with database.transaction() as conn:
        await conn.execute(
            "UPDATE identity.principals SET permissions='{}',access_epoch=access_epoch+1 WHERE principal_id=%s",
            (context.principal_id,),
        )
    with pytest.raises(AppError, match="access_changed"):
        await access.save(context, body, identity=identity)


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.invalid/path",
        "https://user:secret@example.invalid",
        "https://example.invalid?x=1",
        "https://example.invalid:99999",
        "https://EXAMPLE.invalid",
    ],
)
def test_access_origin_does_not_accept_credentials_or_arbitrary_paths(origin):
    with pytest.raises(AppError, match="invalid_access_origin"):
        canonical_origin(origin)


def test_certificate_selection_checks_actual_key_and_domain_without_network(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tianshu.example.invalid")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("tianshu.example.invalid")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    tls = {"certificate_file": str(cert_file), "key_file": str(key_file)}
    assert certificate_context(tls, "tianshu.example.invalid")
    with pytest.raises(AppError, match="access_certificate_invalid"):
        certificate_context(tls, "another.example.invalid")
