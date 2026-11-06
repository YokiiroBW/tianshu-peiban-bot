"""Explicit domain registration; no service locator and no legacy product imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from aiohttp import web

from .api import error_middleware, json_response
from .database import Database
from .execution import PROCESS_ROLES

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
    statistics: Any = None
    role_ready: Callable[[], Any] | None = None
    module_status: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainModule:
    name: str
    register_routes: Callable[[web.Application, RuntimeServices], None]
    start: Callable[[RuntimeServices], Awaitable[None]] | None = None
    stop: Callable[[RuntimeServices], Awaitable[None]] | None = None
    register_tasks: Callable[[Any, RuntimeServices], None] | None = None
    migrations: tuple[Any, ...] = ()
    start_roles: tuple[str, ...] | None = None
    maintenance: Callable[[RuntimeServices], Awaitable[None]] | None = None
    maintenance_roles: tuple[str, ...] | None = None


MODULE_START_ROLES = {
    "channels": ("interactive",),
    "capabilities": ("interactive", "bot-foreground", "bot-background", "independent"),
    "conversation": ("bot-foreground",),
    "memory": ("bot-background",),
    "companion": ("bot-background",),
    "life": ("bot-background",),
}


def module_start_roles(module):
    return module.start_roles if module.start_roles is not None else MODULE_START_ROLES.get(module.name, ())


def module_maintenance_roles(module):
    return module.maintenance_roles if module.maintenance_roles is not None else module_start_roles(module)


async def start_module(module, services):
    """Start only the owning role; a local optional failure stays local."""
    if services.module_status.get(module.name, {}).get("state") == "unavailable":
        return False
    roles = module_start_roles(module)
    if module.start is None or services.role not in roles:
        return False
    try:
        await module.start(services)
    except Exception as exc:
        services.module_status[module.name] = {"state": "unavailable", "error": type(exc).__name__}
        if module.stop:
            try:
                await module.stop(services)
            except Exception:
                pass
        return False
    services.module_status[module.name] = {"state": "ready"}
    return True


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
        missing = sorted(set(PROCESS_ROLES) - roles)
        configured = bool(names)
        # This endpoint answers whether the API itself can serve requests. Other
        # role and module health is reported separately, not an API restart cause.
        healthy = configured and "interactive" in roles
        return json_response(
            {
                "status": "ready" if healthy else "degraded",
                "domains": names,
                "missing_roles": missing,
                "processes": processes,
                "configured": configured,
                "module_status": services.module_status,
                "business_progress": "unknown",
            },
            status=200 if healthy else 503,
        )

    app.router.add_get("/health/live", live)
    app.router.add_get("/health/ready", ready)

    async def lifespan(_app):
        started = []
        try:
            _app["data_generation"] = await services.jobs.current_generation()
            for module in modules:
                if await start_module(module, services):
                    started.append(module)
            yield
        finally:
            for module in reversed(started):
                if module.stop:
                    try:
                        await module.stop(services)
                    except Exception:
                        pass

    app.cleanup_ctx.append(lifespan)
    return app
