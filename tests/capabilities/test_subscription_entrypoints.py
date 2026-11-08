"""Console commands and automatic scans through real Actions and isolated media storage."""

import asyncio
import traceback
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.api import invoke
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.media.background import Background
from tianshu.capabilities.media.books.catalog import Catalog as BookCatalog
from tianshu.capabilities.media.books.runtime import Books
from tianshu.capabilities.media.books.sources import Sources
from tianshu.capabilities.media.config import EMPTY_RULES
from tianshu.capabilities.media.destination import head_key
from tianshu.capabilities.media.music.imports import Imports
from tianshu.capabilities.media.music.library import MusicLibrary
from tianshu.capabilities.media.music.runtime import Music
from tianshu.capabilities.media.repository import Repository
from tianshu.capabilities.media.rule_context import RuleSourceContext
from tianshu.capabilities.media.rules import RuleEvaluator
from tianshu.capabilities.media.scheduling import scan_due
from tianshu.capabilities.media.subscriptions import Subscriptions
from tianshu.capabilities.media.web import WebMedia
from tianshu.capabilities.service import Capabilities, Operation
from tianshu.capabilities.storage import run_repository
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, SourceRef, TrustedContext

pytestmark = pytest.mark.asyncio
BOOK_ID = "b" * 24


@pytest_asyncio.fixture
async def subscriptions(database, tmp_path):
    await migrate(
        database,
        (
            Migration(
                "capabilities-0001",
                files("tianshu.capabilities").joinpath("schema.sql").read_text(),
            ),
            Migration(
                "capabilities-0002-runtime-observations",
                files("tianshu.capabilities").joinpath("runtime_observations.sql").read_text(),
            ),
        ),
    )
    jobs = JobQueue(database.config.dsn, database=database)
    jobs.register("capabilities.media_job", AsyncMock())
    runtime = SimpleNamespace(database=database, files=None, jobs=jobs)
    clock = SimpleNamespace(now=100)

    async def local(fn, *args, **kwargs):
        return await run_repository(database, fn, *args, **kwargs)

    context = TrustedContext(
        "author",
        None,
        None,
        None,
        ("private",),
        ("author",),
        1,
        OriginKind.HUMAN,
        frozenset({"config.publish"}),
    )

    async def refresh(previous, **_kwargs):
        return replace(previous, origin_kind=OriginKind.TIMER, access_epoch=2)

    authority = SimpleNamespace(
        check=AsyncMock(), refresh_scheduled_context=AsyncMock(side_effect=refresh)
    )
    media = SimpleNamespace(
        repo=Repository("unused", runtime=runtime, clock=lambda: clock.now),
        local=local,
        wake=asyncio.Event(),
        config={
            "targets": {
                "video": {
                    "kind": "media_server",
                    "directory": str(tmp_path / "video"),
                }
            },
            "scan_timeout_seconds": 10,
            "scan_page_limit": 2,
            "scan_item_limit": 100,
        },
        accounts=SimpleNamespace(cookie=lambda _id: ""),
        connector=SimpleNamespace(scan=AsyncMock(return_value={})),
        comics=SimpleNamespace(),
    )
    media.music = object.__new__(Music)
    media.music.media = media
    media.music.connector = SimpleNamespace()
    media.music.library = MusicLibrary(media.repo, media.accounts)
    media.music.imports = Imports(media.music)
    media.books = object.__new__(Books)
    media.books.media, media.books.repo = media, media.repo
    media.books.catalog = BookCatalog(media)
    media.books.selection = SimpleNamespace(prepare=AsyncMock())
    media.books.sources = object.__new__(Sources)
    media.books.sources.media, media.books.sources.repo, media.books.sources.books = (
        media,
        media.repo,
        media.books,
    )
    media.books.network = SimpleNamespace(fetch=AsyncMock())
    media.subscriptions = Subscriptions(media)
    owner = object.__new__(Capabilities)
    owner.prepared, owner.media, owner.runtime, owner.authority = True, media, runtime, authority
    owner.sources = SimpleNamespace(read=AsyncMock(return_value={"content": "original"}))
    owner.catalog = Catalog()
    owner.actions = Actions(database, jobs, owner.catalog, authority)
    owner.actions.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    owner.actions.register_tasks()
    owner.web_media = WebMedia(media, authority, CommandStore(database))
    media.capabilities = owner
    failures = []
    original_operate = owner.operate

    async def operate(*args, **kwargs):
        try:
            return await original_operate(*args, **kwargs)
        except Exception:
            failures.append(traceback.format_exc())
            raise

    owner.operate = operate
    for domain, operations in {
        "media": ("subscriptions/save", "subscriptions/scan", "subscriptions/control", "resolve"),
        "music": ("settings/save", "library/scan"),
        "books": (
            "subscriptions/save",
            "subscriptions/scan",
            "subscriptions/control",
            "feeds/refresh",
        ),
    }.items():
        for operation in operations:
            owner.catalog.register(Operation(owner, domain, operation, permission="config.publish"))
    background = Background(runtime, next_due=lambda name: local(media.repo.maintenance_due, name))
    background.start("music_imports", media.music.imports.pump)
    background.start("subscriptions", media.subscriptions.pump)
    background.start("book_subscriptions", scan_due, media, "book_subscription", "book_id", None)
    background.start("book_sources", scan_due, media, "book_feed", "feed_id", None)

    async def command(domain, operation, body=None):
        result = await invoke(
            owner,
            domain,
            operation,
            {
                "client_request_id": uuid4().hex,
                "expected_revision": None,
                **(body or {}),
            },
            context,
        )
        receipt = await owner.actions.run(result.action_id)
        assert receipt.error_code != "capability_failed", failures
        return receipt

    yield SimpleNamespace(
        media=media,
        owner=owner,
        context=context,
        command=command,
        database=database,
        clock=clock,
        directory=str(tmp_path / "music"),
    )
    await background.close()
    await owner.actions.close()


