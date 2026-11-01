"""Configuration deletion preserves execution evidence and explicit embedding ownership."""

from types import SimpleNamespace
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models.configuration_delete import delete_configuration
from tianshu.models.client import ModelClient
from tianshu.models.discovery import native_request, parse_catalog
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Settings, Secrets
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def test_native_catalog_pagination_never_invents_capability_evidence():
    provider = {"protocol": "gemini.generate", "base_url": "https://example.invalid/v1beta"}
    url, headers, params = native_request(provider, "synthetic", "opaque-token")
    assert url.endswith("/v1beta/models") and params["pageToken"] == "opaque-token"
    assert "Authorization" not in headers
    page = parse_catalog(
        "gemini.generate", {"models": [{"name": "models/native-model"}], "nextPageToken": "page-2"}
    )
    assert page == {
        "models": ["native-model"],
        "next_cursor": "page-2",
        "coverage": {"complete": False, "missing": ["more_pages"]},
    }
    assert "verified_capabilities" not in page
    assert (
        parse_catalog(
            "anthropic.messages",
            {"data": [{"id": "claude-real"}], "has_more": True, "last_id": "opaque"},
        )["next_cursor"]
        == "opaque"
    )
    with pytest.raises(AppError, match="model_enumeration_unsupported"):
        native_request({**provider, "protocol": "cohere.rerank"}, "synthetic")
    with pytest.raises(AppError, match="provider_model_catalog_invalid"):
        parse_catalog("openai.chat", {"data": [{"id": "unsafe\nmodel"}]})


async def test_duplicate_external_admission_cannot_cancel_original_owner():
    client = object.__new__(ModelClient)
    cancellations = []

    async def duplicate(*args, **kwargs):
        raise AppError("attempt_already_exists", status=409)

    async def cancel(attempt):
        cancellations.append(attempt)

    client._json, client._cancel_cleanup = duplicate, cancel
    request = SimpleNamespace(
        attempt_id="original", budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=10)),
        service_class=ServiceClass.INTERACTIVE,
    )
    with pytest.raises(AppError, match="attempt_already_exists"):
        async with client.resource(request):
            pytest.fail("A duplicate has no resource permit")
    assert cancellations == []


async def test_provider_cascade_cas_and_binding_inheritance(database, tmp_path):
    services = SimpleNamespace(
        database=database,
        settings=Settings({}, Secrets({}), tmp_path),
        commands=CommandStore(database),
    )
    context = TrustedContext(
        "operator", None, None, None, (), (), 1, OriginKind.HUMAN, frozenset({"models.manage"})
    )
    async with database.transaction() as conn:
        await conn.execute(
            "INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) VALUES('p','P','https://example.invalid/v1','sealed:provider:p','openai.embedding')"
        )
        await conn.execute(
            "INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES('q',2,1,1)"
        )
        await conn.execute(
            "INSERT INTO models.routes(route_id,provider_id,model,quota_group) VALUES('r','p','native-embedding','q')"
        )
        await conn.execute(
            "INSERT INTO models.bindings(function,service_class,actor_id,route_id) VALUES('*','interactive','','r'),('chat','interactive','role','r')"
        )
        await conn.execute(
            "INSERT INTO models.embedding_profile(route_id,model_version,normalization,query_instruction,route_fingerprint) VALUES('r','fixed-version','l2','','fixed-fingerprint')"
        )
        await conn.execute(
            "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,function,service_class,route_id,route_revision,provider_id,model,protocol,quota_group,request_digest,context_fingerprint,status,dispatch,deadline_at) VALUES('executed','intent','operator','embed','interactive','r',1,'p','native-embedding','openai.embedding','q','digest','context','succeeded','submitted',now())"
        )
    role = replace(context, actor_id="role")
    removed = await delete_configuration(
        services,
        role,
        "bindings",
        {
            "client_request_id": "inherit",
            "expected_revision": 1,
            "actor_id": "role",
            "function": "chat",
            "service_class": "interactive",
        },
    )
    assert len(removed.data["catalog"]["bindings"]) == 1
    with pytest.raises(AppError, match="embedding_profile_in_use"):
        await delete_configuration(
            services,
            context,
            "providers",
            {"provider_id": "p", "client_request_id": "blocked", "expected_revision": 1},
        )
    await delete_configuration(
        services,
        context,
        "embedding-profile",
        {"client_request_id": "clear-index-profile", "expected_revision": 1},
    )
    deleted = await delete_configuration(
        services,
        context,
        "providers",
        {"provider_id": "p", "client_request_id": "delete", "expected_revision": 1},
    )
    assert (
        deleted.data["catalog"]["providers"] == []
        and deleted.data["catalog"]["routes"] == []
        and deleted.data["catalog"]["bindings"] == []
    )
    async with database.transaction(read_only=True) as conn:
        evidence = await (
            await conn.execute(
                "SELECT status,dispatch,provider_id,model FROM models.attempts WHERE attempt_id='executed'"
            )
        ).fetchone()
    assert evidence == {
        "status": "succeeded",
        "dispatch": "submitted",
        "provider_id": "p",
        "model": "native-embedding",
    }
    replay = await delete_configuration(
        services,
        context,
        "providers",
        {"provider_id": "p", "client_request_id": "delete", "expected_revision": 1},
    )
    assert replay.client_request_id == deleted.client_request_id
    with pytest.raises(AppError, match="revision_conflict"):
        await delete_configuration(
            services,
            context,
            "providers",
            {"provider_id": "p", "client_request_id": "stale-delete", "expected_revision": 1},
        )
