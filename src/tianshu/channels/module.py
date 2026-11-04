"""Root assembles delivery before Expression, then binds ingress after Conversation."""

from pathlib import Path
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.migrations import Migration
from .service import Channels


def build_module(
    runtime, *, settings, identity, sources, actions, catalog, artifacts, configuration, web=None
):
    service = Channels(
        runtime,
        settings,
        identity=identity,
        sources=sources,
        actions=actions,
        catalog=catalog,
        artifacts=artifacts,
        configuration=configuration,
        web=web,
    )
    from .api import register_routes

    def routes(app, _runtime):
        register_routes(app, service)

    def tasks(_jobs, _runtime):
        service.register_tasks()

    async def start(_runtime):
        await service.start()

    async def stop(_runtime):
        await service.close()

    module = DomainModule(
        "channels",
        routes,
        start,
        stop,
        tasks,
        (
            Migration(
                "channels-0001", Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            ),
            Migration(
                "channels-0002-media-supplements",
                Path(__file__).with_name("media_supplements.sql").read_text(encoding="utf-8"),
            ),
        ),
    )
    return module, service
