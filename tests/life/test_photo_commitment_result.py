from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tianshu.life.worker import LifeWorker
from tianshu.types import ArtifactRef, MediaRef, OriginKind, Receipt, SourceRef, TrustedContext


@pytest.mark.asyncio
async def test_requested_photo_retains_the_original_due_window_and_explicit_delivery():
    worker = LifeWorker(SimpleNamespace(database=None, sources=None), None, None, None, None, None)
    prepared = []

    async def propose(candidate):
        prepared.append(candidate)

    worker.propose_contact = propose
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
    assert len(prepared) == 1
    candidate = prepared[0]
    assert candidate.explicit
    assert candidate.action_id == "original-action"
    assert candidate.due_at == datetime(2026, 10, 30, 23, tzinfo=UTC)
    assert candidate.expires_at == datetime(2026, 10, 30, 23, 30, tzinfo=UTC)
    assert candidate.source_refs == (ref,)
    assert candidate.payload["public_parts"][0]["media"]["media_id"] == "artifact:photo"


@pytest.mark.asyncio
async def test_unknown_photo_does_not_become_a_contact_or_new_generation():
    worker = LifeWorker(SimpleNamespace(database=None, sources=None), None, None, None, None, None)
    async def forbidden(_candidate):
        raise AssertionError("unknown generation cannot be shared")
    worker.propose_contact = forbidden
    context = TrustedContext("author", "actor", None, "conversation", ("scope",),
                             ("conversation",), 1, OriginKind.TIMER, delivery_allowed=True)
    result = Receipt("receipt", "original-action", "original-attempt", "unknown", "unknown",
                     "not_requested", result={"photo_intent": {"delivery_kind": "requested_delivery"}})
    await worker.detached_result(context, result, {"capability_id": "images.generate"})
