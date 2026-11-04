"""Only real media transfers spend shared attempts; unknown is not replayed."""

import asyncio
import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.execution_context import CURRENT_ACTION
from tianshu.capabilities.media.recovery import physical_download
from tianshu.capabilities.media.music.selection import Selection
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryPolicy
from tianshu.types import Action, OriginKind, TrustedContext


async def setup_media(database):
    await migrate(database, (
        Migration("0007_rb_runtime", files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text()),
    ))
    async with database.transaction() as conn:
        generation = await JobQueue.current_generation_on(conn)
        job = {"job_id": "job", "generation": 1, "data_generation": generation,
               "cancel_requested": False, "context": {}}
        await conn.execute("INSERT INTO capability_media.jobs(id,dedupe,state,created,updated,generation,owner,body) "
            "VALUES('job','job','downloading',0,0,1,'owner',%s)", (json.dumps(job),))
    context = TrustedContext("owner", None, None, None, ("system",), ("system",), 1,
                             OriginKind.TIMER, frozenset({"*"}))
    action = Action("original", "original", "media.acquire", None, context,
                    {"job_id": "job"}, OriginKind.TIMER)
    capabilities = SimpleNamespace(recovery_policy=RecoveryPolicy(),
        runtime=SimpleNamespace(database=database, jobs=JobQueue(database.config.dsn)),
        authority=SimpleNamespace(check=AsyncMock()),
        sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True)))
    return SimpleNamespace(capabilities=capabilities), job, action


async def next_generation(database, job):
    async with database.transaction() as conn:
        saved = await (await conn.execute("SELECT body FROM capability_media.jobs WHERE id='job'")).fetchone()
        job = {**json.loads(saved["body"]), "generation": job["generation"] + 1}
        await conn.execute("UPDATE capability_media.jobs SET generation=%s,body=%s WHERE id='job'",
                           (job["generation"], json.dumps(job)))
        await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second'")
    return job


@pytest.mark.asyncio
async def test_download_retry_changes_route_without_resetting_budget(database):
    media, job, action = await setup_media(database)
    current = CURRENT_ACTION.set(action)
    try:
        with pytest.raises(Fault, match="download_failed"):
            async with physical_download(media, job, "owner", family="video", material="cid",
                                         route="first", request={"quality": "high"}):
                raise Fault("download_failed", 503)
        job = await next_generation(database, job)
        async with physical_download(media, job, "owner", family="video", material="cid",
                                     route="second", request={"quality": "low"}):
            pass  # This is the already selected physical transfer.
        async with database.transaction(read_only=True) as conn:
            lineages = await (await conn.execute("SELECT attempts,max_attempts FROM runtime.recovery_lineages")).fetchall()
            attempts = await (await conn.execute("SELECT route,status FROM runtime.recovery_attempts ORDER BY attempt_number")).fetchall()
        assert lineages == [{"attempts": 2, "max_attempts": 5}]
        assert attempts == [{"route": "first", "status": "failed"}, {"route": "second", "status": "succeeded"}]
        job = await next_generation(database, job)
        with pytest.raises(Fault, match="media_download_original_unresolved"):
            async with physical_download(media, job, "owner", family="video", material="cid",
                                         route="third", request={"quality": "low"}):
                pytest.fail("a successful original is not automatically downloaded again")
    finally:
        CURRENT_ACTION.reset(current)


@pytest.mark.asyncio
async def test_interrupted_transfer_remains_unknown_and_cannot_start_another(database):
    media, job, action = await setup_media(database)
    current = CURRENT_ACTION.set(action)
    try:
        with pytest.raises(asyncio.CancelledError):
            async with physical_download(media, job, "owner", family="comic", material=["chapter", 0],
                                         route="source", request={"index": 0}):
                raise asyncio.CancelledError
        job = await next_generation(database, job)
        with pytest.raises(Fault, match="media_download_original_unresolved"):
            async with physical_download(media, job, "owner", family="comic", material=["chapter", 0],
                                         route="replacement", request={"index": 0}):
                pytest.fail("unknown original must be observed")
        async with database.transaction(read_only=True) as conn:
            saved = await (await conn.execute("SELECT status FROM runtime.recovery_attempts")).fetchall()
        assert saved == [{"status": "unknown"}]
    finally:
        CURRENT_ACTION.reset(current)


