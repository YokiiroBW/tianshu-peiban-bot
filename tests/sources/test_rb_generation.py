"""Source writes obey the generation and transaction of their original work."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest

from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind


_spec = spec_from_file_location(
    "rb_generation_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


async def _generation(core):
    async with core.database.transaction(read_only=True) as conn:
        return (await (await conn.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton"
        )).fetchone())["id"]


async def _record(core, context, event_id, *, connection=None, generation=None):
    return (await core.sources.record_event(
        context, producer="web", event_id=event_id, revision=1,
        occurred_at=datetime.now(UTC), parts=(InputPart("text", text="original"),),
        origin_kind=OriginKind.HUMAN, connection=connection, generation=generation,
    ))[0]


async def _change_generation_during_file_verification(core, monkeypatch, operation_factory):
    entered, release = Event(), Event()
    actual_reader = core.files.open_reader

    @contextmanager
    def delayed_reader(reference):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("file verification was not released")
        with actual_reader(reference) as stream:
            yield stream

    monkeypatch.setattr(core.files, "open_reader", delayed_reader)
    await _change_generation_while_pending(core, entered, release, operation_factory)


async def _change_generation_while_pending(core, entered, release, operation_factory):
    before = await _generation(core)
    operation = asyncio.create_task(operation_factory(str(before)))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        replacement = uuid4() if isinstance(before, UUID) else str(uuid4())
        async with core.database.transaction() as conn:
            await conn.execute(
                "UPDATE runtime.installation_generation SET id=%s WHERE singleton", (replacement,)
            )
    finally:
        release.set()
    with pytest.raises(AppError, match="job_generation_expired"):
        await operation


class RollbackMarker(Exception):
    pass


async def test_event_rejects_stale_generation_and_joins_caller_transaction(core, ordinary_users):
    alice, _ = ordinary_users
    current = str(await _generation(core))
    with pytest.raises(AppError, match="job_generation_expired"):
        await _record(core, alice, "stale-event", generation=str(uuid4()))
    async with core.database.transaction(read_only=True) as conn:
        stale = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'='stale-event'"
        )).fetchone()
    assert stale is None

    with pytest.raises(RollbackMarker):
        async with core.database.transaction() as conn:
            reference = await _record(core, alice, "rolled-back-event", connection=conn,
                                      generation=current)
            assert await core.sources.validate_persistence(conn, alice, (reference,))
            raise RollbackMarker()
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'='rolled-back-event'"
        )).fetchone()
    assert row is None


async def test_supplement_rejects_stale_generation_and_joins_caller_transaction(core, ordinary_users):
    alice, _ = ordinary_users
    current = str(await _generation(core))
    original = await _record(core, alice, "supplemented-event", generation=current)
    media = core.files.put_bytes(b"attachment", media_type="image/png")
    args = dict(original_ref=original, producer="web", event_id="supplemented-event",
                event_revision=1, part_index=0, supplement_revision=1)
    with pytest.raises(AppError, match="job_generation_expired"):
        await core.sources.register_media_supplement(alice, media, **args, generation=str(uuid4()))
    with pytest.raises(RollbackMarker):
        async with core.database.transaction() as conn:
            supplement = await core.sources.register_media_supplement(
                alice, media, **args, connection=conn, generation=current,
            )
            assert await core.sources.validate_media_supplement_on(
                conn, alice, supplement, original_ref=original, producer="web",
                event_id="supplemented-event", event_revision=1, part_index=0,
            )
            raise RollbackMarker()
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT 1 FROM sources.media_supplements")).fetchone()
    assert row is None
    assert (await core.sources.check_many(alice, ((original,),))) == (True,)


async def test_new_supplement_snapshots_generation_before_file_verification(
    core, ordinary_users, monkeypatch
):
    alice, _ = ordinary_users
    original = await _record(core, alice, "generation-race")
    media = core.files.put_bytes(b"attachment", media_type="image/png")
    await _change_generation_during_file_verification(
        core, monkeypatch,
        lambda _generation: core.sources.register_media_supplement(
            alice, media, original_ref=original, producer="web", event_id="generation-race",
            event_revision=1, part_index=0, supplement_revision=1,
        ),
    )
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT 1 FROM sources.media_supplements")).fetchone()
    assert row is None


async def test_initial_media_rejects_old_generation_without_creating_a_source(core, ordinary_users):
    alice, _ = ordinary_users
    current = str(await _generation(core))
    media = core.files.put_bytes(b"initial attachment", media_type="image/png")
    locator = {"kind": "initial_media", "event_id": "stale-initial-media"}
    with pytest.raises(AppError, match="job_generation_expired"):
        await core.sources.register_media(alice, media, locator=locator, generation=str(uuid4()))
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'=%s", (locator["event_id"],)
        )).fetchone()
    assert row is None

    reference = await core.sources.register_media(alice, media, locator=locator, generation=current)
    assert (await core.sources.check_many(alice, ((reference,),))) == (True,)


@pytest.mark.parametrize("explicit_generation", [True, False])
async def test_initial_media_does_not_cross_generation_during_file_verification(
    core, ordinary_users, monkeypatch, explicit_generation
):
    alice, _ = ordinary_users
    media = core.files.put_bytes(b"initial attachment", media_type="image/png")
    locator = {"kind": "initial_media", "event_id": "initial-media-race"}
    await _change_generation_during_file_verification(
        core, monkeypatch,
        lambda generation: core.sources.register_media(
            alice, media, locator=locator,
            generation=generation if explicit_generation else None,
        ),
    )
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'=%s", (locator["event_id"],)
        )).fetchone()
    assert row is None


async def test_capture_and_publish_reject_stale_generation(core):
    context = core.owner
    current = str(await _generation(core))
    media = core.files.put_bytes(b"published attachment", media_type="image/png")
    capture_locator = {"kind": "capture", "event_id": "stale-capture"}
    publish_locator = {"kind": "publication", "event_id": "stale-publication"}
    with pytest.raises(AppError, match="job_generation_expired"):
        await core.sources.capture(
            context, locator=capture_locator, raw=b"captured", mime_type="text/plain",
            generation=str(uuid4()),
        )
    with pytest.raises(AppError, match="job_generation_expired"):
        await core.sources.publish_media(
            context, media, locator=publish_locator, target="current", generation=str(uuid4()),
        )
    async with core.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT source_id FROM sources.versions WHERE locator->>'event_id'=ANY(%s)",
            ([capture_locator["event_id"], publish_locator["event_id"]],),
        )).fetchall()
    assert not rows
    captured = await core.sources.capture(
        context, locator=capture_locator, raw=b"captured", mime_type="text/plain", generation=current,
    )
    published = await core.sources.publish_media(
        context, media, locator=publish_locator, target="current", generation=current,
    )
    assert await core.sources.check_many(context, ((captured,), (published,))) == (True, True)


@pytest.mark.parametrize("explicit_generation", [True, False])
async def test_capture_freezes_generation_before_storing_file(core, monkeypatch, explicit_generation):
    context = core.owner
    entered, release = Event(), Event()
    actual_put = core.files.put_bytes
    locator = {"kind": "capture", "event_id": "capture-race"}

    def delayed_put(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("file storage was not released")
        return actual_put(*args, **kwargs)

    monkeypatch.setattr(core.files, "put_bytes", delayed_put)
    await _change_generation_while_pending(
        core, entered, release,
        lambda generation: core.sources.capture(
            context, locator=locator, raw=b"captured", mime_type="text/plain",
            generation=generation if explicit_generation else None,
        ),
    )
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'=%s", (locator["event_id"],)
        )).fetchone()
    assert row is None


@pytest.mark.parametrize("explicit_generation", [True, False])
async def test_publish_freezes_generation_before_file_verification(core, monkeypatch, explicit_generation):
    context = core.owner
    media = core.files.put_bytes(b"published attachment", media_type="image/png")
    locator = {"kind": "publication", "event_id": "publish-race"}
    await _change_generation_during_file_verification(
        core, monkeypatch,
        lambda generation: core.sources.publish_media(
            context, media, locator=locator, target="current",
            generation=generation if explicit_generation else None,
        ),
    )
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT 1 FROM sources.versions WHERE locator->>'event_id'=%s", (locator["event_id"],)
        )).fetchone()
    assert row is None
