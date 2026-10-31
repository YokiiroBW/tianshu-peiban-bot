"""Explicit domain registration; no service locator and no legacy product imports."""

from __future__ import annotations

from dataclasses import dataclass
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
    role: str = "interactive"
    events: Any = None
    diagnostics: Any = None
    commands: Any = None
    credentials: Any = None


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
    app = web.Application(
        middlewares=[error_middleware],
        client_max_size=services.settings.get("http", {}).get("max_body_bytes", 512 * 1024 * 1024),
    )
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
            cursor = await conn.execute(
                "SELECT role,ready,registration FROM runtime.processes "
                "WHERE heartbeat_at>now()-interval '20 seconds'"
            )
            processes = await cursor.fetchall()
        roles = {row["role"] for row in processes if row["ready"]}
        missing = sorted({"interactive", "model-broker", "worker"} - roles)
        configured = bool(names)
        healthy = configured and not missing
        return json_response(
            {
                "status": "ready" if healthy else "degraded",
                "domains": names,
                "missing_roles": missing,
                "processes": processes,
                "configured": configured,
            },
            status=200 if healthy else 503,
        )

    app.router.add_get("/health/live", live)
    app.router.add_get("/health/ready", ready)

    async def lifespan(_app):
        started = []
        try:
            for module in modules:
                started.append(module)
                if module.start:
                    await module.start(services)
            yield
        finally:
            for module in reversed(started):
                if module.stop:
                    await module.stop(services)

    app.cleanup_ctx.append(lifespan)
    return app