async def save_music(state):
    receipt = await state.command(
        "music",
        "settings/save",
        {
            "directory": state.directory,
            "layout": "artist_album",
            "expected_revision": 0,
        },
    )
    assert receipt.execution == "succeeded", receipt.error_code
    return receipt


async def test_video_selector_survives_api_admission_and_empty_cas_scan(subscriptions):
    s = subscriptions
    saved = await s.command(
        "media",
        "subscriptions/save",
        {
            "subscription_id": None,
            "expected_revision": 0,
            "label": "UP 主测试",
            "source": {"kind": "uploader", "id": "123"},
            "account_id": None,
            "target_id": "video",
            "initial_sync": "future_only",
            "quality": {"mode": "best", "quality_id": None, "allow_fallback": True},
            "rules": EMPTY_RULES,
            "interval_seconds": 900,
        },
    )
    assert saved.execution == "succeeded", saved.error_code
    source = saved.result["subscription"]
    original = await s.owner.actions.get(saved.action_id, s.context)
    assert original["action"]["arguments"]["source"] == {"kind": "uploader", "id": "123"}
    assert original["action"]["source_refs"] == []
    scanned = await s.command(
        "media",
        "subscriptions/scan",
        {
            "subscription_id": source["subscription_id"],
            "recompute": False,
        },
    )
    assert scanned.execution == "succeeded", scanned.error_code
    assert scanned.result["subscription"]["baseline_ready"] is True
    assert scanned.result["queued"] == 0
    await s.media.local(
        s.media.repo.complete_scan,
        scanned.result["subscription"],
        {"BVtest"},
        [],
        part_members={"BVtest": ["101", "102"]},
    )
    assert await s.media.local(s.media.repo.members, source["subscription_id"]) == {"BVtest"}
    assert await s.media.local(s.media.repo.member_parts, source["subscription_id"]) == {
        "BVtest": {"101", "102"}
    }
    with pytest.raises(Fault, match="version_conflict"):
        await s.media.local(s.media.repo.put, "subscription", source["subscription_id"], source, 99)


async def test_true_source_references_still_attach_to_nonmedia_actions(subscriptions):
    s = subscriptions
    reference = SourceRef("original-material", 3, {"part": 2})
    s.owner.catalog.register(Operation(s.owner, "content", "continue"))
    result = await invoke(
        s.owner,
        "content",
        "continue",
        {
            "client_request_id": "continue-original",
            "source": wire(reference),
        },
        s.context,
    )
    assert result.receipt.execution == "succeeded"
    row = await s.owner.actions.get(result.action_id, s.context)
    assert row["action"]["source_refs"] == [wire(reference)]


