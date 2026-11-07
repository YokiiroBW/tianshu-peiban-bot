"""A configuration blocker stops empty wakes and resumes the original request."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from tianshu.conversation.codec import document
from tianshu.conversation.model_admission import BLOCKED_ROUTE
from tianshu.conversation.module import create_module
from tianshu.conversation.service import ConversationService
from tianshu.models.catalog import ModelCatalog
from tianshu.models.route_requirements import no_compatible_route
from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


@pytest_asyncio.fixture
async def admissions(database):
    await migrate(database, (
        Migration("0032_conversation", files("tianshu.conversation").joinpath("schema.sql").read_text()),
        Migration("0041_conversation_submission_confirmation", files("tianshu.conversation").joinpath("submission_confirmation.sql").read_text()),
    ))
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES('quota',2,0,2)")
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
            "VALUES('provider','fixture','https://fixture.invalid','FIXTURE','openai.chat')")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group,verified_capabilities) "
            "VALUES('primary','provider','primary','quota',ARRAY['text']),('backup','provider','backup','quota',ARRAY['text'])")
        await conn.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id,fallback_routes) "
            "VALUES('chat','interactive','actor','primary',ARRAY['backup'])")
    authority = SimpleNamespace(validate_persistence=AsyncMock(return_value=True), check=AsyncMock())
    models = SimpleNamespace(status=AsyncMock(return_value=None), submit=AsyncMock(
        side_effect=no_compatible_route([{"route_id": "primary", "reason": "model_capability_unverified"}])))

    async def result(attempt_id, **_):
        async with database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT * FROM models.attempts WHERE attempt_id=%s", (attempt_id,))).fetchone()
        return SimpleNamespace(receipt=row) if row else None

    models.result = AsyncMock(side_effect=result)
    models.generation_profile = AsyncMock(side_effect=ModelCatalog(database).resolve)
    jobs = SimpleNamespace(enqueue=AsyncMock(), assert_generation_on=AsyncMock())
    service = ConversationService(database, None, jobs, authority, authority, None, None,
                                  None, None, None, models, None, None, None, None)
    context = TrustedContext("p", "actor", "person", "chat", (), (), 1, OriginKind.HUMAN,
                             conversation_kind="private", delivery_allowed=True)

    async def seed(name="turn", *, blocked=True):
        now = datetime.now(UTC)
        original = ModelRequest(name+":0", name+"-attempt", "chat", ServiceClass.INTERACTIVE, context,
            RequestBudget(now-timedelta(hours=2)), (ModelMessage("user", (ModelPart("text", text="hello"),)),),
            required_capabilities=frozenset({"text", "tools"}), execution_timeout_seconds=900,
            max_output_tokens=512)
        async with database.transaction() as conn:
            await conn.execute("INSERT INTO conversation.turns(turn_id,actor_id,conversation_id,producer,event_id,event_revision,"
                "context,input_parts,source_refs,original_parts,original_source_refs,state,reply_owner,reply_required,"
                "model_attempt_id,occurred_at,received_at,deadline_at,error_code) VALUES(%s,'actor','chat','test',%s,1,"
                "%s,'[]','[]','[]','[]','waiting_external','tianshu',true,%s,%s,%s,%s,%s)",
                (name, name, document(context), original.attempt_id, now, now, now, BLOCKED_ROUTE if blocked else None))
            await conn.execute("INSERT INTO conversation.context_selections(attempt_id,turn_id,round,request_digest,selection,"
                "token_usage,source_refs,round_snapshot) VALUES(%s,%s,0,'digest','[]','{}','[]','{}')", (original.attempt_id, name))
        await service.generation.prepare("turn:"+name+":0", original)
        return original

    return SimpleNamespace(service=service, models=models, jobs=jobs, seed=seed, database=database)


@pytest.mark.asyncio
async def test_no_route_marks_visible_blocker_and_stops_submit_observe_cycle(admissions):
    original = await admissions.seed(blocked=False)
    await admissions.service.model_submit("turn", original.attempt_id)
    await admissions.service.model_complete("turn", original.attempt_id)
    async with admissions.database.transaction(read_only=True) as conn:
        turn = await (await conn.execute("SELECT state,error_code FROM conversation.turns WHERE turn_id='turn'")).fetchone()
        generation = await (await conn.execute("SELECT state,submission_confirmed FROM conversation.generation_requests")).fetchone()
    assert turn == {"state": "waiting_external", "error_code": BLOCKED_ROUTE}
    assert generation == {"state": "claimed", "submission_confirmed": False}
    admissions.jobs.enqueue.assert_not_awaited()
    admissions.models.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_configuration_change_resumes_only_original_frozen_admission(admissions):
    original = await admissions.seed()
    waits = admissions.service.model_admissions
    await waits.reconcile()
    admissions.jobs.enqueue.assert_not_awaited()
    async with admissions.database.transaction() as conn:
        await conn.execute("UPDATE models.routes SET verified_capabilities=ARRAY['text','tools'],revision=revision+1 WHERE route_id='backup'")
    waits.next_check = 0
    await waits.reconcile()
    spec = admissions.jobs.enqueue.await_args.args[1]
    assert spec.task == "conversation.model_submit"
    assert spec.payload == {"turn_id": "turn", "attempt_id": original.attempt_id}
    assert await admissions.service.generation.request(original.attempt_id, original.context) == original
    admissions.models.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_unknown_is_observed_instead_of_readmitting(admissions):
    original = await admissions.seed()
    async with admissions.database.transaction() as conn:
        await conn.execute("INSERT INTO models.attempts(attempt_id,intent_id,function,service_class,principal_id,actor_id,"
            "route_id,route_revision,provider_id,model,protocol,quota_group,request_digest,context_fingerprint,status,dispatch,deadline_at) "
            "VALUES(%s,%s,'chat','interactive','p','actor','primary',1,'provider','primary','openai.chat','quota','digest','context',"
            "'unknown','unknown',now())", (original.attempt_id, original.intent_id))
    await admissions.service.model_admissions.reconcile()
    spec = admissions.jobs.enqueue.await_args.args[1]
    assert spec.task == "conversation.model_complete"
    admissions.models.generation_profile.assert_not_awaited()
    admissions.models.submit.assert_not_awaited()
    async with admissions.database.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT status FROM models.attempts")).fetchone())["status"] == "unknown"


@pytest.mark.asyncio
async def test_resume_enqueue_failure_keeps_blocker_transactionally(admissions):
    original = await admissions.seed()
    admissions.models.generation_profile = AsyncMock(return_value={"route_id": "backup"})
    admissions.jobs.enqueue.side_effect = AppError("queue_unavailable")
    async with admissions.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT * FROM conversation.turns WHERE turn_id='turn'")).fetchone()
    with pytest.raises(AppError, match="queue_unavailable"):
        await admissions.service.model_admissions._resume(row)
    async with admissions.database.transaction(read_only=True) as conn:
        assert (await (await conn.execute("SELECT error_code FROM conversation.turns")).fetchone())["error_code"] == BLOCKED_ROUTE
    assert await admissions.service.generation.request(original.attempt_id, original.context) == original


@pytest.mark.asyncio
async def test_confirmed_or_unconfirmed_recovery_never_resubmits_a_missing_receipt(admissions):
    original = await admissions.seed()
    async with admissions.database.transaction() as conn:
        await conn.execute("UPDATE conversation.generation_requests SET submission_confirmed=true WHERE attempt_id=%s", (original.attempt_id,))
    await admissions.service.model_admissions.reconcile()
    admissions.models.generation_profile.assert_not_awaited()
    admissions.models.submit.assert_not_awaited()
    admissions.jobs.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_cursor_visits_later_blockers_and_throttles_repeated_passes(admissions):
    for name in ("first", "second", "third"):
        await admissions.seed(name)
    waits = admissions.service.model_admissions
    await waits.reconcile(limit=2)
    await waits.reconcile(limit=2)
    assert admissions.models.generation_profile.await_count == 2
    waits.next_check = 0
    await waits.reconcile(limit=2)
    assert [call.args[0].attempt_id for call in admissions.models.generation_profile.await_args_list] == [
        "first-attempt", "second-attempt", "third-attempt"]


@pytest.mark.asyncio
async def test_foreground_maintenance_includes_blocked_admission_recovery():
    service = SimpleNamespace(resume_model_admissions=AsyncMock())
    consumer = SimpleNamespace(reconcile_due=AsyncMock())
    module = create_module(service, None, None, consumer, None)
    await module.maintenance(None)
    assert module.maintenance_roles == ("bot-foreground",)
    service.resume_model_admissions.assert_awaited_once_with(limit=32)
    consumer.reconcile_due.assert_awaited_once_with(limit=32)
