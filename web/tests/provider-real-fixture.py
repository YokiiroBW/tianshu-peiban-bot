"""Isolated real platform/gateway/recorded TLS upstream for browser acceptance."""

import asyncio
import json
import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

workspace = Path(os.environ["TIANSHU_WORKSPACE"])
product = Path(__file__).resolve().parents[3]
products = Path(os.environ["TIANSHU_PRODUCT_WORKSPACE"]) / "worktrees"
fixed_revisions = {
    "PROVIDER-P1/tianshu-platform": "5ead3759a44744a15f50f095870c4e34f40eb7b5",
    "PROVIDER-G1/tianshu-model-gateway": "f93b08a6ce2d31774d2f1b27dc00fd4d5f3ee26d",
    "PROVIDER-C1/tianshu-companion": "74dfdaefbd8c38a77d52ebbfb69a942d5c2b2963",
}
for relative, expected in fixed_revisions.items():
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=products / relative, text=True).strip()
    if actual != expected:
        raise RuntimeError(f"fixed provider fixture revision changed: {relative}")
sys.path[:0] = [
    str(products / "PROVIDER-P1/tianshu-platform"),
    str(products / "PROVIDER-P1/tianshu-platform/tests/backend"),
    str(products / "PROVIDER-G1/tianshu-model-gateway/src"),
    str(products / "PROVIDER-G1/tianshu-model-gateway/tests"),
]
os.environ["TS012_CONTRACT_DIR"] = str(workspace / "contracts/text-dialogue/v1")
os.environ["TIANSHU_CONTRACTS"] = str(workspace / "contracts/text-dialogue/v1")

from fixtures import ENV  # noqa: E402
from gateway_fixtures import RecordingServices, registration, start_http  # noqa: E402
from observability_fixtures import start_tls, write_tls  # noqa: E402
from services.platform.provider_catalog import ProviderCatalog  # noqa: E402
from services.platform.server import create_app as platform_app  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from tianshu_gateway.config import ClientGrant  # noqa: E402
from tianshu_gateway.provider_adapter import OpenAIAdapter  # noqa: E402
from tianshu_gateway.server import GATEWAY, Settings, create_app as gateway_app  # noqa: E402
from web_fixtures import web_settings  # noqa: E402


class FixtureTargets:
    def permits(self, address, connection_type):
        return address == "127.0.0.1"


async def main():
    events_path = product / "apps/web/test-results/provider-real-events.json"
    info_path = product / "apps/web/test-results/provider-real-info.json"
    mode_path = product / "apps/web/test-results/provider-real-mode.txt"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    mode_path.write_text("normal", encoding="utf-8")
    events = []

    def record(name):
        events.append(name)
        events_path.write_text(json.dumps(events), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="provider-u1-") as directory:
        root = Path(directory)
        with patch.dict(os.environ, {**ENV, "TS_PROVIDER_MANAGEMENT": "synthetic-provider-management-token-0123456789"}):
            services = RecordingServices()
            upstream = web.Application()

            async def models(request):
                record("models")
                if mode_path.read_text(encoding="utf-8") == "unsupported":
                    return web.Response(status=404)
                return web.json_response({"data": [{"id": "fixture-text-model"}]})

            async def completion(request):
                record("completion")
                if mode_path.read_text(encoding="utf-8") == "invalid":
                    return web.json_response({"unexpected": "response"})
                if mode_path.read_text(encoding="utf-8") == "wrong-key":
                    return web.json_response({"error": {"message": "synthetic-secret-must-not-appear"}}, status=401)
                return web.json_response(services.response)

            upstream.router.add_get("/v1/models", models)
            upstream.router.add_post("/v1/chat/completions", completion)
            upstream_runner, upstream_url = await start_tls(upstream, root)
            services.configure(upstream_url + "/v1")
            cert, _ = write_tls(root)
            tls = ssl.create_default_context(cafile=str(cert))
            catalog_dir = root / "catalog"
            ProviderCatalog(catalog_dir, create=True)
            base_url = "http://127.0.0.1:4814"
            settings = web_settings(str(root), origin=base_url, static=product / "apps/web/dist")
            # The ordinary provider page must work without legacy template publishing.
            settings.pop("web_models", None)
            settings["provider_self_service"] = {
                "directory": str(catalog_dir),
                "gateway_url": "http://127.0.0.1:9",
                "gateway_token_env": "TS_PROVIDER_MANAGEMENT",
            }
            platform = Platform(settings)
            references = {
                "secret-ref:fixture/platform": "TS012_GATEWAY",
                "secret-ref:fixture/client": "TS012_COMPANION",
                "secret-ref:fixture/management": "TS_PROVIDER_MANAGEMENT",
            }
            gateway_settings = Settings(
                contract_directory=str(workspace / "contracts/text-dialogue/v1"),
                diagnostics_path=str(root / "gateway.sqlite"),
                platform_base_url=base_url,
                platform_credential_ref="secret-ref:fixture/platform",
                platform_origin_env="TS041_TEST_ORIGIN",
                secret_references=references,
                targets=[registration(base_url)],
                clients=[ClientGrant("companion", "secret-ref:fixture/client", "provider-fixture", 7, True)],
                provider_management_credential_ref="secret-ref:fixture/management",
                provider_self_service=True,
            )
            gateway_runner, gateway_url = await start_http(gateway_app(gateway_settings))
            gateway_runner.app[GATEWAY].provider_adapter = OpenAIAdapter(
                policy=FixtureTargets(), resolver=lambda host, port: asyncio.sleep(0, result=("127.0.0.1",)),
                tls_context=tls,
            )
            settings["provider_self_service"]["gateway_url"] = gateway_url
            runner = web.AppRunner(platform_app(platform), handler_cancellation=True, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", 4814).start()
            info_path.write_text(json.dumps({"upstream_url": upstream_url + "/v1", "fixed_revisions": fixed_revisions}), encoding="utf-8")
            record("ready")
            print("PROVIDER_U1_READY", flush=True)
            try:
                await asyncio.Event().wait()
            finally:
                await runner.cleanup()
                await gateway_runner.cleanup()
                await upstream_runner.cleanup()
                platform.close()


if __name__ == "__main__":
    asyncio.run(main())
