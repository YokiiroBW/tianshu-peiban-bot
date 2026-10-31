"""Disposable browser fixture: real console, encrypted catalog, synthetic gateway."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root / "tests/backend")]
from fixtures import ENV  # noqa: E402
from services.platform.external_catalog import ExternalCatalog  # noqa: E402
from services.platform.provider_catalog import ProviderCatalog  # noqa: E402
from services.platform.server import create_app, create_console  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from web_fixtures import web_settings  # noqa: E402


async def main():
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.dict(
            os.environ, {**ENV, "TS_RETRIEVAL_TEST_GATEWAY": "synthetic-only-gateway-token"}
        ),
    ):

        async def model(request):
            body = await request.json()
            operation = request.match_info["operation"]
            if operation == "models":
                return web.json_response(
                    {
                        "models": [
                            "fixture-chat",
                            "fixture-writing",
                            "fixture-embed",
                            "fixture-rerank",
                        ]
                    }
                )
            if body["model_id"] == "unavailable-model":
                return web.json_response({"code": "model_not_found"}, status=404)
            if operation == "test":
                return web.json_response({"outcome": "succeeded"})
            return web.json_response({"vectors": [[1.0, 0.0]], "scores": [0.9, 0.1]})

        gateway = web.Application()
        gateway.router.add_post("/internal/v1/provider-self-service/{operation}", model)
        peer = web.AppRunner(gateway)
        await peer.setup()
        await web.TCPSite(peer, "127.0.0.1", 4829).start()
        catalog = ProviderCatalog(Path(directory) / "providers", create=True)
        source = catalog.sources.save(
            client_id="retrieval-source",
            name="Synthetic provider",
            base_url="https://models.example/v1",
            api_key="synthetic-secret-not-real",
        )
        for kind in ("embed", "rerank"):
            catalog.sources.add_models(
                client_id="retrieval-model-" + kind,
                source_id=source["source_id"],
                expected_revision=1,
                models=["fixture-" + kind],
                kind=kind,
            )
        settings = web_settings(
            directory, origin="http://127.0.0.1:4828", static=root / "apps/web/dist"
        )
        settings["principals"]["admin"]["actions"].append("external.manage")
        settings["provider_self_service"] = {
            "directory": str(catalog.directory),
            "gateway_url": "http://127.0.0.1:4829",
            "gateway_token_env": "TS_RETRIEVAL_TEST_GATEWAY",
        }
        platform = Platform(settings)
        console = create_console(platform)
        console.external.catalog = ExternalCatalog(Path(directory) / "external", create=True)
        runner = web.AppRunner(create_app(platform, console=console))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 4828).start()
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await peer.cleanup()


asyncio.run(main())
