"""A real SDK handshake crosses the unified host's separate owner loops."""

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from deploy.single_container.applications import NativeModule
from deploy.single_container.threaded_exchange import ThreadedExchange


class OneBotThreadBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_handshake_and_persisted_identity_across_owners(self):
        try:
            import nonebot
            import websockets
            from nonebot.adapters.onebot.v11 import Adapter
            from services.platform.bot_adapter_catalog import BotAdapterCatalog
            from services.platform.onebot_servers import OneBotServers
            from services.platform.server import PLATFORM
        except ImportError as exc:
            self.skipTest(f"joint fixture dependency unavailable: {exc}")
        try:
            nonebot.get_driver()
        except ValueError:
            pass
        else:
            self.skipTest("NoneBot was initialized by another test in this process")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            catalog = BotAdapterCatalog(str(Path(directory) / "catalog"))
            adapter_owner = SimpleNamespace(catalog=catalog)
            manager = OneBotServers(adapter_owner)
            platform = SimpleNamespace(
                bot_adapters=SimpleNamespace(onebot_servers=manager)
            )
            module = NativeModule(
                "onebot",
                {"origin": f"http://onebot:{port}", "listen_host": "127.0.0.1"},
                {},
            )
            module.onebot_access_token = "native-token"
            exchange = ThreadedExchange()

            async def onebot_factory(ports):
                module.ports = ports
                nonebot.init(
                    driver="~fastapi",
                    onebot_access_token="native-token",
                    _env_file=None,
                )
                nonebot.get_driver().register_adapter(Adapter)
                return nonebot.get_asgi()

            async def platform_factory(ports):
                app = web.Application()
                app[PLATFORM] = platform
                return app

            exchange.register_factory(
                "onebot", onebot_factory, "asgi", f"http://onebot:{port}"
            )
            exchange.register_factory(
                "platform", platform_factory, "aiohttp", "http://platform:8080"
            )
            await exchange.open()
            try:
                await exchange.invoke("onebot", module.start_public)
                server = await exchange.invoke(
                    "platform",
                    lambda app: manager.create(
                        {
                            "name": "QQ test",
                            "token": "fixture-robot-token",
                            "client_id": "fixture-create",
                        }
                    ),
                )
                path = server["id"].split(":", 1)[1]
                url = f"ws://127.0.0.1:{port}/onebot/v11/ws/{path}"
                async with websockets.connect(
                    url,
                    additional_headers={
                        "Authorization": "Bearer fixture-robot-token",
                        "X-Self-ID": "10001",
                    },
                ):
                    for _ in range(100):
                        if await exchange.invoke(
                            "onebot", lambda app: module.onebot_server_status()
                        ):
                            break
                        await asyncio.sleep(0.01)
                    live = await exchange.invoke(
                        "onebot", lambda app: module.onebot_server_status()
                    )
                    persisted = await exchange.invoke(
                        "platform", lambda app: manager.row(server["id"])
                    )
                    self.assertEqual(live, {server["id"]: "10001"})
                    self.assertEqual(persisted["account_id"], "10001")
                for _ in range(100):
                    if not await exchange.invoke(
                        "onebot", lambda app: module.onebot_server_status()
                    ):
                        break
                    await asyncio.sleep(0.01)
                restored = OneBotServers(adapter_owner)
                self.assertTrue(
                    await restored.authorize(
                        server["id"], "fixture-robot-token", "10001"
                    )
                )
                self.assertFalse(
                    await restored.authorize(
                        server["id"], "fixture-robot-token", "10002"
                    )
                )
            finally:
                await exchange.invoke("onebot", module.stop_accepting)
                await exchange.invoke("onebot", module.close_public)
                self.assertTrue(await exchange.close())


if __name__ == "__main__":
    unittest.main()
