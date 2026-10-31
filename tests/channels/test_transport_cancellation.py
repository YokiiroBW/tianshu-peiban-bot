"""Cancellation closes real pending I/O; reconnection does not resubmit identities."""

import asyncio
import unittest
from unittest.mock import AsyncMock
from tianshu.channels.transports import OneBotTransport


class Socket:
    closed = False

    def __init__(self):
        self.sent = asyncio.Event()
        self.writes = []

    async def send_json(self, body):
        self.writes.append(body)
        self.sent.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class TransportCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_ws_disconnect_keeps_original_unknown_without_second_send(self):
        socket = Socket()
        transport = OneBotTransport(
            {"id": "c", "transport": "reverse_ws"},
            "synthetic",
            on_event=AsyncMock(),
            on_response=AsyncMock(),
        )
        transport.sockets["API"] = socket
        submitted = AsyncMock()
        task = asyncio.create_task(transport.call("original-call", "send_group_msg", {}, submitted))
        await socket.sent.wait()
        await transport.attach(socket, "API")
        result = await task
        self.assertEqual(result.state, "unknown")
        self.assertEqual(len(socket.writes), 1)
        self.assertEqual(socket.writes[0]["echo"], "original-call")
        self.assertFalse(transport.pending)
        submitted.assert_awaited_once()

    async def test_http_cancel_exits_actual_request_context(self):
        entered, closed = asyncio.Event(), asyncio.Event()

        class Request:
            async def __aenter__(self):
                entered.set()
                await asyncio.Event().wait()

            async def __aexit__(self, *args):
                closed.set()

        # Cancelling while __aenter__ waits cannot call __aexit__; the actual
        # underlying await is cancelled and cannot later return a fake receipt.
        requests = []

        class Session:
            def post(self, *args, **kwargs):
                requests.append((args, kwargs))
                return Request()

        transport = OneBotTransport(
            {"id": "c", "transport": "http", "endpoint": "https://synthetic.invalid"},
            "synthetic",
            on_event=AsyncMock(),
            on_response=AsyncMock(),
        )
        transport.session = Session()
        task = asyncio.create_task(transport.call("original", "send_group_msg", {}, AsyncMock()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(requests), 1)