REMOVED_VIDEO = "BV1HbMX6kE95"
AVAILABLE_VIDEO = "BV1xx411c7mD"


async def video_subscription(state, *, initial_sync="history"):
    state.media.evaluator = RuleEvaluator()
    state.media.connector.scan.return_value = {
        bvid: RuleSourceContext(bvid, "favorite", "4061483034")
        for bvid in (REMOVED_VIDEO, AVAILABLE_VIDEO)
    }
    saved = await state.command(
        "media", "subscriptions/save",
        {
            "subscription_id": None, "expected_revision": 0, "label": "收藏夹",
            "source": {"kind": "favorite", "id": "4061483034"}, "account_id": None,
            "target_id": "video", "initial_sync": initial_sync,
            "quality": {"mode": "best", "quality_id": None, "allow_fallback": True},
            "rules": EMPTY_RULES, "interval_seconds": 900,
        },
    )
    assert saved.execution == "succeeded", saved.error_code
    return saved.result["subscription"]


def resolved_video(bvid):
    return {
        "snapshot": {
            "schema_version": 1, "bvid": bvid, "title": "视频",
            "description": "简介", "published_at": "2026-11-08T00:00:00Z",
            "captured_at": "2026-11-08T01:00:00Z",
            "creators": [{"mid": "123", "name": "作者", "role": "uploader"}],
            "parts": [{"cid": "101", "index": 1, "title": "正片", "duration_seconds": 60}],
            "tags": [], "cover_available": False,
        },
        "video": {"cover_url": None}, "creator_images": [],
    }


def video_resolver(code="source_removed"):
    async def resolve(bvid, *_args, **_kwargs):
        if bvid == REMOVED_VIDEO:
            raise Fault(code, 503)
        return resolved_video(bvid)
    return resolve


@pytest.mark.parametrize("initial_sync,queued", [("history", 1), ("future_only", 0)])
async def test_video_removed_member_does_not_block_baseline_or_other_members(
    subscriptions, initial_sync, queued
):
    s = subscriptions
    source = await video_subscription(s, initial_sync=initial_sync)
    s.media.connector.resolve = AsyncMock(side_effect=video_resolver())
    receipt = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": False,
    })
    assert receipt.execution == "succeeded", receipt.error_code
    assert receipt.result["subscription"]["baseline_ready"] is True
    assert receipt.result["subscription"]["code"] == "scan_complete"
    assert receipt.result["queued"] == queued
    assert await s.media.local(s.media.repo.members, source["subscription_id"]) == {
        REMOVED_VIDEO, AVAILABLE_VIDEO,
    }
    assert await s.media.local(s.media.repo.member_parts, source["subscription_id"]) == {
        AVAILABLE_VIDEO: {"101"},
    }
    jobs, _more = await s.media.local(s.media.repo.jobs)
    assert [job["bvid"] for job in jobs] == ([AVAILABLE_VIDEO] if queued else [])
    repeated = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": False,
    })
    assert repeated.execution == "succeeded", repeated.error_code
    assert repeated.result["queued"] == 0


@pytest.mark.parametrize("code", ["auth_required", "source_rate_limited", "source_unavailable"])
async def test_video_uncertain_member_failure_does_not_commit_a_partial_baseline(subscriptions, code):
    s = subscriptions
    source = await video_subscription(s)
    s.media.connector.resolve = AsyncMock(side_effect=video_resolver(code))
    receipt = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": False,
    })
    assert receipt.execution == "failed" and receipt.error_code == code
    current = await s.media.local(s.media.repo.get, "subscription", source["subscription_id"])
    assert current["baseline_ready"] is False
    assert await s.media.local(s.media.repo.members, source["subscription_id"]) == set()
    assert await s.media.local(s.media.repo.job_count) == 0


async def test_video_removed_source_still_fails_without_establishing_an_empty_baseline(subscriptions):
    s = subscriptions
    source = await video_subscription(s)
    s.media.connector.scan.side_effect = Fault("source_removed", 503)
    receipt = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": False,
    })
    assert receipt.execution == "failed" and receipt.error_code == "source_removed"
    current = await s.media.local(s.media.repo.get, "subscription", source["subscription_id"])
    assert current["baseline_ready"] is False
    assert await s.media.local(s.media.repo.job_count) == 0


