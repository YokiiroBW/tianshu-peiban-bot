import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tianshu.models.catalog import QuotaPolicy
from tianshu.models.scheduler import QuotaScheduler
from tianshu.types import OriginKind, RequestBudget, ServiceClass, TrustedContext


def request(identity, kind, *, wait=2, conversation="c"):
    return SimpleNamespace(attempt_id=identity, service_class=kind, max_queue_wait=wait,
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=3)),
        context=TrustedContext("principal", "actor", None, conversation, (), (), 1, OriginKind.HUMAN))


async def test_background_cannot_borrow_foreground_reserve():
    scheduler = QuotaScheduler({"account": QuotaPolicy("account", 2, 1, 1)})
    await scheduler.start()
    pending = None
    try:
        await scheduler.acquire(request("background-1", ServiceClass.ELASTIC), "account", {"tokens": 10})
        pending = asyncio.create_task(scheduler.acquire(
            request("background-2", ServiceClass.ELASTIC), "account", {"tokens": 10}))
        await asyncio.sleep(0)
        await scheduler.acquire(request("foreground", ServiceClass.INTERACTIVE), "account", {"tokens": 10})
        assert not pending.done()
        assert scheduler.snapshot()["account"]["active"] == 2
    finally:
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await scheduler.close()


async def test_two_model_aliases_share_one_rate_budget():
    scheduler = QuotaScheduler({"account": QuotaPolicy("account", 3, 1, 1, tpm=100)})
    await scheduler.start()
    try:
        await scheduler.acquire(request("alias-a", ServiceClass.INTERACTIVE), "account", {"tokens": 80})
        await scheduler.release("account", "alias-a", units={"tokens": 80})
        with pytest.raises(TimeoutError):
            await scheduler.acquire(request("alias-b", ServiceClass.INTERACTIVE, wait=0.05),
                                    "account", {"tokens": 30})
    finally:
        await scheduler.close()


async def test_restart_retains_unknown_upstream_capacity():
    scheduler = QuotaScheduler({"account": QuotaPolicy("account", 1, 0, 1)})
    scheduler.restore([{"quota_group": "account", "attempt_id": "original",
        "service_class": "elastic", "started_at": datetime.now(UTC), "charged_units": {},
        "estimated_units": {}, "status": "unknown", "hold_until": datetime.now(UTC)+timedelta(minutes=1)}])
    await scheduler.start()
    try:
        with pytest.raises(TimeoutError):
            await scheduler.acquire(request("new", ServiceClass.INTERACTIVE, wait=0.05), "account", {})
        assert scheduler.snapshot()["account"]["unknown_holds"] == 1
    finally:
        await scheduler.close()
