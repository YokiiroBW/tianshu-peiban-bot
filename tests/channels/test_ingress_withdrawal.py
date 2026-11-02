"""Recall may settle ingress while its original source admission is in flight."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.channels.ingress import process_event
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


def ingress(*, withdraw=False, error_code="source_media_not_authorized"):
    context = TrustedContext("principal", "actor", "person", "group", ("scope",), ("scope",),
        1, OriginKind.HUMAN, frozenset({"conversation.read"}), conversation_kind="group")
    raw = {"time": 1793440000, "self_id": 12345, "post_type": "message", "message_type": "group",
        "message_id": 8, "user_id": 77, "group_id": 22, "sender": {"user_id": 77},
        "message": [{"type": "text", "data": {"text": "带有已接收媒体的消息"}}]}
    row = {"id": "event", "connection_id": "qq", "state": "pending", "payload": "sealed",
        "event_id": "message:group:8", "created_at": datetime.now(UTC), "superseded": False,
        "outcome": {"media_checkpoint": {"parts": [{"kind": "text", "text": "original media checkpoint"}],
            "source_refs": [{"source_id": "media-original", "revision": 1}]}}}
    updates, accepted = [], []

    async def execute(sql, _parameters):
        if sql.startswith("SELECT e.*"):
            result = dict(row)  # In-flight ingress holds the original snapshot.
        elif sql.startswith("SELECT state"):
            result = {"state": row["state"]}
        else:
            updates.append(sql)
            result = None
        return SimpleNamespace(fetchone=AsyncMock(return_value=result))

    connection = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    @asynccontextmanager
    async def transaction(**_kwargs):
        yield connection

    async def ingest(context, message):
        if withdraw:
            # Source.guard rejects the original media after channel_recall has
            # already withdrawn the event and before collector.accept can run.
            row.update(state="withdrawn", payload=None, outcome=None)
        if error_code:
            raise AppError(error_code, status=403)
        accepted.append(message)
        return {}

    service = SimpleNamespace(
        runtime=SimpleNamespace(database=SimpleNamespace(transaction=transaction)),
        repository=SimpleNamespace(connection=AsyncMock(return_value={"id": "qq", "namespace": "qq",
            "adapter": "onebot", "self_external_id": "12345", "enabled": True}),
            unseal=lambda *_args: {"raw": raw}, bind_event_sources=AsyncMock()),
        identity=SimpleNamespace(admit_event=AsyncMock(return_value=SimpleNamespace(context=context,
            disposition="observe", duplicate=False, reply_owner="tianshu", mentioned_person_ids=(),
            original_author_person_id=None, reply_to_event_id=None))),
        sources=SimpleNamespace(validate_persistence=AsyncMock(return_value=True)),
        conversation=SimpleNamespace(ingest=AsyncMock(side_effect=ingest)),
        actions=SimpleNamespace(invoke=AsyncMock(), find=AsyncMock()),
    )
    return service, row, updates, accepted


@pytest.mark.asyncio
async def test_recalled_media_source_rejection_finishes_only_the_already_withdrawn_ingress():
    service, row, updates, accepted = ingress(withdraw=True)
    await process_event(service, "event")
    assert row["state"] == "withdrawn" and row["payload"] is row["outcome"] is None
    assert accepted == [] and updates == []
    service.repository.bind_event_sources.assert_not_awaited()
    service.actions.invoke.assert_not_awaited()
    # A later duplicate job cannot re-admit, recapture, replay a read or open a turn.
    await process_event(service, "event")
    assert service.conversation.ingest.await_count == service.identity.admit_event.await_count == 1


@pytest.mark.asyncio
async def test_same_media_authorization_failure_for_a_live_event_is_still_an_error():
    service, row, updates, accepted = ingress()
    with pytest.raises(AppError, match="source_media_not_authorized"):
        await process_event(service, "event")
    assert row["state"] == "pending" and row["payload"] == "sealed"
    assert updates == [] and accepted == []


@pytest.mark.asyncio
async def test_unrelated_failure_is_not_hidden_even_after_withdrawal():
    service, row, _updates, accepted = ingress(withdraw=True, error_code="conversation_busy")
    with pytest.raises(AppError, match="conversation_busy"):
        await process_event(service, "event")
    assert row["state"] == "withdrawn" and accepted == []


@pytest.mark.asyncio
async def test_withdrawal_observed_before_ingest_does_not_enter_conversation():
    service, row, updates, accepted = ingress(error_code=None)
    async def admit(*_args):
        row.update(state="withdrawn", payload=None, outcome=None)
        return service.identity.admit_event.return_value
    service.identity.admit_event.side_effect = admit
    await process_event(service, "event")
    service.conversation.ingest.assert_not_awaited()
    assert row["payload"] is row["outcome"] is None and updates == [] and accepted == []
