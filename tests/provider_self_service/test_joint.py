"""Recorded local platform/gateway/upstream joint test. No external provider is contacted."""

import asyncio
import json
import os
import ssl
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import aiohttp
from aiohttp import web
from jsonschema import Draft202012Validator

WORKSPACE = Path(os.environ["TIANSHU_WORKSPACE"])
PRODUCTS = WORKSPACE / "worktrees"
sys.path[:0] = [
    str(PRODUCTS / "PROVIDER-P1/tianshu-platform"),
    str(PRODUCTS / "PROVIDER-P1/tianshu-platform/tests/backend"),
    str(PRODUCTS / "PROVIDER-G1/tianshu-model-gateway/src"),
    str(PRODUCTS / "PROVIDER-G1/tianshu-model-gateway/tests"),
    str(PRODUCTS / "PROVIDER-C1/tianshu-companion/src"),
    str(PRODUCTS / "PROVIDER-C1/tianshu-companion/tests"),
]
os.environ["TS012_CONTRACT_DIR"] = str(WORKSPACE / "contracts/text-dialogue/v1")
os.environ["TIANSHU_CONTRACTS"] = str(WORKSPACE / "contracts/text-dialogue/v1")

from fixtures import ENV, bearer  # noqa: E402
from gateway_fixtures import RecordingServices, registration, start_http  # noqa: E402
from observability_fixtures import start_tls, write_tls  # noqa: E402
from services.platform.provider_catalog import ProviderCatalog  # noqa: E402
from services.platform.server import create_app as platform_app  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from services.platform.web_console import WebConsole  # noqa: E402
from tianshu_gateway.config import ClientGrant  # noqa: E402
from tianshu_gateway.provider_adapter import OpenAIAdapter  # noqa: E402
from tianshu_gateway.server import GATEWAY, Settings, create_app as gateway_app  # noqa: E402
from web_fixtures import PASSWORD, web_settings  # noqa: E402
from support import Harness  # noqa: E402
from tianshu_companion.clients import Gateway as CompanionGateway  # noqa: E402
from tianshu_companion.contracts import Fault as CompanionFault  # noqa: E402
from tianshu_companion.model_selection import HttpDefaultModelSelector  # noqa: E402


class FixtureTargets:
    def permits(self, address, connection_type):
        return address == "127.0.0.1"


class LocalServiceClient:
    """Recorded loopback transport for production companion ports in this joint test."""

    def __init__(self, url, token, session):
        self.url, self.token, self.session = url, token, session

    async def call(self, path, body=None, headers=None):
        async with self.session.request("GET" if body is None else "POST",
            self.url + path, json=body,
            headers={"Authorization": "Bearer " + self.token, **(headers or {})}) as response:
            result = await response.json()
            if response.status != 200:
                raise CompanionFault("dependency_unavailable")
            return result


