"""A frozen model profile identifies the quota used by its physical attempt."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.models.broker import ModelBroker
from tianshu.models.catalog import embedding_fingerprint
from tianshu.models.ledger import receipt
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest_asyncio.fixture
async def rb_database(database):
    async with database.transaction() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS runtime.installation_generation "
            "(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),id text NOT NULL,"
            "created_at timestamptz NOT NULL DEFAULT now())")
        await conn.execute("INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,%s) "
                           "ON CONFLICT(singleton) DO NOTHING", (str(uuid4()),))
        installed = await (await conn.execute("SELECT to_regclass('models.submissions') AS name")).fetchone()
        if installed["name"] is None:
            await conn.execute(files("tianshu.models").joinpath("async_execution.sql").read_text())
        column = await (await conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='models' "
            "AND table_name='attempts' AND column_name='data_generation'")).fetchone()
        if column is None:
            await conn.execute(files("tianshu.models").joinpath("generation_fence.sql").read_text())
    return database


def request():
    context = TrustedContext("fixture", "actor", None, "conversation", (), (), 1, OriginKind.ACTOR)
    return ModelRequest("intent", "attempt", "reflect", ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC) + timedelta(minutes=1)),
        (ModelMessage("user", (ModelPart("text", text="fixture"),)),),
        protocol="openai.chat", max_output_tokens=512, execution_timeout_seconds=30)


async def seed_routes(database):
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) "
                           "VALUES('primary-quota',2,0,2),('backup-quota',2,0,2),('embedding-quota',2,0,2)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('p1','primary','https://primary.invalid','KEY1','openai.chat'),"
            "('p2','backup','https://backup.invalid','KEY2','openai.chat'),"
            "('pe','embedding','https://embedding.invalid','KEY3','openai.embedding')")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities,verification) "
            "VALUES('r1','p1','main','primary-quota',ARRAY['text'],NULL),"
            "('r2','p2','backup','backup-quota',ARRAY['text'], '{\"context_window_tokens\":16000}'::jsonb),"
            "('re','pe','embedding','embedding-quota',ARRAY['embed'],NULL)")
        await conn.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id,fallback_routes) "
            "VALUES('reflect','elastic','actor','r1',ARRAY['r2'])")
        await conn.execute("INSERT INTO models.quota_cooldowns(quota_group,blocked_until,reason) "
            "VALUES('primary-quota',now()+interval '1 minute','retry_after')")
        embedding = await (await conn.execute("SELECT r.*,p.protocol,p.base_url FROM models.routes r "
            "JOIN models.providers p USING(provider_id) WHERE r.route_id='re'")).fetchone()
        await conn.execute("INSERT INTO models.embedding_profile(route_id,model_version,normalization,query_instruction,route_fingerprint) "
            "VALUES('re','v1','l2','',%s)", (embedding_fingerprint(embedding),))


async def test_profile_and_receipt_use_actual_route_quota(rb_database):
    await seed_routes(rb_database)
    broker = ModelBroker(rb_database, None, None)
    original = request()
    selected = await broker.generation_profile(original)
    assert selected["route_id"] == "r2" and selected["quota_group"] == "backup-quota"
    privileged = replace(original, context=replace(original.context, permissions=frozenset({"models.route"})),
                         route_id="r1", allow_configured_fallback=True)
    explicit_unpinned = await broker.generation_profile(privileged)
    assert (explicit_unpinned["route_id"], explicit_unpinned["quota_group"]) == ("r2", "backup-quota")
    plan = await broker.catalog.resolve(original)
    row, created = await broker.ledger.create(original, plan, {"tokens": 1}, frozen=original)
    assert created and receipt(row).route_id == selected["route_id"]
    assert receipt(row).quota_group == selected["quota_group"]

    embedding = await broker.embedding_profile()
    assert embedding["route_id"] == "re" and embedding["route_revision"] == 1
    assert embedding["quota_group"] == "embedding-quota"
    frozen_embedding = replace(original, function="embed", route_id=embedding["route_id"],
        route_revision=embedding["route_revision"], protocol="openai.embedding",
        required_capabilities=frozenset({"embed"}), max_output_tokens=0,
        messages=(), native_payload={"input": ["fixture"]})
    embedding_plan = await broker.catalog.resolve(frozen_embedding)
    assert (embedding_plan.route_id, embedding_plan.quota_group) == ("re", "embedding-quota")
    async with rb_database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET revision=2 WHERE route_id='re'")
    with pytest.raises(AppError, match="model_route_revision_conflict"):
        await broker.catalog.resolve(frozen_embedding)


async def test_fixed_route_and_revision_do_not_change_quota_when_primary_is_cooling(rb_database):
    await seed_routes(rb_database)
    broker = ModelBroker(rb_database, None, None)
    frozen = replace(request(), route_id="r1", route_revision=1, allow_configured_fallback=True)
    profile = await broker.generation_profile(frozen)
    assert (profile["route_id"], profile["route_revision"], profile["quota_group"]) == (
        "r1", 1, "primary-quota")
    assert (await broker.catalog.resolve(frozen)).route_id == "r1"

    async with rb_database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET revision=2 WHERE route_id='r1'")
    with pytest.raises(AppError, match="model_route_revision_conflict"):
        await broker.generation_profile(frozen)
    async with rb_database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET revision=1 WHERE route_id='r1'")
        await conn.execute("UPDATE models.bindings SET route_id='r2',fallback_routes='{}' "
                           "WHERE function='reflect' AND service_class='elastic' AND actor_id='actor'")
    with pytest.raises(AppError, match="model_route_binding_changed"):
        await broker.generation_profile(frozen)
