"""Real OneBot adapter plus Platform automatic enrollment, with a synthetic WS peer."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.single_container.applications import OneBotListener
from deploy.single_container.exchange import LocalExchange
from fixtures import ENV
from services.platform.service import Platform
from test_bots import bot_settings


class OneBotNativeJoinTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_automatically_enrolls_the_actual_connected_sdk_account(
        self,
    ):
        try:
            import nonebot
            import websockets
            from tianshu_nonebot.onebot import create_host
            from tianshu_nonebot.storage import read_access_key
        except ImportError as exc:
            self.skipTest(f"joint fixture dependency unavailable: {exc}")
        try:
            nonebot.get_driver()
        except ValueError:
            pass
        else:
            self.skipTest("NoneBot was initialized by another test in this process")
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                os.environ, {**ENV, "TS_ADAPTER_CORE_TOKEN": "synthetic-core-token"}
            ),
        ):
            root = Path(folder)
            settings = bot_settings(folder)
            settings["core"] = {
                "base_url": "https://127.0.0.1:9443",
                "token_env": "TS_ADAPTER_CORE_TOKEN",
            }
            settings["bot_adapter_self_service"] = {
                "directory": str(root / "private-adapters"),
                "allowed_cidrs": ["127.0.0.0/8"],
                "actors": [{"id": "actor:a", "label": "Synthetic actor"}],
            }
            platform = Platform(settings)
            app = create_host(
                host="127.0.0.1",
                port=18765,
                data_dir=root / "onebot",
                access_token="native-test-token",
            )
            instance = app.state.lifecycle.instance_id
            adapter_key = read_access_key(root / "onebot" / "adapter.sqlite3")
            exchange = LocalExchange()
            exchange.register("onebot", app, "asgi", "http://onebot:18765")
            await exchange.open()
            manager = platform.bot_adapters.onebot_servers
            listener = OneBotListener(
                app,
                0,
                host="127.0.0.1",
                token="native-test-token",
                authorize=manager.authorize,
                reserve=manager.reserve,
                claim=manager.claim,
                release=manager.release,
            )

            async def status():
                return listener.connected_status()

            platform.bot_adapters.bind_owned_adapter(
                instance,
                exchange.session_factory("onebot"),
                origin="http://onebot:18765",
                onboarding=lambda: {"access_key": adapter_key, "ws_port": 18765},
                server_status=status,
            )
            await listener.start()
            try:
                row = await manager.create(
                    {
                        "name": "Fixture QQ",
                        "token": "fixture-test-token",
                        "client_id": "synthetic-create",
                    }
                )
                port = listener.server.servers[0].sockets[0].getsockname()[1]
                uri = (
                    f"ws://127.0.0.1:{port}/onebot/v11/ws/{row['id'].split(':', 1)[1]}"
                )
                async with websockets.connect(
                    uri,
                    additional_headers={
                        "Authorization": "Bearer fixture-test-token",
                        "X-Self-ID": "10001",
                    },
                ) as ws:

                    async def sdk_peer():
                        async for raw in ws:
                            request = json.loads(raw)
                            self.assertEqual(request["action"], "get_login_info")
                            await ws.send(
                                json.dumps(
                                    {
                                        "status": "ok",
                                        "retcode": 0,
                                        "data": {
                                            "user_id": 10001,
                                            "nickname": "Synthetic QQ",
                                        },
                                        "echo": request["echo"],
                                    }
                                )
                            )

                    peer = asyncio.create_task(sdk_peer())
                    try:
                        for _ in range(100):
                            if listener.connected_status():
                                break
                            await asyncio.sleep(0.01)
                        await asyncio.wait_for(manager.synchronize(), timeout=10)
                        observed = platform.bot_observation.rows()
                        self.assertEqual(len(observed), 1)
                        self.assertEqual(observed[0]["account_id"], "10001")
                        self.assertEqual(observed[0]["name"], "Fixture QQ")
                        self.assertEqual(observed[0]["state"], "ready")
                        self.assertEqual(manager.row(row["id"])["account_id"], "10001")
                    finally:
                        peer.cancel()
                        await asyncio.gather(peer, return_exceptions=True)
            finally:
                await listener.stop_accepting()
                await listener.close()
                await exchange.close()
                platform.close()


if __name__ == "__main__":
    unittest.main()
