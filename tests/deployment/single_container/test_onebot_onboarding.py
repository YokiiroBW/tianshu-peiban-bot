"""Owned OneBot bootstrap and per-robot WebSocket routing."""

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy.single_container.applications import (
    OneBotListener,
    onebot_onboarding,
    onebot_token,
)


class OneBotOnboardingTests(unittest.TestCase):
    def test_reads_internal_access_key_from_owned_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / "storage.json"
            settings.write_text(
                json.dumps({"storage_backends": {"backend": "fixture"}})
            )
            token_file = root / "token"
            token_file.write_text("original-listener-token")
            spec = {
                "settings": str(settings),
                "data_directory": str(root),
                "token_file": str(token_file),
                "origin": "http://onebot.internal:18765",
            }
            self.assertEqual(onebot_token(spec, {}), "original-listener-token")
            environment = {"ONEBOT_STORAGE": "fixture"}
            read_key = Mock(return_value="synthetic-adapter-key")
            package = types.ModuleType("tianshu_nonebot")
            package.__path__ = []
            storage = types.ModuleType("tianshu_nonebot.storage")
            storage.read_access_key = read_key
            with patch.dict(
                sys.modules,
                {"tianshu_nonebot": package, "tianshu_nonebot.storage": storage},
            ):
                result = onebot_onboarding(spec, environment)
            self.assertEqual(
                result, {"access_key": "synthetic-adapter-key", "ws_port": 18765}
            )
            read_key.assert_called_once_with(
                root / "adapter.sqlite3", {"backend": "fixture"}, environ=environment
            )


class OneBotWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_socket_accept_releases_reservation_without_binding(self):
        operations = []

        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})

        async def authorize(server_id, token, account):
            return True

        async def reserve(server_id, token, account):
            operations.append("reserve")
            return "ticket"

        async def claim(server_id, token, account, reservation):
            operations.append("claim")
            return True

        async def release(server_id, reservation):
            operations.append("release")

        async def interrupted(message):
            raise ConnectionResetError("client disconnected")

        listener = OneBotListener(
            app,
            1,
            token="native",
            authorize=authorize,
            reserve=reserve,
            claim=claim,
            release=release,
        )
        scope = {
            "type": "websocket",
            "path": "/onebot/v11/ws/" + "a" * 32,
            "headers": [(b"authorization", b"Bearer robot"), (b"x-self-id", b"10001")],
        }
        with self.assertRaises(ConnectionResetError):
            await listener(scope, None, interrupted)
        self.assertEqual(operations, ["reserve", "release"])
        self.assertEqual(listener.connected_status(), {})

    async def test_native_websocket_handshake_routes_tokens_and_tracks_disconnect(self):
        try:
            import nonebot
            import websockets
            from nonebot.adapters.onebot.v11 import Adapter
        except ImportError:
            self.skipTest(
                "NoneBot and websockets are needed for the native handshake test"
            )
        try:
            nonebot.get_driver()
        except ValueError:
            pass
        else:
            self.skipTest("NoneBot was initialized by another test in this process")
        nonebot.init(
            driver="~fastapi", onebot_access_token="native-token", _env_file=None
        )
        driver = nonebot.get_driver()
        driver.register_adapter(Adapter)
        app = nonebot.get_asgi()
        server_id = "onebot:" + "a" * 32
        bound = {}

        async def authorize(identifier, token, account):
            return (
                identifier == server_id
                and token == "robot-token"
                and bound.get(identifier, account) == account
            )

        async def reserve(identifier, token, account):
            return (
                "reservation" if await authorize(identifier, token, account) else None
            )

        async def claim(identifier, token, account, reservation):
            if reservation != "reservation":
                return False
            if not await authorize(identifier, token, account):
                return False
            bound[identifier] = account
            return True

        async def release(identifier, reservation):
            pass

        listener = OneBotListener(
            app,
            0,
            host="127.0.0.1",
            token="native-token",
            authorize=authorize,
            reserve=reserve,
            claim=claim,
            release=release,
        )
        async with app.router.lifespan_context(app):
            await listener.start()
            try:
                port = listener.server.servers[0].sockets[0].getsockname()[1]
                url = f"ws://127.0.0.1:{port}/onebot/v11/ws/{'a' * 32}"
                with self.assertRaises(websockets.exceptions.InvalidStatus):
                    async with websockets.connect(
                        url,
                        additional_headers={
                            "Authorization": "Bearer wrong",
                            "X-Self-ID": "10001",
                        },
                    ):
                        pass
                self.assertEqual(bound, {})
                async with websockets.connect(
                    url,
                    additional_headers={
                        "Authorization": "Bearer robot-token",
                        "X-Self-ID": "10001",
                    },
                ) as ws:
                    await ws.ping()
                    for _ in range(100):
                        if listener.connected_status():
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(bound, {server_id: "10001"})
                    self.assertEqual(listener.connected_status(), {server_id: "10001"})
                for _ in range(100):
                    if not listener.connected_status():
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(listener.connected_status(), {})
                with self.assertRaises(websockets.exceptions.InvalidStatus):
                    async with websockets.connect(
                        url,
                        additional_headers={
                            "Authorization": "Bearer robot-token",
                            "X-Self-ID": "10002",
                        },
                    ):
                        pass
                legacy = f"ws://127.0.0.1:{port}/onebot/v11/ws"
                async with websockets.connect(
                    legacy,
                    additional_headers={
                        "Authorization": "Bearer native-token",
                        "X-Self-ID": "10002",
                    },
                ):
                    self.assertEqual(listener.connected_status(), {})
            finally:
                await listener.stop_accepting()
                await listener.close()


if __name__ == "__main__":
    unittest.main()