async def test_video_removed_member_preserves_local_file_receipt_and_parts_during_recompute(
    subscriptions, tmp_path
):
    s = subscriptions
    source = await video_subscription(s)
    s.media.connector.resolve = AsyncMock(side_effect=lambda bvid, *_args, **_kwargs: resolved_video(bvid))
    first = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": False,
    })
    assert first.execution == "succeeded" and first.result["queued"] == 2
    jobs, _more = await s.media.local(s.media.repo.jobs)
    original = next(job for job in jobs if job["bvid"] == REMOVED_VIDEO)
    local_file = tmp_path / "retained-video.mp4"
    local_file.write_bytes(b"original local video")
    original = await s.media.local(s.media.repo.update_job, original["job_id"], {
        "state": "published", "code": "published",
        "asset_receipt": {"original": "confirmed", "path": str(local_file)},
    })
    head = {"job_id": original["job_id"], "original": "local-publication"}
    identity = head_key({**source, "bvid": REMOVED_VIDEO})
    before_head = await s.media.local(s.media.repo.put, "target_head", identity, head, 0)
    current = await s.media.local(s.media.repo.get, "subscription", source["subscription_id"])
    await s.media.local(s.media.repo.put, "subscription", source["subscription_id"], {
        **current, "quality_reassessment": True, "destination_reassessment": True,
    }, current["revision"])
    s.media.connector.resolve.side_effect = video_resolver()
    second = await s.command("media", "subscriptions/scan", {
        "subscription_id": source["subscription_id"], "recompute": True,
    })
    assert second.execution == "succeeded", second.error_code
    assert await s.media.local(s.media.repo.job, original["job_id"]) == original
    assert await s.media.local(s.media.repo.get, "target_head", identity) == before_head
    assert (await s.media.local(s.media.repo.member_parts, source["subscription_id"]))[REMOVED_VIDEO] == {"101"}
    assert local_file.read_bytes() == b"original local video"


async def test_music_settings_arm_timer_and_timer_enqueues_with_refreshed_authority(subscriptions):
    s = subscriptions
    await save_music(s)
    source = await s.media.local(s.media.repo.get, "music_library", "library")
    assert source["context"]["principal_id"] == s.context.principal_id
    due = await s.media.local(s.media.repo.maintenance_due, "music_imports")
    assert due == {"known": True, "due": 100}
    observation = await s.media.music.imports.pump()
    assert observation["schedule_observation"]["scan"]["submitted"] == 1
    source = await s.media.local(s.media.repo.get, "music_library", "library")
    receipt = await s.owner.actions.run(source["schedule"]["action_key"])
    assert receipt.execution == "succeeded", receipt.error_code
    job = await s.media.local(s.media.repo.job, receipt.result["job_id"])
    assert job["context"]["access_epoch"] == 2 and job["context"]["actor_id"] is None
    assert job["parent_action_id"] == receipt.action_id
    assert job["audio_operation"] == "scan"
    s.clock.now = 160
    await s.media.music.imports.pump()
    source = await s.media.local(s.media.repo.get, "music_library", "library")
    assert source["schedule"]["state"] == "completed"
    assert (await s.media.local(s.media.repo.maintenance_due, "music_imports"))["due"] == 3700
    async with s.database.transaction(read_only=True) as conn:
        native = await (
            await conn.execute(
                "SELECT queue_name FROM procrastinate_jobs WHERE task_name='capabilities.media_job'"
            )
        ).fetchall()
    assert native == [{"queue_name": "music_library"}]


async def test_manual_music_scan_accepts_console_envelope_and_keeps_old_results(subscriptions):
    s = subscriptions
    await save_music(s)
    first = await s.command("music", "library/scan")
    assert first.execution == "succeeded", first.error_code
    original = await s.media.local(s.media.repo.job, first.result["job_id"])
    await s.media.local(
        s.media.repo.update_job,
        original["job_id"],
        {"state": "unknown", "code": "original_unknown"},
    )
    second = await s.command("music", "library/scan")
    assert second.execution == "succeeded", second.error_code
    assert first.result["job_id"] != second.result["job_id"]
    assert (await s.media.local(s.media.repo.job, original["job_id"]))["code"] == "original_unknown"


