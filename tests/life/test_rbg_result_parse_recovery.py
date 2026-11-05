"""Video parse recovery only uses the classified original Action lineage."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.life.worker import LifeWorker
from tianshu.runtime.errors import AppError
from tianshu.types import Receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_video_parse_requires_matching_read_only_classification(historical):
    payload = {"phase": "parse", "outcome": "failed", "recovery": "invalid_model_output" if historical else "resume_parse",
        "parser_version": 2, "model_attempt_id": "successful-model-attempt", "segment": "main", "source_refs": []}
    original = Receipt("receipt", "action", "attempt", "failed", "none", "not_requested",
                       result=payload, error_code="invalid_model_output" if historical else "parse_failed")
    facts = {"eligible": True, "operation": "resume_parse", "mode": "local_parse", "result_revision": 4,
             "action_id": "action", "attempt_id": "attempt", "receipt_id": "receipt"}
    actions = SimpleNamespace(inspect_video_parse_recovery=AsyncMock(return_value=facts),
                              invoke=AsyncMock(), resume=AsyncMock())
    worker = LifeWorker.__new__(LifeWorker)
    worker.actions = actions
    context = object()
    assert await worker._capability_recovery(context, original, 4, "video.observe") == "resume_parse"
    actions.inspect_video_parse_recovery.assert_awaited_once_with("action", context, expected_attempt_id="attempt")
    actions.invoke.assert_not_awaited()
    actions.resume.assert_not_awaited()
    facts["receipt_id"] = "different"
    with pytest.raises(AppError, match="video_parse_recovery_changed"):
        await worker._capability_recovery(context, original, 4, "video.observe")


@pytest.mark.asyncio
async def test_legacy_invalid_output_is_not_resumed_when_classifier_rejects_it():
    original = Receipt("receipt", "action", "attempt", "failed", "none", "not_requested",
                       error_code="invalid_model_output")
    actions = SimpleNamespace(inspect_video_parse_recovery=AsyncMock(return_value={"eligible": False}),
                              invoke=AsyncMock(), resume=AsyncMock())
    worker = LifeWorker.__new__(LifeWorker)
    worker.actions = actions
    assert await worker._capability_recovery(object(), original, 1, "video.observe") is None
    actions.resume.assert_not_awaited()
