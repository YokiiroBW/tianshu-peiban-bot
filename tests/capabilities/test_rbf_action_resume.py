from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Availability, CapabilityDefinition, Catalog
from tianshu.capabilities.contracts import Fault
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Action, OriginKind, Receipt, TrustedContext


@pytest.mark.asyncio
async def test_terminal_resume_checks_original_receipt_and_keeps_control_idempotent(database):
    await migrate(database, (Migration("0007_rb_runtime",
        files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("capabilities-0001", files("tianshu.capabilities").joinpath("schema.sql").read_text())))
    async def fail(action, attempt):
        return Receipt(str(uuid4()), action.action_id, attempt.attempt_id,
                       "failed", "none", "not_requested", error_code="read_failed")
    handler = SimpleNamespace(definition=CapabilityDefinition("example.read", "read", "", {"type": "object"},
        {"type": "object"}), availability=AsyncMock(return_value=Availability(True, True)), execute=fail,
        resume=AsyncMock(), resume_preflight=AsyncMock(return_value={}), validate_resume_on=AsyncMock())
    catalog = Catalog()
    catalog.register(handler)
    jobs = JobQueue(database.config.dsn)
    actions = Actions(database, jobs, catalog, SimpleNamespace(check=AsyncMock()))
    actions.sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    actions.register_tasks()
    ctx = TrustedContext("owner", "actor", None, "scene", ("scope",), ("scope",), 1,
                         OriginKind.ACTOR, frozenset({"*"}))
    action = Action("original", "intent", "example.read", "actor", ctx, {}, OriginKind.ACTOR)
    try:
        failed = await actions.invoke(action)
        snapshot = await actions.get(action.action_id, ctx)
        with pytest.raises(Fault) as stale:
            await actions.resume(action.action_id, ctx, client_request_id="stale",
                                 expected_attempt_id="other", expected_receipt_id=failed.receipt_id,
                                 expected_revision=snapshot["result_revision"])
        assert stale.value.code == "attempt_changed"
        guard = AsyncMock(return_value=True)
        options = {"client_request_id": "recover", "expected_attempt_id": failed.attempt_id,
                   "expected_receipt_id": failed.receipt_id, "expected_revision": snapshot["result_revision"]}
        pending = await actions.resume(action.action_id, ctx, **options,
                                       recovery_guard=(guard, "recovery-attempt", "owner-token"))
        assert pending.execution == "pending" and pending.attempt_id != failed.attempt_id
        guard.assert_awaited_once()
        replay = await actions.resume(action.action_id, ctx, **options)
        assert replay.attempt_id == pending.attempt_id
        async with database.transaction(read_only=True) as conn:
            old = await (await conn.execute("SELECT receipt FROM capabilities.attempts WHERE id=%s",
                                            (failed.attempt_id,))).fetchone()
        assert old["receipt"]["receipt_id"] == failed.receipt_id
    finally:
        await actions.close()
        await catalog.close()
