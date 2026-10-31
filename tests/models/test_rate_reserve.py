import asyncio

import pytest

from tianshu.models.catalog import QuotaPolicy
from tianshu.models.scheduler import QuotaScheduler
from tianshu.types import ServiceClass
from test_quota_scheduler import request


async def test_background_cannot_consume_foreground_token_window():
    policy = QuotaPolicy("account", 4, 1, 2, tpm=100, interactive_unit_reserve={"tokens": 40})
    scheduler = QuotaScheduler({"account": policy})
    await scheduler.start()
    try:
        await scheduler.acquire(
            request("background", ServiceClass.ELASTIC), "account", {"tokens": 60}
        )
        await scheduler.release("account", "background", units={"tokens": 60})
        with pytest.raises(TimeoutError):
            await scheduler.acquire(
                request("background-more", ServiceClass.ELASTIC, wait=0.05),
                "account",
                {"tokens": 1},
            )
        await scheduler.acquire(
            request("foreground", ServiceClass.INTERACTIVE), "account", {"tokens": 40}
        )
    finally:
        await scheduler.close()


async def test_batched_http_requests_use_actual_rpm_and_preserve_foreground_share():
    policy = QuotaPolicy("account", 4, 1, 2, rpm=3, interactive_rpm_reserve=1)
    scheduler = QuotaScheduler({"account": policy})
    await scheduler.start()
    try:
        await scheduler.acquire(
            request("archive-batch", ServiceClass.ELASTIC), "account", {"requests": 2}
        )
        await scheduler.submitted("account", "archive-batch")
        await scheduler.release("account", "archive-batch", units={"tokens": 0})
        assert scheduler.snapshot()["account"]["window_requests"] == 2
        with pytest.raises(TimeoutError):
            await scheduler.acquire(
                request("background-more", ServiceClass.ELASTIC, wait=0.05),
                "account",
                {"requests": 1},
            )
        await scheduler.acquire(request("foreground", ServiceClass.INTERACTIVE), "account", {})
        assert scheduler.snapshot()["account"]["window_requests"] == 3
        with pytest.raises(TimeoutError):
            await scheduler.acquire(
                request("over-rpm", ServiceClass.INTERACTIVE, wait=0.05), "account", {}
            )
    finally:
        await scheduler.close()
