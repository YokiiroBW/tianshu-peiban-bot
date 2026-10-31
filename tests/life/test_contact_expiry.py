"""A person's contact window survives retries and is never renewed by policy edits."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.life.contacts import Contacts
from tianshu.life.service import LifeService
from tianshu.life.types import ContactCandidate
from tianshu.life.worker import LifeWorker
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import OriginKind, TrustedContext


class Authority:
    async def check(self, *_args):
        pass

    async def validate_persistence(self, *_args):
        return True


class Jobs:
    def __init__(self):
        self.items = []

    async def enqueue(self, _connection, job):
        self.items.append(job)


class NoExpression:
    async def send(self, *_args, **_kwargs):
        raise AssertionError("An expired candidate cannot be sent")


@pytest.mark.asyncio
async def test_person_ttl_caps_new_contacts_and_never_renews_old_or_retried_candidates(database):
    await migrate(database, (Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),))
    # Existing settings acquire the native four-hour default without rewriting their CAS.
    async with database.transaction() as connection:
        await connection.execute(
            "INSERT INTO life.contact_preferences(actor_id,person_id,scope_ids) VALUES('actor','',ARRAY['scope'])"
        )
    await migrate(database, (Migration("0033_life_contact_expiry", files("tianshu.life").joinpath("contact_expiry.sql").read_text()),))
    context = TrustedContext("author", "actor", "person", "conversation", ("scope",),
        ("person",), 1, OriginKind.ACTOR, permissions=frozenset({"life.manage"}),
        conversation_kind="private", delivery_allowed=True)
    authority, jobs = Authority(), Jobs()

    async def affect(_context):
        return {"valence": 0.8}

    contacts = Contacts(database)
    life = LifeService(database, CommandStore(database), jobs, authority,
        SimpleNamespace(affect=affect), contacts, sources=authority, memory=None)
    worker = LifeWorker(life, None, None, None, None, NoExpression())
    inherited = await contacts.preferences(context)
    assert inherited["expiry_seconds"] == 14400
    assert inherited["edit_revision"] is None

    async def save_expiry(seconds, revision):
        return await life.save_contact_preferences(context, {
            "client_request_id": f"expiry-{seconds}", "expected_revision": revision,
            "timezone": "UTC", "quiet_start": "00:00", "quiet_end": "00:00",
            "cooldown_seconds": 0, "daily_quota": 3, "unanswered_limit": 3,
            "enabled": True, "expiry_seconds": seconds,
        })

    await save_expiry(3600, None)
    assert (await contacts.preferences(context))["expiry_seconds"] == 3600
    assert (await contacts.preferences(replace(context, person_id="other")))["expiry_seconds"] == 14400
    now = datetime.now(UTC)
    candidate = ContactCandidate("first", "actor", "conversation", "creation", "一次真实完成后的分享",
        now, now + timedelta(hours=8), context)
    await worker.propose_contact(candidate)

    async def read(identifier):
        async with database.transaction(read_only=True) as connection:
            return await (await connection.execute(
                "SELECT * FROM life.contact_candidates WHERE id=%s", (identifier,)
            )).fetchone()

    first = await read("first")
    assert first["expires_at"] <= first["created_at"] + timedelta(hours=1)
    assert first["expires_at"] > first["created_at"] + timedelta(minutes=59)
    # Simulate a delayed ordinary candidate; changing due_at does not change its birth/window.
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE life.contact_candidates SET created_at=created_at-interval '2 hours',"
            "expires_at=expires_at-interval '2 hours',due_at=now()-interval '1 minute' WHERE id='first'"
        )
    expired = await read("first")
    await save_expiry(14400, 1)
    queued = len(jobs.items)
    await worker.propose_contact(candidate)
    assert len(jobs.items) == queued
    assert (await read("first"))["expires_at"] == expired["expires_at"]
    await worker.contact_due("first", 1)
    assert (await read("first"))["state"] == "cancel"
    assert (await read("first"))["reason"] == "window_expired"

    # A shorter current person policy is also consumed at review, before original expiry.
    second = replace(candidate, candidate_id="second")
    await worker.propose_contact(second)
    async with database.transaction() as connection:
        await connection.execute(
            "UPDATE life.contact_candidates SET created_at=now()-interval '2 minutes' WHERE id='second'"
        )
    assert (await read("second"))["expires_at"] > datetime.now(UTC)
    await save_expiry(60, 2)
    await worker.contact_due("second", 1)
    reviewed = await read("second")
    assert reviewed["state"] == "cancel"
    assert reviewed["expires_at"] <= datetime.now(UTC)
    # Explicit promises keep their own original window rather than the ordinary-share TTL.
    explicit = replace(candidate, candidate_id="explicit", explicit=True,
                       delivery_kind="requested_delivery")
    assert (await worker.prepare_contact(explicit)).candidate.expires_at == explicit.expires_at
