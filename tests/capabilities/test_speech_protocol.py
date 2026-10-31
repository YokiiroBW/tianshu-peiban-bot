import base64, json
import unittest
from types import SimpleNamespace
from tianshu.capabilities.speech.stream import fish_stream
from tianshu.capabilities.speech.mcp import FishMCP
from tianshu.capabilities.external import ProviderError


class SpeechProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_alignment_replaces_prior_chunk_snapshot(self):
        events = [
            {
                "chunk_seq": 0,
                "audio_base64": base64.b64encode(b"a").decode(),
                "chunk_audio_offset_sec": 2,
                "alignment": {"segments": [{"text": "你", "start": 0, "end": 0.5}]},
            },
            {
                "chunk_seq": 0,
                "audio_base64": base64.b64encode(b"b").decode(),
                "chunk_audio_offset_sec": 2,
                "alignment": {"segments": [{"text": "你好", "start": 0, "end": 1}]},
            },
        ]

        async def lines():
            for event in events:
                yield ("data: " + json.dumps(event, ensure_ascii=False) + "\n").encode()
                yield b"\n"

        audio, alignment = await fish_stream(SimpleNamespace(content=lines()))
        self.assertEqual(audio, b"ab")
        self.assertEqual(alignment, [{"text": "你好", "start": 2.0, "end": 3.0}])

    def test_connected_schema_required_parameters_are_not_silently_omitted(self):
        adapter = FishMCP("fixture")
        adapter.tools = {
            "create_voice_clone": {
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "audio_urls": {"type": "array"}},
                    "required": ["name", "audio_urls"],
                }
            }
        }
        with self.assertRaises(ProviderError):
            adapter.compile("create_voice_clone", {"name": "voice"})
        arguments = {
            "name": "voice",
            **adapter.recordings(
                "create_voice_clone", [{"url": "https://fixture.invalid/actual.wav"}]
            ),
        }
        self.assertEqual(
            adapter.compile("create_voice_clone", arguments)["audio_urls"],
            ["https://fixture.invalid/actual.wav"],
        )


class MCPCompletion(unittest.IsolatedAsyncioTestCase):
    async def test_sse_matching_response_closes_without_waiting_for_eof(self):
        import asyncio
        from unittest.mock import patch

        class Response:
            status = 200
            content_type = "text/event-stream"
            headers = {}
            closed = False

            @property
            def content(self):
                async def lines():
                    yield b'data: {"jsonrpc":"2.0","id":"request","result":{"ok":true}}\n'
                    yield b"\n"
                    await asyncio.Event().wait()

                return lines()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

        response = Response()

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def post(self, *args, **kwargs):
                return response

        with (
            patch("tianshu.capabilities.speech.mcp.aiohttp.ClientSession", return_value=Session()),
            patch("tianshu.capabilities.speech.mcp.secrets.token_hex", return_value="request"),
        ):
            result = await asyncio.wait_for(FishMCP("fixture").rpc("tools/call"), 0.2)
        self.assertEqual(result, {"ok": True})
        self.assertTrue(response.closed)

    async def test_expired_refresh_lease_does_not_repeat_rotating_token_request(self):
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock
        from tianshu.capabilities.speech.mcp import OAuth
        from tianshu.capabilities.contracts import Fault

        async def execute(sql, args=None):
            result = (
                {"state": "refreshing", "expired": True} if sql.startswith("SELECT state") else None
            )
            return SimpleNamespace(fetchone=AsyncMock(return_value=result))

        @asynccontextmanager
        async def transaction(**kwargs):
            yield SimpleNamespace(execute=execute)

        configuration = SimpleNamespace(
            get=AsyncMock(return_value={"id": "connection", "enabled": True}),
            secret=AsyncMock(
                return_value={"expires_at": 0, "refresh_token": "fixture", "access_token": "old"}
            ),
        )
        oauth = OAuth(SimpleNamespace(transaction=transaction), configuration)
        oauth.refresh_tokens = AsyncMock()
        with self.assertRaises(Fault) as raised:
            await oauth.token({"id": "connection"})
        self.assertEqual(raised.exception.code, "speech_oauth_refresh_unknown_reconnect")
        oauth.refresh_tokens.assert_not_awaited()
