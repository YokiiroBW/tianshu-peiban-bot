"""Actual provider configuration cannot borrow deployment or another provider's keys."""

import base64
from types import SimpleNamespace

import pytest

from tianshu.models.credentials import provider_reference
from tianshu.models.management import mutate
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Secrets, Settings
from tianshu.runtime.credentials import CredentialStore
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


@pytest.mark.parametrize(
    "reference", ["TIANSHU_MASTER_KEY", "env:TIANSHU_DATABASE_URL", "sealed:provider:another"]
)
def test_provider_references_cannot_escape_their_namespace(reference):
    with pytest.raises(AppError, match="provider_credential_reference_forbidden"):
        provider_reference("current", reference, {"current": "TIANSHU_MASTER_KEY"})
    assert provider_reference("current", "sealed:provider:current") == "sealed:provider:current"
    assert (
        provider_reference(
            "current",
            "TIANSHU_PROVIDER_CURRENT_KEY",
            {"current": "env:TIANSHU_PROVIDER_CURRENT_KEY"},
        )
        == "env:TIANSHU_PROVIDER_CURRENT_KEY"
    )


async def test_provider_key_preserve_clear_and_forbidden_ref_are_transactional(database, tmp_path):
    settings = Settings(
        {}, Secrets({"MASTER": base64.urlsafe_b64encode(b"x" * 32).decode()}), tmp_path
    )
    credentials = CredentialStore(database, settings.secrets, "MASTER")
    services = SimpleNamespace(
        settings=settings, credentials=credentials, commands=CommandStore(database)
    )
    context = TrustedContext(
        "operator", None, None, None, (), (), 1, OriginKind.HUMAN, frozenset({"models.manage"})
    )
    provider = {
        "provider_id": "current",
        "name": "current",
        "base_url": "https://provider.invalid/v1",
        "protocol": "openai.chat",
        "enabled": True,
    }
    saved = await mutate(
        services,
        context,
        "providers",
        {
            **provider,
            "expected_revision": 0,
            "api_key": "synthetic-provider-key",
            "client_request_id": "initial",
        },
    )
    assert saved["data"]["providers"][0]["credential_configured"] is True
    await mutate(
        services,
        context,
        "providers",
        {**provider, "expected_revision": 1, "client_request_id": "preserve"},
    )
    assert await credentials.resolve("sealed:provider:current") == "synthetic-provider-key"
    with pytest.raises(AppError, match="provider_credential_reference_forbidden"):
        await mutate(
            services,
            context,
            "providers",
            {
                **provider,
                "expected_revision": 2,
                "credential_ref": "MASTER",
                "client_request_id": "escape",
            },
        )
    assert await credentials.resolve("sealed:provider:current") == "synthetic-provider-key"
    cleared = await mutate(
        services,
        context,
        "providers",
        {**provider, "expected_revision": 2, "clear_api_key": True, "client_request_id": "clear"},
    )
    assert cleared["data"]["providers"][0]["credential_configured"] is False
    with pytest.raises(AppError, match="credential_not_configured"):
        await credentials.resolve("sealed:provider:current")
