from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.content.recovery import MAX_ATTEMPTS, identity, reserve_on
from tianshu.capabilities.content.service import Content
from tianshu.capabilities.contracts import Fault, digest
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.types import Action, Attempt, OriginKind, TrustedContext


def context():
    return TrustedContext("owner", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR)


def action():
    return Action("action", "intent", "content.read", "actor", context(),
                  {"url": "https://example.org/article", "render": "browser"}, OriginKind.ACTOR)


def policy(attempts=0):
    return SimpleNamespace(
        register_on=AsyncMock(return_value={"lineage_id": "lineage", "attempts": attempts}),
        reserve_attempt_on=AsyncMock(side_effect=lambda _conn, _lineage, **kw:
            SimpleNamespace(attempt_id=kw["attempt_id"], status="reserved", probe_token=None, number=attempts + 1)),
        fail_attempt_on=AsyncMock(return_value=datetime(2026, 11, 5, tzinfo=UTC)),
        succeed_attempt_on=AsyncMock(),
        claim_lineage_due_on=AsyncMock(return_value={"claim_token": "token"}),
        inspect_wait_on=AsyncMock(return_value=None),
    )


def connection():
    async def execute(query, _parameters):
        if query.startswith("SELECT attempt,receipt,execution_owner"):
            value = {"attempt": {"attempt_id": "old"}, "receipt": {"execution": "running"},
                     "execution_owner": "worker", "owner_live": True}
        else:
            value = None
        return SimpleNamespace(fetchone=AsyncMock(return_value=value))
    return SimpleNamespace(execute=execute)


@pytest.mark.asyncio
async def test_failed_browser_read_records_phase_and_original_material_budget():
    conn, shared = connection(), policy()
    @asynccontextmanager
    async def transaction(**_kwargs):
        yield conn
    sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    browser = SimpleNamespace(read=AsyncMock(side_effect=Fault("browser_source_unavailable", 503)))
    content = Content(SimpleNamespace(database=SimpleNamespace(transaction=transaction)), sources,
                      SimpleNamespace(check=AsyncMock()), browser=browser)
    content.recovery_policy = shared
    execution = {"owner": SimpleNamespace(owners=SimpleNamespace(id="worker")),
                 "data_generation": "generation", "submitted": False}
    token = CURRENT_EXECUTION.set(execution)
    try:
        receipt = await content.execute(action(), Attempt("old", "action"))
    finally:
        CURRENT_EXECUTION.reset(token)
    assert receipt.execution == "failed" and receipt.effect == "none"
    assert receipt.result["phase"] == "acquisition"
    assert receipt.result["recovery"] == "resume_material_read"
    assert receipt.result["attempt_number"] == 1 and receipt.result["max_attempts"] == 5
    assert receipt.result["retry_at"].endswith("+00:00")
    assert receipt.result["material_read"]["method"] == "browser"
    assert shared.register_on.await_args.kwargs["max_attempts"] == MAX_ATTEMPTS
    assert shared.fail_attempt_on.await_count == 1
    assert await content.resume_preflight(action(), Attempt("old", "action"), receipt) == receipt.result["material_read"]


@pytest.mark.asyncio
async def test_resume_claims_same_material_lineage_and_spends_same_budget():
    conn, shared = connection(), policy(attempts=1)
    content = SimpleNamespace(recovery_policy=shared)
    before = identity(action(), Attempt("old", "action"), "browser", "https://example.org/article")
    after = identity(action(), Attempt("new", "action"), "browser", "https://example.org/article")
    fallback = identity(action(), Attempt("new", "action"), "http", "https://example.org/redirected")
    assert before["material_id"] == after["material_id"] and before["version"] == after["version"]
    assert before["material_id"] == fallback["material_id"] and before["version"] == fallback["version"]
    assert before["attempt_id"] != after["attempt_id"]
    await reserve_on(content, conn, action(), Attempt("new", "action"), "browser",
                     "https://example.org/article", generation="generation")
    assert shared.register_on.await_args.kwargs["material_id"] == before["material_id"]
    assert shared.reserve_attempt_on.await_args.kwargs["claim_token"] == "token"
    assert shared.reserve_attempt_on.await_args.kwargs["original_state"] == "failed"


@pytest.mark.asyncio
async def test_unknown_effect_and_failed_commit_cannot_resume_material():
    content = Content(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(check=AsyncMock()))
    attempt = Attempt("old", "action")
    from tianshu.types import Receipt
    for receipt in (
        Receipt("r1", "action", "old", "unknown", "unknown", "not_requested",
                result={"phase": "acquisition", "recovery": "observe_original_attempt"}),
        Receipt("r2", "action", "old", "failed", "none", "not_requested",
                result={"phase": "commit", "recovery": "none"}),
    ):
        with pytest.raises(Fault) as failure:
            await content.resume_preflight(action(), attempt, receipt)
        assert failure.value.code == "read_recovery_not_eligible"


@pytest.mark.asyncio
async def test_confirmed_commit_resume_uses_checkpoint_without_another_read(monkeypatch):
    from tianshu.capabilities import action_checkpoint
    from tianshu.types import Receipt
    saved = {"raw_base64": "Ym9keQ==", "mime_type": "text/plain",
             "content": {"text": "body"}, "locator": {"url": "https://example.org/article"}, "after": 0}
    monkeypatch.setattr(action_checkpoint, "load", AsyncMock(return_value=saved))
    content = Content(SimpleNamespace(database=object()), SimpleNamespace(), SimpleNamespace(check=AsyncMock()))
    content.fetch = AsyncMock()
    content._commit_read = AsyncMock(return_value=Receipt("done", "action", "new", "succeeded", "none", "not_requested"))
    token = CURRENT_EXECUTION.set({"data_generation": "generation"})
    try:
        receipt = await content._execute_with_phase(action(), Attempt("new", "action"),
            resume={"phase": "commit", "checkpoint_digest": digest(saved)})
    finally:
        CURRENT_EXECUTION.reset(token)
    assert receipt.execution == "succeeded"
    content.fetch.assert_not_awaited()
    content._commit_read.assert_awaited_once_with(action(), Attempt("new", "action"), saved)


@pytest.mark.asyncio
async def test_reconcile_completed_checkpoint_without_capturing_or_reading_again(monkeypatch):
    from tianshu.capabilities import action_checkpoint
    from tianshu.types import Receipt
    saved = {"original_source": {"source_id": "original", "revision": 1},
             "normalized_source": {"source_id": "normalized", "revision": 1},
             "content": {"text": "body"}, "locator": {"url": "https://example.org/article"}, "after": 0}
    monkeypatch.setattr(action_checkpoint, "load", AsyncMock(return_value=saved))
    sources = SimpleNamespace(capture=AsyncMock())
    content = Content(SimpleNamespace(database=object()), sources, SimpleNamespace(check=AsyncMock()))
    content.fetch = AsyncMock()
    done = Receipt("receipt", "action", "old", "succeeded", "none", "not_requested")
    content._read_receipt = lambda *_args: done
    assert await content.reconcile(action(), Attempt("old", "action")) is done
    content.fetch.assert_not_awaited()
    sources.capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsafe_remote_wait_is_propagated_without_a_new_read():
    from tianshu.runtime.recovery_policy import RecoveryWait
    wait = RecoveryWait("remote_wait", datetime.now(UTC) + timedelta(seconds=30))
    wait.remote_rollback_confirmed = False
    content = Content(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(check=AsyncMock()))
    content._execute_read = AsyncMock(side_effect=wait)
    with pytest.raises(RecoveryWait) as caught:
        await content.execute(action(), Attempt("old", "action"))
    assert caught.value is wait
