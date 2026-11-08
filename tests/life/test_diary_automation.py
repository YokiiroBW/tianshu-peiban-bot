"""New local-day diary work uses real lifecycle tables and isolated episode ports."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tianshu.conversation.codec import document, signature
from tianshu.life.diaries import DiaryService
from tianshu.life.generation_control import GenerationControl
from tianshu.memory.types import Episode, EpisodePage
from tianshu.models.types import ModelReceipt, ModelRequest
from tianshu.models.ledger import submission_digest
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, OriginKind, SourceRef, RequestBudget, ServiceClass
from test_planner_execution import setup
from historical_support import diary_fixture as historical_fixture, migrations


class Authority:
    epoch = 1

    async def check(self, context, *_refs):
        if context.access_epoch != self.epoch:
            raise AppError("access_changed",status=403)

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)

    async def refresh_scheduled_context(self, previous, *, permission, connection=None):
        return replace(previous,access_epoch=self.epoch,origin_kind=OriginKind.TIMER)


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, spec, **_options):
        self.items.append(spec)


class Memory:
    def __init__(self, episodes):
        self.episodes = episodes
        self.watermark = "e:1"
        self.windows = []

    async def episodes_between(self, context, start, end, *, limit, cursor):
        self.windows.append((start,end))
        rows = tuple(item for item in self.episodes if start <= item.occurred_at < end
            and item.actor_id == context.actor_id and set(item.scope_ids).issubset(context.scope_ids))
        return EpisodePage(rows,None,Coverage(True),self.watermark)

    async def episodes_by_refs(self, context, refs, *, connection=None):
        assert connection is not None  # The final source/Episode/diary CAS is atomic.
        found = tuple(item for item in self.episodes if (item.episode_id,item.revision) in refs
            and item.actor_id == context.actor_id and set(item.scope_ids).issubset(context.scope_ids))
        return Page(found,None,Coverage(len(found) == len(refs)))


async def fixture(database, *, empty=False, unknown=False):
    value = await setup(database,"今天只记下实际完成的小事。",lose_response=unknown)
    await migrations(database,diary=True)
    # This diary scenario owns its calendar instead of inheriting planner defaults.
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE life.actor_state SET timezone=%s WHERE actor_id=%s",
            ("Asia/Shanghai",value.context.actor_id))
        await connection.execute(
            "UPDATE life.routines SET timezone=%s,wake_at='08:00',sleep_at='23:00',"
            "enabled=true,idle_seconds=5400 WHERE actor_id=%s",
            ("Asia/Shanghai",value.context.actor_id))
    life = value.worker.life
    identity, jobs = Authority(), Jobs()
    from tianshu.identity.service import IdentityService
    from tianshu.runtime.jobs import JobQueue
    from tianshu.runtime.recovery_policy import RecoveryPolicy
    identity.scene_id = IdentityService.scene_id
    jobs.current_generation_on = JobQueue.current_generation_on
    jobs.assert_generation_on = JobQueue.assert_generation_on
    async def current_generation():
        async with database.transaction(read_only=True) as connection:
            return await jobs.current_generation_on(connection)
    jobs.current_generation = current_generation
    episode = Episode("actual","activity:completed",1,"actor",OriginKind.ACTOR,
        datetime(2026,11,1,18,tzinfo=UTC),"在想象的小屋里整理书架",
        (SourceRef("actual-completion",1),),("actor-scope",),
        {"imagined":True,"representation":"fictional_world","software_action":False},Coverage(True),1,"e:1")
    memory = Memory(() if empty else (episode,))
    life.identity, life.jobs, life.memory = identity, jobs, memory
    value.worker.assembler.identity = identity
    diaries = DiaryService(life,value.models,value.worker.assembler)
    diaries.bind_observability(recovery_policy=RecoveryPolicy(),statistics=None)
    value.models.cancel = AsyncMock()
    return SimpleNamespace(diaries=diaries,life=life,models=value.models,context=value.context,
        memory=memory,episode=episode,identity=identity,jobs=jobs)


async def day_state(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.diary_days")).fetchone()


async def diary_state(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.diaries")).fetchone()


async def start_day(value):
    day, scope = "2026-11-02", value.identity.scene_id(value.context)
    await value.diaries.day_closed(value.context,day)
    await value.diaries.review_day("actor",scope,day,1,await value.jobs.current_generation())
    return day,scope


async def collect_current(database, value):
    diary = await diary_state(database)
    await value.diaries.collect(diary["id"],diary["generation_id"],None)
    return await diary_state(database)


async def unbound_saved_day(database):
    value = await historical_fixture(database)
    value.context = replace(value.context,scope_ids=("author-scope","other-scope","actor-scope"),
        audience=("author-scope","other-scope","actor-scope"),origin_kind=OriginKind.HUMAN)
    scheduled = replace(value.context,scope_ids=("author-scope","actor-scope"),
        audience=("author-scope","actor-scope"),origin_kind=OriginKind.TIMER)
    await value.diaries.day_closed(scheduled,"2026-11-02")
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET material_revisions=%s,blocked_revision=revision,"
            "reviewed_revision=revision,last_error='context_mandatory_budget_exceeded'",(document({"actual":1}),))
    # Preserve the production shape created before manual requests could bind
    # an author's wider scene to its existing narrower automatic day.
    with patch.object(value.diaries.automation,"bind",AsyncMock(return_value=(None,value.context))):
        await value.diaries.request(value.context,{"day":"2026-11-02","expected_revision":0,
            "client_request_id":"historical-unbound-success"})
    current = await collect_current(database,value)
    await value.diaries.generate(current["id"],current["generation_id"])
    return value,await diary_state(database),scheduled


@pytest.mark.asyncio
async def test_closed_local_day_creates_a_source_backed_draft_and_same_material_does_not_regenerate(database):
    value = await fixture(database)
    day, _scope = await start_day(value)
    diary = await collect_current(database,value)
    await value.diaries.generate(diary["id"],diary["generation_id"])
    first = await diary_state(database)
    covered = await day_state(database)
    assert first["state"] == "draft" and first["current_revision"] == 1
    assert covered["reviewed_revision"] == covered["revision"] == 1
    assert covered["material_revisions"] == {"actual":1} and covered["generation_id"] is None
    page = await value.diaries.list(value.context)
    assert page.items[0]["day"] == day
    assert page.items[0]["automatic"]["state"] == "reviewed"
    assert page.items[0]["automatic"]["day"] == day
    assert "material_revisions" not in page.items[0]["automatic"]
    read = await value.diaries.read(value.context,{"id":first["id"]})
    assert read["diary"]["day"] == day
    assert read["diary"]["automatic"] == page.items[0]["automatic"]
    start,end = value.memory.windows[0]
    assert start.astimezone(UTC) == datetime(2026,11,1,16,tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026,11,2,16,tzinfo=UTC)
    # A derived diary and an unrelated global sequence change are not new experience.
    value.memory.episodes += (replace(value.episode,episode_id="diary-output",sequence=999,
        actual_result={"derived_kind":"diary"}),)
    value.memory.watermark = "e:999"
    await value.diaries.day_closed(value.context,day)
    await value.diaries.request(value.context,{"client_request_id":"same-material-review",
        "day":day,"expected_revision":first["revision"]})
    again = await collect_current(database,value)
    assert again["state"] == "draft" and again["reason"] == "diary_no_new_material"
    assert again["current_revision"] == 1 and again["last_changed_at"] == first["last_changed_at"]
    assert len(value.models.calls) == 1
    await value.diaries.changed(value.context,"same-original",episode=value.episode)
    assert (await day_state(database))["revision"] == 1


@pytest.mark.asyncio
async def test_empty_or_derived_only_day_does_not_call_model_and_restart_does_not_invent_days(database):
    value = await fixture(database,empty=True)
    await value.diaries.recover()
    assert value.jobs.items == [] and value.models.calls == []
    await start_day(value)
    diary = await collect_current(database,value)
    assert diary["state"] == "empty" and diary["reason"] == "diary_no_new_material"
    assert (await day_state(database))["reviewed_revision"] == 1
    await value.diaries.recover()
    assert value.models.calls == []


@pytest.mark.asyncio
async def test_changed_original_revision_revises_that_day_once_and_preserves_publication_boundary(database):
    value = await fixture(database)
    day,scope = await start_day(value)
    diary = await collect_current(database,value)
    await value.diaries.generate(diary["id"],diary["generation_id"])
    value.memory.episodes = (replace(value.episode,revision=2,summary="实际只整理了一层书架"),)
    revised = value.memory.episodes[0]
    await value.diaries.changed(value.context,"revision:2",episode=revised)
    await value.diaries.changed(value.context,"duplicate:2",episode=revised)
    assert (await day_state(database))["revision"] == 2
    await value.diaries.review_day("actor",scope,day,2, await value.jobs.current_generation())
    diary = await collect_current(database,value)
    await value.diaries.generate(diary["id"],diary["generation_id"])
    result = await diary_state(database)
    assert result["current_revision"] == 2 and result["published_revision"] is None
    assert len(value.models.calls) == 2
    async with database.transaction(read_only=True) as connection:
        versions = await (await connection.execute(
            "SELECT recipe,disclosure_ref FROM life.diary_revisions ORDER BY revision")).fetchall()
    assert [row["recipe"]["episodes"][0]["revision"] for row in versions] == [1,2]
    assert all(row["disclosure_ref"] is None for row in versions)


async def test_new_experience_updates_a_previously_blocked_saved_day(database):
    value=await fixture(database)
    day,scope=await start_day(value)
    diary=await collect_current(database,value)
    await value.diaries.generate(diary["id"],diary["generation_id"])
    later=replace(value.episode,episode_id="later-tool",event_id="later-tool",sequence=2,
                  origin_kind=OriginKind.TOOL_RESULT,actual_result={"execution":"succeeded"})
    value.memory.episodes+=(later,)
    await value.diaries.changed(value.context,"later-tool:1",episode=later)
    async with database.transaction() as conn:
        await conn.execute("UPDATE life.diary_days SET blocked_revision=revision,last_error='diary_saved_material_unreconciled'")
    await value.diaries.review_day("actor",scope,day,2,await value.jobs.current_generation())
    pending=await collect_current(database,value)
    assert pending["state"]=="generating" and pending["current_revision"]==1
    await value.diaries.generate(pending["id"],pending["generation_id"])
    final=await diary_state(database)
    assert final["current_revision"]==2 and final["published_revision"] is None
    assert (await day_state(database))["last_error"] is None
    assert len(value.models.calls)==2


@pytest.mark.parametrize("submitted", [False, True])
async def test_failed_collection_can_resume_without_reusing_its_old_command(database,submitted):
    value=await fixture(database)
    day,scope=await start_day(value)
    original=await diary_state(database)
    with patch.object(value.memory,"episodes_between",AsyncMock(side_effect=AppError("source_dependency_budget_exceeded",status=503))):
        with pytest.raises(AppError,match="source_dependency_budget_exceeded"):
            await collect_current(database,value)
    assert (await day_state(database))["blocked_revision"]==1 and not value.models.calls
    await value.diaries.recover()
    if submitted:
        # An existing frozen request is not a collection-only failure. Keep
        # observing that original rather than creating another physical call.
        with patch.object(value.diaries.generations,"observe_request",AsyncMock(return_value=object())):
            result=await value.diaries.review_day("actor",scope,day,1,await value.jobs.current_generation())
        assert result["state"]=="blocked"
        assert (await diary_state(database))["generation_id"]==original["generation_id"]
        assert not value.models.calls
        return
    await value.diaries.review_day("actor",scope,day,1,await value.jobs.current_generation())
    current=await diary_state(database)
    assert current["state"]=="collecting" and current["generation_id"]!=original["generation_id"]
    current=await collect_current(database,value)
    await value.diaries.generate(current["id"],current["generation_id"])
    assert (await diary_state(database))["current_revision"]==1 and len(value.models.calls)==1


@pytest.mark.asyncio
async def test_unknown_same_scene_new_epoch_observes_original_and_late_success_commits_once(database):
    value = await fixture(database,unknown=True)
    day,scope = await start_day(value)
    diary = await collect_current(database,value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"],diary["generation_id"])
    original = value.models.calls[0]
    assert (await diary_state(database))["state"] == "unknown"
    value.identity.epoch = 2
    value.diaries.generations.observe_request = AsyncMock(wraps=value.diaries.generations.observe_request)
    observed = await value.diaries.review_day("actor",scope,day,1, await value.jobs.current_generation())
    assert observed["state"] == "unknown" and len(value.models.calls) == 1
    original_observation = value.diaries.generations.observe_request.await_args
    assert original_observation.args[0] == original.attempt_id
    assert original_observation.kwargs["observer"].access_epoch == 2
    value.models.actual = replace(value.models.actual,
        receipt=replace(value.models.actual.receipt,status="succeeded",dispatch="submitted"))
    await value.diaries.review_day("actor",scope,day,1, await value.jobs.current_generation())
    result = await diary_state(database)
    assert result["state"] == "draft" and result["current_revision"] == 1
    assert value.models.observations[-1][1].access_epoch == 2
    frozen = await value.diaries.generations.request(original.attempt_id,original.context)
    assert frozen.context == original.context and frozen.budget.trace_id == original.budget.trace_id
    assert len(value.models.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("timing,admit", [("new_unbounded", True), ("legacy", False), ("business_expired", False)])
async def test_recovery_uses_original_business_expiry_without_rewriting_a_frozen_preparation_budget(database, timing, admit):
    value = await fixture(database)
    await start_day(value)
    diary = await collect_current(database, value)
    past = datetime.now(UTC)-timedelta(seconds=1)
    generation = await value.jobs.current_generation()
    frozen = ModelRequest("diary:"+diary["generation_id"], diary["generation_id"], "reflect", ServiceClass.ELASTIC,
        value.context, RequestBudget(past), (), execution_timeout_seconds=None if timing == "legacy" else 300,
        business_deadline_at=past if timing == "business_expired" else None,
        route_id="fixture-route",route_revision=1,protocol="openai.chat",data_generation=generation)
    queued = ModelReceipt(frozen.attempt_id, frozen.intent_id, "queued", "not_started",
        "fixture-route", 1, "provider", "model", "openai.chat", "quota",
        request_digest=submission_digest(frozen),data_generation=generation)
    await value.diaries.generations.prepare("diary:"+diary["generation_id"],frozen)
    value.models.submit = AsyncMock(return_value=queued)
    assert frozen.budget.deadline_at == past and frozen.attempt_id == diary["generation_id"]
    if admit:
        outcome = await value.diaries.generate(diary["id"], diary["generation_id"])
        assert outcome["state"] == "generating"
        assert value.models.submit.await_args.args[0] == frozen
    else:
        with pytest.raises(AppError,match="generation_request_expired"):
            await value.diaries.generate(diary["id"], diary["generation_id"])
        row = await diary_state(database)
        assert row["state"] == "failed" and row["reason"] == "generation_request_expired"
        value.models.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_revised_material_rejects_late_success_then_legal_new_material_can_continue(database):
    value = await fixture(database,unknown=True)
    day,scope = await start_day(value)
    diary = await collect_current(database,value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"],diary["generation_id"])
    value.memory.episodes = (replace(value.episode,revision=2),)
    await value.diaries.changed(value.context,"actual:2",episode=value.memory.episodes[0])
    value.models.actual = replace(value.models.actual,
        receipt=replace(value.models.actual.receipt,status="succeeded",dispatch="submitted"))
    await value.diaries.review_day("actor",scope,day,2, await value.jobs.current_generation())
    refused = await diary_state(database)
    assert refused["reason"] == "diary_material_changed" and refused["current_revision"] == 0
    pending = await day_state(database)
    assert pending["reviewed_revision"] == 0 and pending["blocked_revision"]==1 and pending["revision"] == 2
    await value.diaries.review_day("actor",scope,day,2, await value.jobs.current_generation())
    fresh = await collect_current(database,value)
    assert fresh["generation_id"] != diary["generation_id"] and fresh["state"] == "generating"
    assert len(value.models.calls) == 1  # New actual material has only been collected.


@pytest.mark.asyncio
async def test_cancelled_unknown_keeps_original_receipt_and_discards_late_content(database):
    value = await fixture(database,unknown=True)
    await start_day(value)
    diary = await collect_current(database,value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"],diary["generation_id"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diaries SET generation_cancel_requested=true,state='cancelling'")
    result = await value.diaries.control_generation(diary["id"],diary["generation_id"],value.context,"cancel")
    assert result["state"] == "cancelling" and value.models.actual.receipt.status == "unknown"
    value.models.actual = replace(value.models.actual,
        receipt=replace(value.models.actual.receipt,status="succeeded",dispatch="submitted"))
    await value.diaries.generate(diary["id"],diary["generation_id"])
    cancelled = await diary_state(database)
    assert cancelled["state"] == "cancelled" and cancelled["current_revision"] == 0
    assert cancelled["reason"] == "model_completed_after_cancel" and len(value.models.calls) == 1


@pytest.mark.asyncio
async def test_other_scope_and_unclosed_days_do_not_dirty_a_closed_diary(database):
    value = await fixture(database,empty=True)
    await start_day(value)
    await collect_current(database,value)
    for episode in (replace(value.episode,scope_ids=("unrelated-private",)),
                    replace(value.episode,occurred_at=datetime(2026,11,3,tzinfo=UTC)),
                    replace(value.episode,actual_result={"derived_kind":"reflection"})):
        await value.diaries.changed(value.context,"unrelated",episode=episode)
    row = await day_state(database)
    assert row["revision"] == row["reviewed_revision"] == 1 and value.models.calls == []


@pytest.mark.asyncio
async def test_source_loss_retires_diary_application_without_relabeling_unknown_model(database):
    value = await fixture(database,unknown=True)
    day,scope = await start_day(value)
    diary = await collect_current(database,value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"],diary["generation_id"])
    async def denied(*_args):
        return False
    value.life.sources.validate_persistence = denied
    with pytest.raises(AppError,match="source_unavailable"):
        await value.diaries.review_day("actor",scope,day,1, await value.jobs.current_generation())
    current = await diary_state(database)
    assert current["state"] == "failed" and current["reason"] == "source_unavailable"
    assert current["current_revision"] == 0 and value.models.actual.receipt.status == "unknown"
    assert len(value.models.calls) == 1


@pytest.mark.asyncio
async def test_another_principal_cannot_control_original_diary_execution(database):
    value = await fixture(database,unknown=True)
    await start_day(value)
    diary = await collect_current(database,value)
    with pytest.raises(TimeoutError):
        await value.diaries.generate(diary["id"],diary["generation_id"])
    caller = replace(value.context,principal_id="different-author")
    with pytest.raises(AppError,match="generation_observation_context_mismatch"):
        await value.diaries.control_generation(diary["id"],diary["generation_id"],caller,"cancel")
    value.models.cancel.assert_not_called()
    assert (await diary_state(database))["state"] == "unknown"


@pytest.mark.asyncio
async def test_manual_recovery_binds_only_its_closed_day_and_saved_reconcile_changes_only_metadata(database):
    value = await fixture(database)
    value.episode = replace(value.episode,coverage=Coverage(False,None,("semantic_unassessed",)))
    value.memory.episodes = (value.episode,)
    day,scope = await start_day(value)
    original = await collect_current(database,value)
    failed_at = datetime.now(UTC)-timedelta(days=1)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diaries SET state='failed',reason='context_mandatory_budget_exceeded'")
        await connection.execute("UPDATE life.diary_days SET generation_id='another-active',generation_revision=1,"
            "reviewed_revision=revision,last_error='context_mandatory_budget_exceeded',last_reviewed_at=%s",(failed_at,))
    body = {"day":day,"expected_revision":original["revision"],"client_request_id":"manual-recovery"}
    with pytest.raises(AppError,match="diary_review_no_longer_current"):
        await value.diaries.request(value.context,body)
    assert (await day_state(database))["generation_id"] == "another-active"
    assert (await diary_state(database))["generation_id"] == original["generation_id"]
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET generation_id=NULL,generation_revision=NULL")
    # Preserve the historical manual request that had no automatic binding.
    with patch.object(value.diaries.automation,"bind",AsyncMock(return_value=(None,value.context))):
        await value.diaries.request(value.context,body)
    current = await collect_current(database,value)
    assert current["generation_recipe"]["automatic"] is None
    await value.diaries.generate(current["id"],current["generation_id"])
    saved = await diary_state(database)
    settled = await day_state(database)
    assert saved["state"] == "draft" and saved["current_revision"] == 1
    assert settled["generation_id"] is None and settled["last_error"] == "context_mandatory_budget_exceeded"

    # The deployed unbound-success case is repaired through the normal command.
    async with database.transaction() as connection:
        immutable = await (await connection.execute("SELECT * FROM life.diary_revisions")).fetchall()
        frozen = await (await connection.execute("SELECT * FROM conversation.generation_requests")).fetchall()
        audit = await (await connection.execute("SELECT * FROM life.generation_recipes")).fetchall()
        await connection.execute("UPDATE life.diary_days SET last_error='context_mandatory_budget_exceeded',last_reviewed_at=%s",(failed_at,))
    controls = GenerationControl(value.life,None,value.diaries)
    queued = len(value.jobs.items)
    await controls.request(value.context,{"id":saved["id"],"expected_revision":saved["revision"],
        "client_request_id":"reconcile-saved-metadata"},kind="diary",operation="reconcile")
    settled = await day_state(database)
    assert settled["last_error"] is None and settled["last_reviewed_at"] == immutable[0]["created_at"]
    assert not immutable[0]["recipe"]["episodes"][0]["coverage"]["complete"]
    assert (await diary_state(database)) == saved and len(value.models.calls) == 1 and len(value.jobs.items) == queued
    async with database.transaction(read_only=True) as connection:
        assert await (await connection.execute("SELECT * FROM life.diary_revisions")).fetchall() == immutable
        assert await (await connection.execute("SELECT * FROM conversation.generation_requests")).fetchall() == frozen
        assert await (await connection.execute("SELECT * FROM life.generation_recipes")).fetchall() == audit

    # A new explicit refresh binds even an already-reviewed material version.
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.actor_state SET timezone='UTC'")
    refresh = await value.diaries.request(value.context,{"day":day,"expected_revision":saved["revision"],
        "client_request_id":"manual-reviewed-refresh"})
    bound = await day_state(database)
    assert bound["generation_id"] == refresh.attempt_id and bound["generation_revision"] == 1
    current = await diary_state(database)
    assert current["generation_recipe"]["timezone"] == "Asia/Shanghai"
    assert current["generation_recipe"]["automatic"]["revision"] == 1
    current = await collect_current(database,value)
    assert current["state"] == "draft" and current["reason"] == "diary_no_new_material"
    assert (await day_state(database))["last_error"] is None and len(value.models.calls) == 1

    later = replace(value.episode,episode_id="later-actual",summary="稍后实际发生的另一件事")
    value.memory.episodes += (later,)
    await value.diaries.changed(value.context,"later",episode=later)
    manual = await value.diaries.request(value.context,{"day":day,"expected_revision":current["revision"],
        "client_request_id":"manual-new-material"})
    current = await collect_current(database,value)
    newest = replace(value.episode,episode_id="newest-actual",summary="生成期间实际新增的经历")
    value.memory.episodes += (newest,)
    await value.diaries.changed(value.context,"newest",episode=newest)
    value.models.output = ""
    with pytest.raises(AppError,match="diary_generation_failed"):
        await value.diaries.generate(current["id"],manual.attempt_id)
    failed = await day_state(database)
    assert failed["generation_id"] is None and failed["reviewed_revision"] == 1
    assert failed["blocked_revision"] == 2 and failed["revision"] == 3
    assert any(job.task == "life.diary_review" and job.payload["expected_revision"] == 3 for job in value.jobs.items)
    await value.diaries.review_day("actor",scope,day,3, await value.jobs.current_generation())
    current = await collect_current(database,value)
    value.models.output = "仅记录实际经历的新版本。"
    await value.diaries.generate(current["id"],current["generation_id"])
    assert (await day_state(database))["reviewed_revision"] == 3 and (await day_state(database))["last_error"] is None

    # A wider author follows the unique closed scene; an unclosed date stays unbound.
    broader = replace(value.context,scope_ids=(*value.context.scope_ids,"other-scope"),
        audience=(*value.context.audience,"other-scope"))
    current = await diary_state(database)
    refresh = await value.diaries.request(broader,{"day":day,"expected_revision":current["revision"],"client_request_id":"wider-author"})
    assert (await diary_state(database))["generation_recipe"]["automatic"]["scope_key"] == scope
    assert (await day_state(database))["generation_id"] == refresh.attempt_id
    assert (await diary_state(database))["context"]["scope_ids"] == list(value.context.scope_ids)
    await collect_current(database,value)
    assert (await day_state(database))["generation_id"] is None
    await value.diaries.request(broader,{"day":"2026-11-04","expected_revision":0,"client_request_id":"unclosed-date"})
    async with database.transaction(read_only=True) as connection:
        assert len(await (await connection.execute("SELECT * FROM life.diary_days")).fetchall()) == 1
    assert len(value.models.calls) == 3


@pytest.mark.asyncio
async def test_saved_success_reconciles_its_unique_narrower_day_without_rewriting_content_or_receipts(database):
    value,saved,scheduled = await unbound_saved_day(database)
    assert saved["scope_ids"] == ["actor-scope"]
    assert saved["context"]["scope_ids"] == list(value.context.scope_ids)
    assert (await day_state(database))["context"]["scope_ids"] == list(scheduled.scope_ids)
    async with database.transaction(read_only=True) as connection:
        immutable = await (await connection.execute("SELECT * FROM life.diary_revisions")).fetchall()
        frozen = await (await connection.execute("SELECT * FROM conversation.generation_requests")).fetchall()
        audit = await (await connection.execute("SELECT * FROM life.generation_recipes")).fetchall()
    assert immutable[0]["recipe"]["automatic"] is None
    assert immutable[0]["recipe"]["material_revisions"] == [["actual",1]]
    receipt,queued = value.models.actual,len(value.jobs.items)
    value.identity.epoch = 2
    context = replace(value.context,access_epoch=2)
    value.memory.episodes_by_refs = AsyncMock(wraps=value.memory.episodes_by_refs)
    controls = GenerationControl(value.life,None,value.diaries)
    await controls.request(context,{"id":saved["id"],"expected_revision":saved["revision"],
        "client_request_id":"reconcile-wider-author"},kind="diary",operation="reconcile")
    day = await day_state(database)
    assert day["last_error"] is None and day["last_reviewed_at"] == immutable[0]["created_at"]
    assert day["reviewed_revision"] == day["revision"] and day["generation_id"] is None
    reader = value.memory.episodes_by_refs.await_args.args[0]
    assert reader.scope_ids == scheduled.scope_ids and reader.audience == scheduled.audience and reader.access_epoch == 2
    assert (await diary_state(database)) == saved and value.models.actual == receipt
    assert len(value.models.calls) == 1 and len(value.jobs.items) == queued
    async with database.transaction(read_only=True) as connection:
        assert await (await connection.execute("SELECT * FROM life.diary_revisions")).fetchall() == immutable
        assert await (await connection.execute("SELECT * FROM conversation.generation_requests")).fetchall() == frozen
        assert await (await connection.execute("SELECT * FROM life.generation_recipes")).fetchall() == audit


@pytest.mark.asyncio
async def test_manual_wider_author_preserves_active_day_then_collects_only_its_original_scene(database):
    value,saved,scheduled = await unbound_saved_day(database)
    body = {"day":"2026-11-02","expected_revision":saved["revision"],"client_request_id":"manual-wider-scene"}
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET generation_id='another-active',generation_revision=revision")
    with pytest.raises(AppError,match="diary_review_no_longer_current"):
        await value.diaries.request(value.context,body)
    assert (await day_state(database))["generation_id"] == "another-active" and (await diary_state(database)) == saved
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.diary_days SET generation_id=NULL,generation_revision=NULL")
    extra = replace(value.episode,episode_id="outside-closed-scene",scope_ids=("other-scope",))
    later = replace(value.episode,episode_id="later-actual")
    value.memory.episodes += (extra,later)
    value.identity.epoch = 2
    manual = await value.diaries.request(replace(value.context,access_epoch=2),body)
    current = await diary_state(database)
    assert current["context"]["scope_ids"] == list(scheduled.scope_ids)
    assert current["context"]["audience"] == list(scheduled.audience) and current["context"]["access_epoch"] == 2
    assert current["generation_recipe"]["automatic"]["scope_key"] == signature(sorted(scheduled.scope_ids))
    assert (await day_state(database))["generation_id"] == manual.attempt_id
    current = await collect_current(database,value)
    assert current["episode_ids"] == ["actual","later-actual"]
    assert current["generation_recipe"]["material_revisions"] == [["actual",1],["later-actual",1]]
    assert len(value.models.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["principal","scene","scope","audience","actor","day","timezone",
    "active","revised","uncovered","other-day","source","caller"])
async def test_saved_reconcile_does_not_clear_an_unproven_or_other_closed_day(database,mismatch):
    value,saved,scheduled = await unbound_saved_day(database)
    context,day_context = value.context,scheduled
    if mismatch == "principal":
        day_context = replace(scheduled,principal_id="another-author")
    elif mismatch == "scene":
        day_context = replace(scheduled,person_id="another-person")
    elif mismatch == "scope":
        day_context = replace(scheduled,scope_ids=(*scheduled.scope_ids,"unavailable-private"))
    elif mismatch == "audience":
        day_context = replace(scheduled,audience=(*scheduled.audience,"unavailable-private"))
    elif mismatch == "caller":
        context = replace(context,principal_id="another-author")
    async with database.transaction() as connection:
        if day_context != scheduled:
            await connection.execute("UPDATE life.diary_days SET context=%s,scope_ids=%s,scope_key=%s",
                (document(day_context),list(day_context.scope_ids),signature(sorted(day_context.scope_ids))))
        elif mismatch == "actor":
            await connection.execute("UPDATE life.diary_days SET actor_id='another-actor'")
        elif mismatch == "day":
            await connection.execute("UPDATE life.diary_days SET day='2026-11-03'")
        elif mismatch == "timezone":
            await connection.execute("UPDATE life.diary_days SET timezone='UTC'")
        elif mismatch == "active":
            await connection.execute("UPDATE life.diary_days SET generation_id='another-active',generation_revision=revision")
        elif mismatch == "uncovered":
            await connection.execute("UPDATE life.diary_days SET material_revisions=%s",(document({"actual":1,"not-in-saved-success":1}),))
    if mismatch == "revised":
        value.memory.episodes = (replace(value.episode,revision=2),)
    elif mismatch == "other-day":
        value.memory.episodes = (replace(value.episode,occurred_at=datetime(2026,11,3,tzinfo=UTC)),)
    elif mismatch == "source":
        value.life.sources.validate_persistence = AsyncMock(return_value=False)
    before = await day_state(database)
    controls = GenerationControl(value.life,None,value.diaries)
    async def reconcile():
        return await controls.request(context,{"id":saved["id"],"expected_revision":saved["revision"],
            "client_request_id":"reject-"+mismatch},kind="diary",operation="reconcile")
    if mismatch in {"source","caller"}:
        with pytest.raises(AppError,match="source_unavailable" if mismatch == "source" else "generation_observation_context_mismatch"):
            await reconcile()
    else:
        await reconcile()
    assert await day_state(database) == before
    assert (await diary_state(database)) == saved and len(value.models.calls) == 1


@pytest.mark.asyncio
async def test_multiple_narrower_days_are_ambiguous_but_an_exact_original_scene_keeps_priority(database):
    # RB identifies a day by actor/scene/day, not by the reader's scope set.
    # Multiple narrower scope keys within one scene can no longer be created.
    # A narrower day in a different scene must never be selected instead.
    value,saved,scheduled = await unbound_saved_day(database)
    other = replace(scheduled,conversation_id="another-group",conversation_kind="group",
        scope_ids=("actor-scope",),audience=("actor-scope",))
    await value.diaries.day_closed(other,"2026-11-02")
    async with database.transaction(read_only=True) as connection:
        before = await (await connection.execute("SELECT * FROM life.diary_days ORDER BY scope_key")).fetchall()
    controls = GenerationControl(value.life,None,value.diaries)
    body = {"id":saved["id"],"expected_revision":saved["revision"],"client_request_id":"exact-scene-priority"}
    await controls.request(value.context,body,kind="diary",operation="reconcile")
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute("SELECT * FROM life.diary_days ORDER BY scope_key")).fetchall()
    exact = next(row for row in rows if row["scope_key"] == value.identity.scene_id(value.context))
    assert exact["reviewed_revision"] == exact["revision"] and exact["last_error"] is None
    assert [row for row in rows if row["scope_key"] != exact["scope_key"]] == [
        row for row in before if row["scope_key"] != exact["scope_key"]]
    assert exact["context"]["scope_ids"] == list(scheduled.scope_ids)
    assert (await diary_state(database)) == saved and len(value.models.calls) == 1
