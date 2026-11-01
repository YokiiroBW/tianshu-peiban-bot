"""New local-day diary work uses real lifecycle tables and isolated episode ports."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import signature
from tianshu.life.diaries import DiaryService
from tianshu.memory.types import Episode, EpisodePage
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, OriginKind, SourceRef
from test_planner_execution import setup


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

    async def enqueue(self, _connection, spec):
        self.items.append(spec)


class Memory:
    def __init__(self, episodes):
        self.episodes = episodes
        self.watermark = "e:1"
        self.windows = []

    async def episodes_between(self, _context, start, end, *, limit, cursor):
        self.windows.append((start,end))
        rows = tuple(item for item in self.episodes if start <= item.occurred_at < end)
        return EpisodePage(rows,None,Coverage(True),self.watermark)

    async def episodes_by_refs(self, _context, refs, *, connection=None):
        assert connection is not None  # The final source/Episode/diary CAS is atomic.
        found = tuple(item for item in self.episodes if (item.episode_id,item.revision) in refs)
        return Page(found,None,Coverage(len(found) == len(refs)))


async def fixture(database, *, empty=False, unknown=False):
    value = await setup(database,"今天只记下实际完成的小事。",lose_response=unknown)
    life = value.worker.life
    identity, jobs = Authority(), Jobs()
    episode = Episode("actual","activity:completed",1,"actor",OriginKind.ACTOR,
        datetime(2026,11,1,18,tzinfo=UTC),"在想象的小屋里整理书架",
        (SourceRef("actual-completion",1),),("actor-scope",),
        {"imagined":True,"representation":"fictional_world","software_action":False},Coverage(True),1,"e:1")
    memory = Memory(() if empty else (episode,))
    life.identity, life.jobs, life.memory = identity, jobs, memory
    value.worker.assembler.identity = identity
    diaries = DiaryService(life,value.models,value.worker.assembler)
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
    day, scope = "2026-11-02", signature(sorted(value.context.scope_ids))
    await value.diaries.day_closed(value.context,day)
    await value.diaries.review_day("actor",scope,day,1)
    return day,scope


async def collect_current(database, value):
    diary = await diary_state(database)
    await value.diaries.collect(diary["id"],diary["generation_id"],None)
    return await diary_state(database)


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
    assert page.items[0]["automatic"]["state"] == "reviewed"
    assert page.items[0]["automatic"]["day"] == day
    assert "material_revisions" not in page.items[0]["automatic"]
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
    await value.diaries.review_day("actor",scope,day,2)
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
    observed = await value.diaries.review_day("actor",scope,day,1)
    assert observed["state"] == "unknown" and len(value.models.calls) == 1
    assert value.models.observations[-1][1].access_epoch == 2
    value.models.actual = replace(value.models.actual,
        receipt=replace(value.models.actual.receipt,status="succeeded",dispatch="submitted"))
    await value.diaries.review_day("actor",scope,day,1)
    result = await diary_state(database)
    assert result["state"] == "draft" and result["current_revision"] == 1
    frozen = await value.diaries.generations.request(original.attempt_id,original.context)
    assert frozen.context == original.context and frozen.budget.trace_id == original.budget.trace_id
    assert len(value.models.calls) == 1


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
    await value.diaries.review_day("actor",scope,day,2)
    refused = await diary_state(database)
    assert refused["reason"] == "diary_episode_revised" and refused["current_revision"] == 0
    pending = await day_state(database)
    assert pending["reviewed_revision"] == 1 and pending["revision"] == 2
    await value.diaries.review_day("actor",scope,day,2)
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
        await value.diaries.review_day("actor",scope,day,1)
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