async def test_music_timer_rejects_changed_settings_without_enqueuing_old_directory(subscriptions):
    s = subscriptions
    await save_music(s)
    await s.media.music.imports.pump()
    old = await s.media.local(s.media.repo.get, "music_library", "library")
    changed = await s.command(
        "music",
        "settings/save",
        {
            "directory": s.directory + "-new",
            "layout": "artist_album",
            "expected_revision": 1,
        },
    )
    assert changed.execution == "succeeded"
    receipt = await s.owner.actions.run(old["schedule"]["action_key"])
    assert receipt.execution == "failed" and receipt.error_code == "version_conflict"
    async with s.database.transaction(read_only=True) as conn:
        assert (
            await (await conn.execute("SELECT count(*) AS n FROM capability_media.jobs")).fetchone()
        )["n"] == 0


async def test_existing_directory_without_authored_schedule_reports_blocked(subscriptions):
    s = subscriptions
    await s.media.local(
        s.media.repo.put,
        "music_settings",
        "library",
        {
            "directory": s.directory,
            "layout": "artist_album",
            "filename": "artist_title",
        },
    )
    result = await s.media.music.imports.pump()
    assert result["schedule_observation"] == {
        "state": "blocked",
        "code": "subscription_context_required",
    }
    manual = await s.command("music", "library/scan")
    assert manual.execution == "succeeded", manual.error_code
    assert (await s.media.local(s.media.repo.get, "music_library", "library"))["context"]


@pytest.mark.parametrize(
    "evidence", ["confirmed", "unknown", "old_generation", "different_settings"]
)
async def test_upgrade_restores_only_exact_confirmed_current_configuration_author(
    subscriptions, evidence
):
    s = subscriptions
    original = await save_music(s)
    saved_action = await s.owner.actions.get(original.action_id, s.context)
    async with s.database.transaction() as conn:
        await conn.execute("DELETE FROM capability_media.documents WHERE kind='music_library'")
        if evidence == "unknown":
            await conn.execute(
                "UPDATE capabilities.actions SET receipt=jsonb_set(receipt,'{effect}','\"unknown\"') WHERE id=%s",
                (original.action_id,),
            )
        elif evidence == "old_generation":
            await conn.execute(
                "UPDATE capabilities.actions SET document="
                "jsonb_set(document::jsonb,'{data_generation}','\"retired-generation\"')::text WHERE id=%s",
                (original.action_id,),
            )
        elif evidence == "different_settings":
            await conn.execute(
                "UPDATE capability_media.documents SET body="
                "jsonb_set(body::jsonb,'{revision}','99')::text WHERE kind='music_settings'"
            )
    result = await s.media.music.imports.pump()
    source = await s.media.local(s.media.repo.get, "music_library", "library")
    if evidence == "confirmed":
        assert result["schedule_observation"]["scan"]["submitted"] == 1
        assert (
            source["context"]["principal_id"] == saved_action["action"]["context"]["principal_id"]
        )
        assert (await s.owner.actions.get(original.action_id, s.context))["action"] == saved_action[
            "action"
        ]
    else:
        assert source is None
        assert result["schedule_observation"]["state"] == "blocked"


async def seed_book(s):
    await s.media.local(
        s.media.repo.put,
        "book_settings",
        "library",
        {
            "directory": s.directory,
            "interval_seconds": 86400,
        },
    )
    book = await s.media.local(
        s.media.repo.put,
        "book",
        BOOK_ID,
        {
            "book_id": BOOK_ID,
            "source_id": "source",
            "source_type": "remote",
            "metadata": {"title": "测试书籍", "author": "作者"},
            "source_name": "测试来源",
        },
    )
    s.media.books.selection.prepare.return_value = (book, [])
    return book


async def test_book_first_download_timer_and_resume_use_normal_authorized_jobs(subscriptions):
    s = subscriptions
    await seed_book(s)
    saved = await s.command("books", "subscriptions/save", {"book_id": BOOK_ID})
    assert saved.execution == "succeeded", saved.error_code
    job = await s.media.local(s.media.repo.job, saved.result["job_id"])
    assert job["media_kind"] == "book" and job["context"]["principal_id"] == "author"
    paused = await s.command(
        "books",
        "subscriptions/control",
        {
            "book_id": BOOK_ID,
            "action": "pause",
            "expected_revision": 1,
        },
    )
    assert paused.execution == "succeeded", paused.error_code
    resume = await s.command(
        "books",
        "subscriptions/control",
        {
            "book_id": BOOK_ID,
            "action": "resume",
            "expected_revision": 2,
        },
    )
    assert resume.execution == "succeeded", resume.error_code
    assert resume.result["subscription"]["next_scan_at"] == s.clock.now
    observed = await scan_due(s.media, "book_subscription", "book_id", None)
    assert observed["schedule_observation"]["scan"]["submitted"] == 1
    source = await s.media.local(s.media.repo.get, "book_subscription", BOOK_ID)
    scanned = await s.owner.actions.run(source["schedule"]["action_key"])
    assert scanned.execution == "succeeded", scanned.error_code