@pytest.mark.asyncio
async def test_book_split_fallback_keeps_the_successful_part_and_its_budget(database):
    media, job, action = await setup_media(database)
    current = CURRENT_ACTION.set(action)
    try:
        async with physical_download(media, job, "owner", family="book", material=["chapter", 0],
                                     route="source-a", request={"part": "a"}, partition_size=2):
            pass
        job = await next_generation(database, job)
        async with physical_download(media, job, "owner", family="book", material=["chapter", 0],
                                     route="source-b", request={"part": "b"}, partition_size=1):
            pass
        async with database.transaction(read_only=True) as conn:
            rows = await (await conn.execute(
                "SELECT attempt_id,status,continuation_of,continuation_reason FROM runtime.recovery_attempts ORDER BY attempt_number"
            )).fetchall()
        assert len(rows) == 2 and rows[0]["status"] == rows[1]["status"] == "succeeded"
        assert rows[1]["continuation_of"] == rows[0]["attempt_id"]
        assert rows[1]["continuation_reason"] == "capacity_repartition"
    finally:
        CURRENT_ACTION.reset(current)


@pytest.mark.asyncio
async def test_music_compares_distinct_planned_candidates_and_reuses_confirmed_packages(database, tmp_path):
    media, job, action = await setup_media(database)
    job.update(quality={"level": "best", "allow_fallback": True}, recording_id="recording")
    async with database.transaction() as conn:
        await conn.execute("UPDATE capability_media.jobs SET body=%s WHERE id='job'", (json.dumps(job),))

    async def update(**changes):
        async with database.transaction() as conn:
            row = await (await conn.execute("SELECT body FROM capability_media.jobs WHERE id='job' FOR UPDATE")).fetchone()
            body = {**json.loads(row["body"]), **changes}
            await conn.execute("UPDATE capability_media.jobs SET body=%s WHERE id='job'", (json.dumps(body),))
        job.update(body)
        return body

    async def local(operation, *args):
        return operation(*args)

    media.local = local
    media.repo = SimpleNamespace(clock=lambda: 100)
    offers = [{"provider": "source-" + str(index), "track": {"source_id": "track"},
               "offered": {"level": "hires", "url": "https://example.invalid"},
               "account": {"account_id": "account-" + str(index), "provider": "source-" + str(index), "revision": 1}}
              for index in range(6)]

    async def acquire(_job, candidate, _update, *, before_request):
        await before_request()
        return {"quality": {"level": "standard"}, "files": [],
                "download_provider": candidate["provider"], "download_source_id": "track"}

    downloads = SimpleNamespace(media=media, resolver=SimpleNamespace(offers=AsyncMock(return_value=offers)),
        storage=SimpleNamespace(stage=lambda candidate_job, name: tmp_path / str(candidate_job["candidate_index"]),
                                available=lambda _job: None),
        transfer=SimpleNamespace(sample=AsyncMock(side_effect=Fault("music_download_unavailable", 503))),
        package_present=lambda package: package is not None, acquire=AsyncMock(side_effect=acquire))
    current = CURRENT_ACTION.set(action)
    try:
        await Selection(downloads).choose(job, update, owner="owner")
        assert downloads.acquire.await_count == 6
        async with database.transaction(read_only=True) as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_attempts WHERE status='succeeded'")).fetchone()
        assert row["n"] == 6
        await Selection(downloads).choose(job, update, owner="owner")
        assert downloads.acquire.await_count == 6
    finally:
        CURRENT_ACTION.reset(current)