class JointTest(unittest.IsolatedAsyncioTestCase):
    def assert_provider_error(self, document):
        schema = json.loads((Path(__file__).resolve().parents[2] /
                             "contracts/provider-self-service/v1/schema.json").read_text())
        Draft202012Validator({"$ref": "#/$defs/error", "$defs": schema["$defs"]}).validate(document)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager_token = "synthetic-provider-management-token-0123456789"
        env = patch.dict(os.environ, {**ENV, "TS_PROVIDER_MANAGEMENT": self.manager_token,
                                      "TIANSHU_WORKSPACE": str(WORKSPACE)})
        env.start()
        self.addCleanup(env.stop)
        self.clock_value = time.time()
        self.calls = []
        self.mode = "normal"
        self.services = RecordingServices()
        upstream = web.Application()
        upstream.router.add_get("/v1/models", self.models)
        upstream.router.add_post("/v1/chat/completions", self.upstream)
        self.upstream_runner, self.upstream_url = await start_tls(upstream, self.root)
        self.addAsyncCleanup(self.upstream_runner.cleanup)
        self.services.configure(self.upstream_url + "/v1")
        cert, _ = write_tls(self.root)
        self.ssl = ssl.create_default_context(cafile=str(cert))
        catalog_dir = self.root / "catalog"
        ProviderCatalog(catalog_dir, create=True)
        settings = web_settings(str(self.root), origin="http://127.0.0.1:4814")
        settings["principals"]["companion"]["actions"].append("config.select")
        settings["principals"]["gateway"]["actions"].append("provider.runtime")
        settings["provider_self_service"] = {
            "directory": str(catalog_dir), "gateway_url": "http://127.0.0.1:9",
            "gateway_token_env": "TS_PROVIDER_MANAGEMENT",
        }
        self.platform = Platform(settings, clock=lambda: self.clock_value)
        self.console = WebConsole(self.platform)
        self.platform_runner, self.platform_url = await start_http(
            platform_app(self.platform, console=self.console)
        )
        self.addAsyncCleanup(self.platform_runner.cleanup)
        settings["web"]["origin"] = self.platform_url
        self.settings = settings
        references = {
            "secret-ref:fixture/platform": "TS012_GATEWAY",
            "secret-ref:fixture/client": "TS012_COMPANION",
            "secret-ref:fixture/management": "TS_PROVIDER_MANAGEMENT",
        }
        gateway_settings = Settings(
            contract_directory=str(WORKSPACE / "contracts/text-dialogue/v1"),
            diagnostics_path=str(self.root / "gateway.sqlite"),
            platform_base_url=self.platform_url,
            platform_credential_ref="secret-ref:fixture/platform",
            platform_origin_env="TS041_TEST_ORIGIN",
            secret_references=references,
            targets=[registration(self.platform_url)],
            clients=[ClientGrant("companion", "secret-ref:fixture/client",
                                 "provider-fixture", 7, True)],
            provider_management_credential_ref="secret-ref:fixture/management",
            provider_self_service=True,
        )
        self.gateway_runner, self.gateway_url = await start_http(gateway_app(gateway_settings))
        self.addAsyncCleanup(self.gateway_runner.cleanup)
        self.gateway = self.gateway_runner.app[GATEWAY]
        async def resolver(host, port):
            return ("127.0.0.1",)
        self.gateway.provider_adapter = OpenAIAdapter(
            policy=FixtureTargets(), resolver=resolver, tls_context=self.ssl
        )
        settings["provider_self_service"]["gateway_url"] = self.gateway_url
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                             timeout=aiohttp.ClientTimeout(total=10),
                                             trust_env=False)
        self.addAsyncCleanup(self.client.close)
        self.internal_client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
                                                     trust_env=False)
        self.addAsyncCleanup(self.internal_client.close)
        async with self.client.get(self.platform_url + "/api/web/session") as response:
            anonymous = await response.json()
        self.csrf = (await self.web("login", {"username": "synthetic-admin",
                                              "password": PASSWORD}, anonymous["csrf"]))[1]["csrf"]
        await self.web("models/unlock", {"password": PASSWORD})

    async def models(self, request):
        self.calls.append(("models", request.headers.get("Authorization")))
        if self.mode == "unsupported":
            return web.Response(status=404)
        return web.json_response({"data": [{"id": "fixture-text-model"}]})

    async def upstream(self, request):
        body = await request.json()
        self.calls.append(("completion", request.headers.get("Authorization"), body))
        if self.mode == "slow":
            await asyncio.sleep(3)
        if self.mode == "wrong-key":
            return web.json_response({"error": {"message": "synthetic-secret-reflected"}}, status=401)
        if self.mode == "missing-model":
            return web.json_response({"error": {"code": "model_not_found"}}, status=404)
        return web.json_response(self.services.response)

    async def web(self, path, body, csrf=None):
        async with self.client.post(self.platform_url + "/api/web/" + path,
            json=body, headers={"Origin": self.platform_url,
                                "X-CSRF-Token": csrf or self.csrf}) as response:
            return response.status, await response.json()

    async def service(self, path, body, principal):
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(),
                                         trust_env=False) as service_client:
            async with service_client.post(
                self.platform_url + "/internal/v1/provider-self-service/" + path,
                json=body, headers={"Authorization": bearer(principal)}
            ) as response:
                return response.status, await response.json()

    async def chat(self, version, key="chat-1", turn_id="turn-fixture-1"):
        headers = {"Authorization": bearer("COMPANION"), "X-Request-ID": key,
                   "X-Tianshu-Turn-ID": turn_id,
                   "X-Tianshu-Config-Version": str(version),
                   "X-Tianshu-Workload": "companion.text"}
        async with self.client.post(self.gateway_url + "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}], "stream": False},
            headers=headers) as response:
            return response.status, await response.json()

    async def restart_platform(self):
        port = urlsplit(self.platform_url).port
        await self.platform_runner.cleanup()
        self.platform.close()
        self.platform = Platform(self.settings, clock=lambda: self.clock_value)
        self.console = WebConsole(self.platform)
        runner = web.AppRunner(platform_app(self.platform, console=self.console), handler_cancellation=True,
                               access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        self.platform_runner = runner
        self.addAsyncCleanup(runner.cleanup)
        async with self.client.get(self.platform_url + "/api/web/session") as response:
            anonymous = await response.json()
        self.csrf = (await self.web("login", {"username": "synthetic-admin",
                       "password": PASSWORD}, anonymous["csrf"]))[1]["csrf"]
        await self.web("models/unlock", {"password": PASSWORD})

    async def test_catalog_gateway_runtime(self):
        status, empty = await self.web("providers/view", {})
        self.assertEqual((status, empty["providers"]), (200, []))
        key = "synthetic-upstream-api-key-12345"
        status, provider = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "name": "Synthetic provider", "protocol":
            "openai-chat-completions", "base_url": self.upstream_url + "/v1",
            "model_id": "", "enabled": True, "api_key": key,
        })
        self.assertEqual(status, 200, provider)
        self.assertNotIn(key, json.dumps(provider))
        pid = provider["provider_id"]
        status, listing = await self.web("providers/models", {"provider_id": pid,
                                                               "expected_revision": 1})
        self.assertEqual((status, listing["models"]), (200, ["fixture-text-model"]))
        status, provider = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 1,
            "name": "Synthetic provider", "protocol": "openai-chat-completions",
            "base_url": self.upstream_url + "/v1", "model_id": "fixture-text-model",
            "enabled": True, "api_key": "",
        })
        self.assertEqual((status, provider["revision"]), (200, 2))
        test_id = str(uuid.uuid4())
        status, tested = await self.web("providers/test", {"client_id": test_id,
                                            "provider_id": pid, "expected_revision": 2})
        self.assertEqual((status, tested["outcome"]), (200, "succeeded"), tested)
        before = len(self.calls)
        self.assertEqual(await self.web("providers/test", {"client_id": test_id,
                              "provider_id": pid, "expected_revision": 2}), (200, tested))
        self.assertEqual(len(self.calls), before)
        status, chosen = await self.web("providers/default", {"client_id": str(uuid.uuid4()),
                        "provider_id": pid, "expected_revision": 2,
                        "expected_default_revision": 0})
        self.assertEqual(status, 200, chosen)
        request = {"turn_id": "turn-fixture-1", "actor_id": "actor-fixture",
                   "person_id": "person-fixture", "audience": "self_private",
                   "conversation_id": "conversation-fixture", "caller_service": "companion",
                   "workload": "companion.text"}
        status, selection = await self.service("select", request, "COMPANION")
        self.assertEqual(status, 200, selection)
        before = len(self.calls)
        status, denied = await self.chat(selection["config_version"], "chat-ungranted",
                                         "turn-ungranted")
        self.assertEqual(status, 403, denied)
        self.assertEqual(len(self.calls), before)
        status, old_pending = await self.service("select", {
            **request, "turn_id": "turn-fixture-old"}, "COMPANION")
        self.assertEqual(status, 200)
        self.assertEqual(old_pending["config_version"], selection["config_version"])
        status, _ = await self.service("select", {
            **request, "turn_id": "turn-fixture-revoked"}, "COMPANION")
        self.assertEqual(status, 200)
        status, generated = await self.chat(selection["config_version"])
        self.assertEqual(status, 200, generated)
        self.assertNotIn(key, json.dumps(generated))
        self.assertEqual(self.calls[-1][1], "Bearer " + key)
        harness = Harness(self.root / "companion.sqlite", silence_ms=0)
        await harness.core.close()
        harness.gateway = CompanionGateway(harness.contracts, LocalServiceClient(
            self.gateway_url, ENV["TS012_COMPANION"], self.internal_client))
        selector = HttpDefaultModelSelector(LocalServiceClient(
            self.platform_url, ENV["TS012_COMPANION"], self.internal_client))
        harness.core = harness.new_core(default_model_selector=selector)
        try:
            await harness.ingest(text="Recorded local provider reply")
            await harness.cycles(70)
            turns = harness.turns()
            self.assertEqual(turns[0]["phase"], "sent", turns)
            self.assertEqual(turns[0]["config_version"], selection["config_version"])
            self.assertTrue(harness.sender.calls)
        finally:
            await harness.core.close()
        queued_harness = Harness(self.root / "queued-companion.sqlite", silence_ms=0)
        await queued_harness.core.close()
        queued_harness.gateway = CompanionGateway(queued_harness.contracts, LocalServiceClient(
            self.gateway_url, ENV["TS012_COMPANION"], self.internal_client))
        queued_selector = HttpDefaultModelSelector(LocalServiceClient(
            self.platform_url, ENV["TS012_COMPANION"], self.internal_client))
        queued_harness.core = queued_harness.new_core(default_model_selector=queued_selector)
        self.addAsyncCleanup(queued_harness.core.close)
        await queued_harness.ingest(text="Queued before default switch")
        with queued_harness.core.store.transaction():
            queued_harness.core._seal_due(queued_harness.clock())
        self.assertEqual(
            ("queued", selection["config_version"]),
            (queued_harness.turns()[0]["phase"], queued_harness.turns()[0]["config_version"]),
        )
        self.clock_value += 3700
        status, renewed = await self.service("select", {
            **request, "turn_id": "turn-fixture-renewed"}, "COMPANION")
        self.assertEqual(status, 200, renewed)
        self.assertGreater(renewed["config_version"], selection["config_version"])
        self.clock_value -= 3700
        second_key = "synthetic-second-api-key-67890"
        status, second = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "name": "Second synthetic", "protocol":
            "openai-chat-completions", "base_url": self.upstream_url + "/v1",
            "model_id": "fixture-text-model", "enabled": True, "api_key": second_key,
        })
        self.assertEqual(status, 200, second)
        status, _ = await self.web("providers/test", {"client_id": str(uuid.uuid4()),
            "provider_id": second["provider_id"], "expected_revision": 1})
        self.assertEqual(status, 200)
        status, _ = await self.web("providers/default", {"client_id": str(uuid.uuid4()),
            "provider_id": second["provider_id"], "expected_revision": 1,
            "expected_default_revision": chosen["revision"]})
        self.assertEqual(status, 200)
        await queued_harness.cycles(70)
        self.assertEqual(
            ("sent", selection["config_version"]),
            (queued_harness.turns()[0]["phase"], queued_harness.turns()[0]["config_version"]),
        )
        self.assertEqual(self.calls[-1][1], "Bearer " + key)
        await queued_harness.core.close()
        status, newer = await self.service("select", {
            **request, "turn_id": "turn-fixture-new"}, "COMPANION")
        self.assertEqual(status, 200)
        self.assertNotEqual(newer["config_version"], selection["config_version"])
        status, pinned = await self.service("select", {
            **request, "turn_id": "turn-fixture-old"}, "COMPANION")
        self.assertEqual((status, pinned["config_version"]),
                         (200, selection["config_version"]))
        status, _ = await self.chat(selection["config_version"], "chat-old-default",
                                    "turn-fixture-old")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][1], "Bearer " + key)
        status, _ = await self.chat(newer["config_version"], "chat-new-default",
                                    "turn-fixture-new")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][1], "Bearer " + second_key)
        await self.restart_platform()
        status, resumed = await self.web("providers/view", {})
        self.assertEqual(status, 200)
        self.assertEqual(resumed["default"]["provider_id"], second["provider_id"])
        status, after_restart = await self.service("select", {
            **request, "turn_id": "turn-after-restart"}, "COMPANION")
        self.assertEqual(status, 200, after_restart)
        status, _ = await self.chat(after_restart["config_version"], "chat-after-restart",
                                    "turn-after-restart")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][1], "Bearer " + second_key)
        status, updated = await self.web("providers/clear-key", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 2})
        self.assertEqual(status, 200, updated)
        before = len(self.calls)
        status, refusal = await self.chat(selection["config_version"], "chat-revoked",
                                          "turn-fixture-revoked")
        self.assertNotEqual(status, 200, refusal)
        self.assertEqual(len(self.calls), before)
        for path in self.root.rglob("*.sqlite*"):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(key.encode(), raw, path.name)
                self.assertNotIn(second_key.encode(), raw, path.name)

    async def test_failures_manual_model_and_cancel_are_bounded(self):
        key = "synthetic-error-key-12345678"
        status, provider = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "name": "Error fixture",
            "protocol": "openai-chat-completions", "base_url": self.upstream_url + "/v1",
            "model_id": "fixture-text-model", "enabled": True, "api_key": key,
        })
        self.assertEqual(status, 200, provider)
        pid = provider["provider_id"]
        self.mode = "unsupported"
        status, refusal = await self.web("providers/models", {
            "provider_id": pid, "expected_revision": 1})
        self.assertEqual((status, refusal["code"]), (502, "enumeration_unsupported"))
        self.assert_provider_error(refusal)
        self.mode = "wrong-key"
        test_id = str(uuid.uuid4())
        status, refusal = await self.web("providers/test", {
            "client_id": test_id, "provider_id": pid, "expected_revision": 1})
        self.assertEqual((status, refusal["code"]), (502, "authentication_failed"))
        self.assert_provider_error(refusal)
        self.assertNotIn(key, json.dumps(refusal))
        before = len(self.calls)
        status, replay = await self.web("providers/test", {
            "client_id": test_id, "provider_id": pid, "expected_revision": 1})
        self.assertEqual((status, replay["code"]), (502, "authentication_failed"))
        self.assertEqual(len(self.calls), before)
        status, state = await self.web("providers/view", {})
        self.assertEqual(state["providers"][0]["test"]["outcome"], "authentication_failed")
        self.assertNotIn(key, json.dumps(state))
        status, blocked = await self.web("providers/default", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 1,
            "expected_default_revision": 0})
        self.assertEqual((status, blocked["code"]), (409, "provider_not_tested"))
        self.mode = "slow"
        cancel_id = str(uuid.uuid4())
        pending = asyncio.create_task(self.web("providers/test", {
            "client_id": cancel_id, "provider_id": pid, "expected_revision": 1}))
        async with asyncio.timeout(3):
            while len(self.calls) == before:
                await asyncio.sleep(0.01)
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        count = len(self.calls)
        status, unknown = await self.web("providers/test", {
            "client_id": cancel_id, "provider_id": pid, "expected_revision": 1})
        self.assertEqual((status, unknown["code"]), (409, "result_unknown"))
        self.assertEqual((unknown["execution_state"], unknown["retryable"]), ("unknown", False))
        self.assert_provider_error(unknown)
        self.assertEqual(len(self.calls), count)
        self.mode = "normal"
        status, changed = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 1,
            "name": "Error fixture", "protocol": "openai-chat-completions",
            "base_url": self.upstream_url + "/missing", "model_id": "fixture-text-model",
            "enabled": True, "api_key": "",
        })
        self.assertEqual(status, 200, changed)
        status, endpoint = await self.web("providers/test", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 2})
        self.assertEqual((status, endpoint["code"]), (502, "endpoint_failed"))
        status, _ = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 2,
            "name": "Error fixture", "protocol": "openai-chat-completions",
            "base_url": self.upstream_url + "/v1", "model_id": "fixture-text-model",
            "enabled": True, "api_key": "",
        })
        self.assertEqual(status, 200)
        self.mode = "slow"
        self.gateway.provider_adapter.timeout_seconds = 0.2
        status, timed = await self.web("providers/test", {
            "client_id": str(uuid.uuid4()), "provider_id": pid, "expected_revision": 3})
        self.assertEqual((status, timed["code"]), (504, "timed_out"))
        self.assertEqual((timed["execution_state"], timed["retryable"]), ("unknown", False))
        self.assert_provider_error(timed)
        status, state = await self.web("providers/view", {})
        self.assertEqual(status, 200)
        self.assertEqual(state["providers"][0]["test"]["outcome"], "unknown")

    async def test_management_view_rechecks_lock_after_blocked_read(self):
        status, provider = await self.web("providers/save", {
            "client_id": str(uuid.uuid4()), "name": "Private address fixture",
            "protocol": "openai-chat-completions", "base_url": self.upstream_url + "/v1",
            "model_id": "fixture-text-model", "enabled": True,
            "api_key": "synthetic-view-key-1234567890",
        })
        self.assertEqual(status, 200, provider)
        entered, release = threading.Event(), threading.Event()
        original = self.platform.provider_catalog.view

        def blocked_view():
            entered.set()
            release.wait(3)
            return original()

        with patch.object(self.platform.provider_catalog, "view", side_effect=blocked_view):
            pending = asyncio.create_task(self.web("providers/view", {}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            try:
                lock_status, _ = await self.web("models/lock", {})
                self.assertEqual(lock_status, 200)
            finally:
                release.set()
            status, denied = await pending
        self.assertEqual((status, denied["code"]), (403, "management_required"))
        self.assertNotIn(self.upstream_url, json.dumps(denied))

    async def test_queued_mutation_rechecks_revoked_principal(self):
        entered, release = threading.Event(), threading.Event()
        original = self.console.providers._write

        def blocked_write(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)

        with patch.object(self.console.providers, "_write", side_effect=blocked_write):
            pending = asyncio.create_task(self.web("providers/save", {
                "client_id": str(uuid.uuid4()), "name": "Must not commit",
                "protocol": "openai-chat-completions", "base_url": self.upstream_url + "/v1",
                "model_id": "fixture-text-model", "enabled": True,
                "api_key": "synthetic-revocation-key-12345",
            }))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            try:
                with self.platform.store.connect(write=True) as db:
                    db.execute("INSERT INTO revoked_principals VALUES (?)", ("admin",))
            finally:
                release.set()
            status, denied = await pending
        self.assertEqual(status, 401, denied)
        self.assertEqual(self.platform.provider_catalog.view()["providers"], [])


if __name__ == "__main__":
    unittest.main()
