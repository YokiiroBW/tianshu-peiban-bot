"""Explicit domain registration; no service locator and no legacy product imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from aiohttp import web

from .api import error_middleware, json_response
from .database import Database

if TYPE_CHECKING:
    from .jobs import JobQueue
    from tianshu.models.ports import ModelPort


@dataclass(slots=True)
class RuntimeServices:
    database: Database
    jobs: JobQueue
    models: ModelPort
    files: Any
    settings: Any


@dataclass(frozen=True, slots=True)
class DomainModule:
    name: str
    register_routes: Callable[[web.Application, RuntimeServices], None]
    start: Callable[[RuntimeServices], Awaitable[None]] | None = None
    stop: Callable[[RuntimeServices], Awaitable[None]] | None = None
    register_tasks: Callable[[Any, RuntimeServices], None] | None = None
    migrations: tuple[Any, ...] = ()


SERVICES = web.AppKey("tianshu.services", RuntimeServices)


def create_app(services: RuntimeServices, modules: tuple[DomainModule, ...] = ()):
    app = web.Application(middlewares=[error_middleware], client_max_size=2 * 1024 * 1024)
    app[SERVICES] = services
    names = [module.name for module in modules]
    if len(set(names)) != len(names):
        raise ValueError("duplicate_domain_registration")
    for module in modules:
        module.register_routes(app, services)

    async def live(_request):
        return json_response({"status": "live"})

    async def ready(_request):
        async with services.database.transaction(control=True, read_only=True) as conn:
            await conn.execute("SELECT 1")
        return json_response({"status": "ready", "domains": names})

    app.router.add_get("/health/live", live)
    app.router.add_get("/health/ready", ready)

    async def lifespan(_app):
        started = []
        try:
            for module in modules:
                if module.start:
                    await module.start(services)
                started.append(module)
            yield
        finally:
            for module in reversed(started):
                if module.stop:
                    await module.stop(services)

    app.cleanup_ctx.append(lifespan)
    return app
