"""Cloud resources, quota cooldowns and protocol adaptation stay distinct."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest
import pytest_asyncio

from tianshu.models.catalog import ModelCatalog, QuotaPolicy, embedding_fingerprint
from tianshu.models.http_timing import HttpTiming, retry_after
from tianshu.models.route_adaptation import adapt_request
from tianshu.models.scheduler import QuotaScheduler
from tianshu.models.transport import provider_headers
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest_asyncio.fixture
async def rb_database(database):
    async with database.transaction() as conn:
        found = await (await conn.execute("SELECT to_regclass('models.submissions') AS table_name")).fetchone()
        if found["table_name"] is None:
            await conn.execute(files("tianshu.models").joinpath("async_execution.sql").read_text())
    return database


def request(identity):
    return ModelRequest("work:" + identity, identity, "reflect", ServiceClass.ELASTIC,
        TrustedContext("fixture", "actor", None, "conversation", (), (), 1, OriginKind.ACTOR),
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)),
        (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
        execution_timeout_seconds=30)


async def test_cloud_unknown_does_not_hold_capacity_or_idle_foreground_slots():
    scheduler = QuotaScheduler({"account": QuotaPolicy("account", 2, 1, 1)})
    scheduler.restore([{"quota_group": "account", "attempt_id": "unknown", "protocol": "openai.chat",
        "service_class": "elastic", "started_at": datetime.now(UTC), "charged_units": {},
        "estimated_units": {}, "status": "unknown", "hold_until": datetime.now(UTC) + timedelta(minutes=5)}])
    await scheduler.start()
    try:
        await asyncio.wait_for(scheduler.acquire(request("a"), "account", {}), 1)
        await asyncio.wait_for(scheduler.acquire(request("b"), "account", {}), 1)
        assert scheduler.snapshot()["account"]["cloud_active"] == 2
        assert scheduler.snapshot()["account"]["unknown_holds"] == 0
    finally:
        await scheduler.close()


async def test_retry_after_delays_only_its_quota_domain():
    scheduler = QuotaScheduler({name: QuotaPolicy(name, 1, 0, 1) for name in ("limited", "other")})
    await scheduler.start()
    pending = None
    try:
        await scheduler.defer_group("limited", datetime.now(UTC) + timedelta(seconds=0.2))
        pending = asyncio.create_task(scheduler.acquire(request("wait"), "limited", {}))
        await asyncio.wait_for(scheduler.acquire(request("independent"), "other", {}), 1)
        assert not pending.done()
        await asyncio.wait_for(pending, 1)
    finally:
        if pending and not pending.done():
            pending.cancel()
        await scheduler.close()


async def test_route_outage_does_not_block_another_route_in_the_same_quota_domain():
    scheduler = QuotaScheduler({"account": QuotaPolicy("account", 1, 0, 1)})
    await scheduler.start()
    waiting = None
    try:
        await scheduler.defer_route("account", "r1", datetime.now(UTC) + timedelta(seconds=0.2))
        waiting = asyncio.create_task(scheduler.acquire(request("waiting"), "account", {}, route_id="r1"))
        await asyncio.wait_for(scheduler.acquire(request("other"), "account", {}, route_id="r2"), 1)
        assert not waiting.done()
        await asyncio.wait_for(waiting, 1)
    finally:
        if waiting and not waiting.done():
            waiting.cancel()
        await scheduler.close()


def test_fallback_translates_only_proven_equivalent_options():
    original = replace(request("json"), protocol="openai.chat", parameters={
        "max_tokens": 512, "temperature": 0.2, "response_format": {"type": "json_object"}},
        max_output_tokens=512)
    adapted = adapt_request(original, "openai.chat", "gemini.generate")
    assert adapted.messages == original.messages and adapted.system == original.system
    assert adapted.parameters["generationConfig"] == {
        "temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 512}
    with pytest.raises(AppError, match="model_fallback_protocol_incompatible"):
        adapt_request(replace(original, parameters={"reasoning_effort": "high"}),
                      "openai.chat", "gemini.generate")


def test_timing_unknowns_and_data_generation_are_explicit():
    timing = HttpTiming(None).record()
    assert timing["connection_reused"] is None
    assert timing["points_ms"]["dns_start"] is None
    assert timing["points_ms"]["first_valid_output"] is None
    assert timing["notes"]["provider_queue_and_compute"] == "unknown"
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert retry_after("12", now=now) == now + timedelta(seconds=12)
    assert retry_after("invalid", now=now) is None
    context = request("session").context
    base = "https://opencode.ai/zen/v1"
    first = provider_headers(base, context, "one", generation="generation-a")
    assert provider_headers(base, context, "two", generation="generation-a") == first
    assert provider_headers(base, context, "one", generation="generation-b") != first


async def test_fallback_uses_independent_configured_quota_and_verified_context(rb_database):
    async with rb_database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) "
                           "VALUES('primary',2,0,2),('backup',2,0,2)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('p1','primary','https://primary.invalid','KEY1','openai.chat'),"
            "('p2','backup','https://backup.invalid','KEY2','gemini.generate')")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities,verification) "
            "VALUES('r1','p1','one','primary',ARRAY['text'],NULL),"
            "('r2','p2','two','backup',ARRAY['text'], '{\"context_window_tokens\": 16000}'::jsonb)")
        await conn.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id,fallback_routes) "
            "VALUES('reflect','elastic','actor','r1',ARRAY['r2'])")
        await conn.execute("INSERT INTO models.quota_cooldowns(quota_group,blocked_until,reason) "
            "VALUES('primary',now()+interval '1 minute','retry_after')")
    original = replace(request("fallback"), protocol="openai.chat", max_output_tokens=512)
    selected = await ModelCatalog(rb_database).resolve(original)
    assert selected.route_id == "r2" and selected.primary_route_id == "r1"
    assert selected.protocol == "gemini.generate" and selected.selection_reason == "primary_quota_cooldown"
    async with rb_database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET quota_group='primary' WHERE route_id='r2'")
    assert (await ModelCatalog(rb_database).resolve(original)).route_id == "r1"


async def test_same_dimension_different_embedding_fingerprint_is_not_a_fallback(rb_database):
    async with rb_database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) "
                           "VALUES('primary',2,0,2),('backup',2,0,2)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('p1','primary','https://primary.invalid','KEY1','openai.embedding'),"
            "('p2','backup','https://backup.invalid','KEY2','openai.embedding')")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities) "
            "VALUES('r1','p1','vector-a','primary',ARRAY['embed']),"
            "('r2','p2','vector-b','backup',ARRAY['embed'])")
        row = await (await conn.execute("SELECT r.*,p.protocol,p.base_url FROM models.routes r "
            "JOIN models.providers p USING(provider_id) WHERE route_id='r1'")).fetchone()
        await conn.execute("INSERT INTO models.embedding_profile(route_id,model_version,normalization,query_instruction,route_fingerprint) "
            "VALUES('r1','v1','unit','',%s)", (embedding_fingerprint(row),))
        await conn.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id,fallback_routes) "
            "VALUES('embed','elastic','actor','r1',ARRAY['r2'])")
        await conn.execute("INSERT INTO models.quota_cooldowns(quota_group,blocked_until,reason) "
            "VALUES('primary',now()+interval '1 minute','retry_after')")
    original = replace(request("embed"), function="embed", max_output_tokens=0,
                       required_capabilities=frozenset({"embed"}))
    assert (await ModelCatalog(rb_database).resolve(original)).route_id == "r1"
