from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life.worker import LifeWorker
from tianshu.types import ArtifactRef, MediaRef, OriginKind, Receipt, SourceRef, TrustedContext


@pytest.mark.asyncio
async def test_requested_photo_retains_the_original_due_window_and_explicit_delivery():
    connection=object()
    @asynccontextmanager
    async def transaction():
        yield connection
    worker = LifeWorker(SimpleNamespace(database=SimpleNamespace(transaction=transaction), sources=None), None, None, None, None, None)
    worker.prepare_contact=AsyncMock(side_effect=lambda candidate:candidate)
    worker.persist_contact=AsyncMock()
    context = TrustedContext("author", "actor", "person", "conversation", ("scope",),
                             ("person",), 1, OriginKind.TIMER,
                             conversation_kind="private", delivery_allowed=True)
    ref = SourceRef("original", 3, {"start": 2, "end": 8})
    artifact = ArtifactRef("photo", MediaRef("artifact:photo", "controlled", "image/png"), (ref,))
    result = Receipt("receipt", "original-action", "original-attempt", "succeeded", "confirmed",
                     "not_requested", (artifact,), result={"photo_intent": {
                         "delivery_kind": "requested_delivery", "target_ref": "conversation",
                         "motivation": "seven oclock promise", "window": {
                             "due_at": "2026-10-31T07:00:00+08:00",
                             "expires_at": "2026-10-31T07:30:00+08:00"}}})
    await worker.detached_result(context, result, {"capability_id": "images.generate", "source_refs": []})
    worker.prepare_contact.assert_awaited_once()
    candidate = worker.prepare_contact.call_args.args[0]
    worker.persist_contact.assert_awaited_once_with(connection,candidate)
    assert candidate.explicit
    assert candidate.action_id == "original-action"
    assert candidate.due_at == datetime(2026, 10, 30, 23, tzinfo=UTC)
    assert candidate.expires_at == datetime(2026, 10, 30, 23, 30, tzinfo=UTC)
    assert candidate.source_refs == (ref,)
    assert candidate.payload["public_parts"][0]["media"]["media_id"] == "artifact:photo"


@pytest.mark.asyncio
async def test_unknown_photo_does_not_become_a_contact_or_new_generation():
    models=SimpleNamespace(generate=AsyncMock())
    worker = LifeWorker(SimpleNamespace(database=None, sources=None), models, None, None, None, None)
    worker.prepare_contact=AsyncMock()
    worker.persist_contact=AsyncMock()
    context = TrustedContext("author", "actor", None, "conversation", ("scope",),
                             ("conversation",), 1, OriginKind.TIMER, delivery_allowed=True)
    result = Receipt("receipt", "original-action", "original-attempt", "unknown", "unknown",
                     "not_requested", result={"photo_intent": {"delivery_kind": "requested_delivery"}})
    await worker.detached_result(context, result, {"capability_id": "images.generate"})
    worker.prepare_contact.assert_not_awaited()
    worker.persist_contact.assert_not_awaited()
    models.generate.assert_not_awaited()
