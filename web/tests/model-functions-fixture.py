"""Loopback platform with disposable synthetic accounts and provider catalogue."""

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
from services.platform.provider_catalog import ProviderCatalog  # noqa: E402
from services.platform.server import create_app  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from web_fixtures import web_settings  # noqa: E402


async def main():
    with tempfile.TemporaryDirectory(prefix="model-functions-fixture-") as directory:
        with patch.dict(
            os.environ, {**ENV, "TS_FUNCTION_GATEWAY": "synthetic-management-token-only"}
        ):
            catalog = ProviderCatalog(Path(directory) / "providers", create=True)
            for label, model in (("聊天模型", "fixture-chat"), ("辅助模型", "fixture-specialist")):
                item = catalog.save(
                    client_id=model,
                    name=label,
                    base_url="https://example.invalid/v1",
                    model_id=model,
                    api_key="synthetic-only",
                )
                catalog.record_test(
                    client_id="test-" + model,
                    provider_id=item["provider_id"],
                    expected_revision=1,
                    outcome="succeeded",
                )
                if model == "fixture-chat":
                    catalog.set_default(
                        client_id="default",
                        provider_id=item["provider_id"],
                        expected_revision=1,
                        expected_default_revision=0,
                    )
            settings = web_settings(
                directory, origin="http://127.0.0.1:4817", static=root / "apps/web/dist"
            )
            settings["provider_self_service"] = {
                "directory": str(catalog.directory),
                "gateway_url": "http://127.0.0.1:1",
                "gateway_token_env": "TS_FUNCTION_GATEWAY",
            }
            runner = web.AppRunner(create_app(Platform(settings)))
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", 4817).start()
            print("Model function fixture ready", flush=True)
            try:
                await asyncio.Event().wait()
            finally:
                await runner.cleanup()


asyncio.run(main())