async def test_paused_book_and_feed_do_not_access_sources(subscriptions):
    s = subscriptions
    await seed_book(s)
    for kind, field, identity in (
        ("book_subscription", "book_id", BOOK_ID),
        ("book_feed", "feed_id", "feed"),
    ):
        await s.media.local(
            s.media.repo.put,
            kind,
            identity,
            {
                field: identity,
                "state": "paused",
                "next_scan_at": 0,
                "interval_seconds": 3600,
                "context": wire(s.context),
                "source_refs": [],
            },
        )
    book = await s.command("books", "subscriptions/scan", {"book_id": BOOK_ID})
    feed = await s.command("books", "feeds/refresh", {"feed_id": "feed"})
    assert book.error_code == "book_subscription_paused" and feed.error_code == "book_feed_paused"
    s.media.books.selection.prepare.assert_not_awaited()
    s.media.books.network.fetch.assert_not_awaited()


async def test_book_unknown_is_preserved_and_known_retry_keeps_attempt_count(subscriptions):
    s = subscriptions
    book = await seed_book(s)
    saved = await s.command("books", "subscriptions/save", {"book_id": BOOK_ID})
    identity = saved.result["job_id"]
    await s.media.local(
        s.media.repo.update_job,
        identity,
        {"state": "unknown", "attempts": 3, "code": "original_unknown"},
    )

    def enqueue():
        with s.media.repo.transaction() as db:
            return s.media.books.catalog.enqueue(book, db=db)

    with pytest.raises(Fault, match="media_job_requires_reconciliation"):
        await s.media.local(enqueue)
    original = await s.media.local(s.media.repo.job, identity)
    assert original["state"] == "unknown" and original["attempts"] == 3
    await s.media.local(s.media.repo.update_job, identity, {"state": "failed"})
    retried = await s.media.local(enqueue)
    assert (
        retried["job_id"] == identity and retried["attempts"] == 3 and retried["state"] == "queued"
    )


async def test_music_lost_permission_is_saved_and_visible_without_running_work(subscriptions):
    s = subscriptions
    await save_music(s)
    s.owner.authority.refresh_scheduled_context.side_effect = AppError("forbidden", status=403)
    result = await s.media.music.imports.pump()
    assert result["schedule_observation"]["state"] == "blocked"
    status = await s.media.local(s.media.music.imports.view)
    assert status["schedule"]["state"] == "blocked"
    assert status["schedule"]["code"] == "forbidden"
    async with s.database.transaction(read_only=True) as conn:
        count = await (
            await conn.execute("SELECT count(*) AS n FROM capability_media.jobs")
        ).fetchone()
    assert count["n"] == 0


async def test_book_timer_stops_on_original_unknown_without_replaying_or_resetting_budget(
    subscriptions,
):
    s = subscriptions
    await seed_book(s)
    saved = await s.command("books", "subscriptions/save", {"book_id": BOOK_ID})
    await s.media.local(
        s.media.repo.update_job,
        saved.result["job_id"],
        {
            "state": "unknown",
            "code": "original_unknown",
            "attempts": 3,
        },
    )
    original = await s.media.local(s.media.repo.job, saved.result["job_id"])
    s.clock.now = 86500
    await scan_due(s.media, "book_subscription", "book_id", None)
    source = await s.media.local(s.media.repo.get, "book_subscription", BOOK_ID)
    receipt = await s.owner.actions.run(source["schedule"]["action_key"])
    assert receipt.error_code == "media_job_requires_reconciliation"
    s.clock.now += 60
    observed = await scan_due(s.media, "book_subscription", "book_id", None)
    assert observed["schedule_observation"]["state"] == "blocked"
    assert await s.media.local(s.media.repo.job, original["job_id"]) == original
