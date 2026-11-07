"""Automatic admission checks primary and backups under the same requirements."""

from dataclasses import replace

import pytest
import pytest_asyncio
import test_rb_profile_quota as profile_fixtures

from tianshu.models.catalog import ModelCatalog
from tianshu.models.ledger import submission_digest
from tianshu.runtime.errors import AppError

rb_database = profile_fixtures.rb_database
request = profile_fixtures.request


@pytest_asyncio.fixture
async def routing_database(rb_database):
    await profile_fixtures.seed_routes(rb_database)
    async with rb_database.transaction() as conn:
        await conn.execute("DELETE FROM models.quota_cooldowns")
        await conn.execute("UPDATE models.routes SET verification=NULL")
        await conn.execute("UPDATE models.routes SET verified_capabilities=ARRAY['text','tools'] WHERE route_id='r2'")
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) "
            "VALUES('third-quota',2,0,2)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('p3','third','https://third.invalid','KEY3','openai.chat')")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities) "
            "VALUES('r3','p3','third','third-quota',ARRAY['text','tools'])")
        await conn.execute("UPDATE models.bindings SET fallback_routes=ARRAY['r2','r3']")
    return rb_database


@pytest.mark.asyncio
async def test_missing_primary_tools_uses_first_backup_with_unknown_capacity_and_preserves_request(routing_database):
    original = replace(request(), required_capabilities=frozenset({"text", "tools"}))
    before = submission_digest(original)
    plan = await ModelCatalog(routing_database).resolve(original)
    assert (plan.route_id, plan.primary_route_id, plan.selection_reason) == (
        "r2", "r1", "primary_capability_unverified")
    assert submission_digest(original) == before
    async with routing_database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT verification FROM models.routes WHERE route_id='r2'")).fetchone()
        assert row["verification"] is None
        assert (await (await conn.execute("SELECT count(*) AS n FROM models.attempts")).fetchone())["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "UPDATE models.routes SET verified_capabilities=ARRAY['text'] WHERE route_id='r2'",
    "UPDATE models.routes SET verification='{\"context_window_tokens\":1}' WHERE route_id='r2'",
    "UPDATE models.routes SET enabled=false WHERE route_id='r2'",
    "UPDATE models.providers SET enabled=false WHERE provider_id='p2'",
    "INSERT INTO models.quota_cooldowns(quota_group,blocked_until,reason) VALUES('backup-quota',now()+interval '1 minute','retry_after')",
])
async def test_incompatible_backup_is_skipped_in_configuration_order(routing_database, change):
    async with routing_database.transaction() as conn:
        await conn.execute(change)
    original = replace(request(), required_capabilities=frozenset({"tools"}))
    assert (await ModelCatalog(routing_database).resolve(original)).route_id == "r3"


@pytest.mark.asyncio
async def test_primary_protocol_incompatibility_can_use_same_protocol_backup(routing_database):
    async with routing_database.transaction() as conn:
        await conn.execute("UPDATE models.providers SET protocol='gemini.generate' WHERE provider_id='p1'")
    original = replace(request(), parameters={"reasoning_effort": "high"})
    selected = await ModelCatalog(routing_database).resolve(original)
    assert (selected.route_id, selected.protocol, selected.primary_protocol) == ("r2", "openai.chat", "openai.chat")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "UPDATE models.routes SET verification='{\"context_window_tokens\":1}' WHERE route_id='r1'",
    "UPDATE models.routes SET enabled=false WHERE route_id='r1'",
    "UPDATE models.providers SET enabled=false WHERE provider_id='p1'",
    "UPDATE models.routes SET parameters='{\"max_tokens\":0}' WHERE route_id='r1'",
])
async def test_unfit_primary_uses_configured_backup(routing_database, change):
    async with routing_database.transaction() as conn:
        await conn.execute(change)
    assert (await ModelCatalog(routing_database).resolve(request())).route_id == "r2"


@pytest.mark.asyncio
async def test_unknown_primary_capacity_does_not_force_fallback(routing_database):
    assert (await ModelCatalog(routing_database).resolve(request())).route_id == "r1"


@pytest.mark.asyncio
async def test_no_compatible_route_reports_ordered_rejections_without_creating_attempt(routing_database):
    async with routing_database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET verification='{\"context_window_tokens\":1}' WHERE route_id='r2'")
        await conn.execute("UPDATE models.routes SET enabled=false WHERE route_id='r3'")
    with pytest.raises(AppError, match="model_no_compatible_route") as caught:
        await ModelCatalog(routing_database).resolve(replace(request(), required_capabilities=frozenset({"tools"})))
    assert [(item["route_id"], item["reason"]) for item in caught.value.details["rejections"]] == [
        ("r1", "model_capability_unverified"), ("r2", "model_context_window_exceeded"), ("r3", "model_route_unavailable")]
    async with routing_database.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT count(*) AS n FROM models.attempts")).fetchone())["n"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pinned", "native", "frozen"])
async def test_exact_or_already_recorded_route_never_switches_after_capability_change(routing_database, mode):
    original = replace(request(), required_capabilities=frozenset({"tools"}))
    saved = None
    if mode == "pinned":
        original = replace(original, route_id="r1", route_revision=1)
    elif mode == "native":
        original = replace(original, native_payload={"messages": []})
    else:
        saved = {"route_id": "r1", "provider_id": "p1", "model": "main", "protocol": "openai.chat",
                 "quota_group": "primary-quota", "route_revision": 1, "provider_revision": 1}
    with pytest.raises(AppError, match="model_capability_unverified"):
        await ModelCatalog(routing_database).resolve(original, frozen_attempt=saved)
