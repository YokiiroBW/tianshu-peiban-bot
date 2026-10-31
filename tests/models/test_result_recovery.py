"""A recovered result must never create another paid attempt or release revoked content."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tianshu.models.ledger import context_fingerprint
from tianshu.models.results import read_result
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, TrustedContext


async def test_result_recovery_checks_original_scene_and_current_sources():
    context = TrustedContext("alice", "actor", "person", "private-a", ("scope",),
                             ("scope",), 1, OriginKind.HUMAN)
    row = {"principal_id": "alice", "context_fingerprint": context_fingerprint(context),
           "source_refs": [{"source_id": "source", "revision": 1, "locator": {}}],
           "result": {"parts": [{"kind": "text", "text": "private result"}],
                      "native_response": {"private": "result"}},
           "attempt_id": "original", "intent_id": "intent", "status": "succeeded",
           "dispatch": "submitted", "route_id": "route", "route_revision": 1,
           "provider_id": "provider", "model": "model", "protocol": "openai.chat",
           "quota_group": "account", "provider_handle": "response", "queued_at": None,
           "started_at": None, "finished_at": None, "first_output_ms": None, "error_code": None}
    revoked = False
    checks = []

    async def get(attempt_id):
        assert attempt_id == "original"
        return row

    async def recheck(request):
        checks.append(request.source_refs)
        if revoked and request.source_refs:
            raise AppError("source_unavailable", status=403)

    broker = SimpleNamespace(ledger=SimpleNamespace(get=get), recheck=recheck)
    recovered = await read_result(broker, "original", context=context)
    assert recovered.parts[0].text == "private result"
    with pytest.raises(AppError, match="model_result_context_forbidden"):
        await read_result(broker, "original", context=replace(context, conversation_id="private-b"))
    revoked = True
    recovered = await read_result(broker, "original", context=context)
    assert recovered.receipt.attempt_id == "original" and recovered.receipt.status == "succeeded"
    assert recovered.parts == () and recovered.native_response is None
    row["status"] = "unknown"
    assert (await read_result(broker, "original", context=context)).parts == ()
    assert len(checks) == 6
