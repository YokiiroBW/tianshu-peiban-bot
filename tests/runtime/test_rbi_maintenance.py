"""Role-local maintenance callbacks share a bounded cadence, never a failure fate."""

import asyncio

from tianshu.runtime.composition import DomainModule, RuntimeServices
from tianshu.runtime.process import maintain_domain, start_domain_maintenance, stop_domain_maintenance


def routes(_app, _services):
    pass


async def test_background_callbacks_are_role_scoped_and_independent_without_operations():
    calls = []
    ready = asyncio.Event()
    stuck_started = asyncio.Event()
    stuck_cancelled = asyncio.Event()
    failed = asyncio.Event()

    async def memory(services):
        calls.append(("memory", services.role))
        ready.set()

    async def growth(_services):
        failed.set()
        raise RuntimeError("fixture domain failure")

    async def life(_services):
        stuck_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stuck_cancelled.set()

    async def should_not_run(_services):
        calls.append(("wrong-role", None))

    modules = (
        DomainModule("memory", routes, maintenance=memory),
        DomainModule("companion", routes, maintenance=growth),
        DomainModule("life", routes, maintenance=life),
        DomainModule("conversation", routes, maintenance=should_not_run),
        DomainModule("unavailable", routes, maintenance=should_not_run, maintenance_roles=("bot-background",)),
        DomainModule("no-hook", routes, start_roles=("bot-background",)),
    )
    services = RuntimeServices(None, None, None, None, {}, role="bot-background",
                               module_status={"unavailable": {"state": "unavailable"}})
    stop = asyncio.Event()
    tasks = start_domain_maintenance(modules, services, stop)
    try:
        assert len(tasks) == 3
        await asyncio.wait_for(asyncio.gather(ready.wait(), stuck_started.wait(), failed.wait()), timeout=1)
        for _ in range(20):
            if services.module_status.get("companion", {}).get("maintenance", {}).get("state") == "failed":
                break
            await asyncio.sleep(0)
        assert services.module_status["companion"]["maintenance"] == {"state": "failed", "error": "RuntimeError"}
        assert calls == [("memory", "bot-background")]
        assert all(not task.done() for task in tasks)
    finally:
        stop.set()
        await stop_domain_maintenance(tasks)
    assert stuck_cancelled.is_set() and all(task.done() for task in tasks)


async def test_explicit_roles_override_start_roles_and_hooks_stop_in_role():
    seen = []
    arrived = asyncio.Event()

    async def tick(services):
        seen.append(services.role)
        arrived.set()

    module = DomainModule("memory", routes, start_roles=("bot-background",),
                          maintenance=tick, maintenance_roles=("bot-foreground",))
    background = RuntimeServices(None, None, None, None, {}, role="bot-background")
    foreground = RuntimeServices(None, None, None, None, {}, role="bot-foreground")
    stop = asyncio.Event()
    assert start_domain_maintenance((module,), background, stop) == ()
    tasks = start_domain_maintenance((module,), foreground, stop)
    try:
        await asyncio.wait_for(arrived.wait(), timeout=1)
        assert seen == ["bot-foreground"]
    finally:
        stop.set()
        await stop_domain_maintenance(tasks)


async def test_stuck_callback_is_bounded_and_reported_without_changing_module_readiness():
    cancelled = asyncio.Event()

    async def blocked(_services):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    module = DomainModule("memory", routes, maintenance=blocked)
    services = RuntimeServices(None, None, None, None, {}, role="bot-background",
                               module_status={"memory": {"state": "ready"}})
    stop = asyncio.Event()
    task = asyncio.create_task(maintain_domain(module, services, stop, interval=10, timeout=0.01))
    try:
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        for _ in range(20):
            if services.module_status["memory"].get("maintenance"):
                break
            await asyncio.sleep(0)
        assert services.module_status["memory"]["state"] == "ready"
        assert services.module_status["memory"]["maintenance"] == {"state": "failed", "error": "TimeoutError"}
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=1)
