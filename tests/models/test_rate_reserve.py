import asyncio

import pytest

from tianshu.models.catalog import QuotaPolicy
from tianshu.models.scheduler import QuotaScheduler
from tianshu.types import ServiceClass
from test_quota_scheduler import request


async def test_background_cannot_consume_foreground_token_window():
    policy = QuotaPolicy("account", 4, 1, 2, tpm=100,
                         interactive_unit_reserve={"tokens": 40})
    scheduler = QuotaScheduler({"account": policy})
    await scheduler.start()
    try:
        await scheduler.acquire(request("background", ServiceClass.ELASTIC), "account", {"tokens": 60})
        await scheduler.release("account", "background", units={"tokens": 60})
        with pytest.raises(TimeoutError):
            await scheduler.acquire(request("background-more", ServiceClass.ELASTIC, wait=0.05),
                                    "account", {"tokens": 1})
        await scheduler.acquire(request("foreground", ServiceClass.INTERACTIVE), "account", {"tokens": 40})
    finally:
        await scheduler.close()
