import asyncio
import json
import threading
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from deploy.observability.query import QueryError
from deploy.single_container.flow_reader import FlowEventReader


def line(stamp, correlation="a" * 32):
    return json.dumps(
        {
            "timestamp": datetime.fromtimestamp(stamp / 1e9, timezone.utc).isoformat(),
            "correlation_id": correlation,
        }
    )


class QueryClient:
    def __init__(self, values=()):
        self.values = list(values)
        self.calls = []
        self.closed = False

    def request(self, path, *, deadline):
        self.calls.append(path)
        deadline.remaining()
        body = {
            "status": "success",
            "data": {"resultType": "streams", "result": [{"values": self.values}]},
        }
        return 200, json.dumps(body).encode(), "application/json"

    def close(self):
        self.closed = True


class FlowReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_query_and_exact_correlation_with_retention_guard(self):
        end = time.time_ns()
        client = QueryClient([(str(end), line(end - 1_000_000))])
        reader = FlowEventReader(client)
        try:
            result = await reader(
                correlation_id="a" * 32, start_ns=end - 10**9, end_ns=end, limit=20
            )
            self.assertEqual(len(result["events"]), 1)
            self.assertTrue(result["complete"])
            query = parse_qs(urlsplit(client.calls[0]).query)
            self.assertIn('stack="tianshu"', query["query"][0])
            self.assertIn('correlation_id="' + "a" * 32 + '"', query["query"][0])
            self.assertEqual(query["limit"], ["21"])
            self.assertEqual(query["direction"], ["backward"])
            self.assertGreaterEqual(int(query["end"][0]), end)
        finally:
            await reader.aclose()
        self.assertTrue(client.closed)

    async def test_late_backlog_is_filtered_by_original_event_time(self):
        end = time.time_ns()
        client = QueryClient(
            [
                (str(end), line(end - 3600 * 10**9)),
                (str(end - 1), line(end - 1_000_000)),
            ]
        )
        reader = FlowEventReader(client)
        try:
            result = await reader(
                correlation_id=None, start_ns=end - 10**9, end_ns=end, limit=20
            )
            self.assertEqual(len(result["events"]), 1)
            self.assertTrue(result["complete"])
        finally:
            await reader.aclose()

    async def test_saturated_result_is_explicitly_partial(self):
        end = time.time_ns()
        client = QueryClient([(str(end), line(end - 1_000_000))] * 3)
        reader = FlowEventReader(client)
        try:
            result = await reader(
                correlation_id=None, start_ns=end - 10**9, end_ns=end, limit=2
            )
            self.assertFalse(result["complete"])
            self.assertEqual(len(result["events"]), 2)
        finally:
            await reader.aclose()

    async def test_browser_cannot_supply_selector_or_unbounded_window(self):
        end = time.time_ns()
        client = QueryClient()
        reader = FlowEventReader(client)
        try:
            for overrides in (
                {"correlation_id": 'a"} |= "secret'},
                {"start_ns": 0},
                {"start_ns": end - 25 * 3600 * 10**9},
                {"limit": 2001},
                {"limit": True},
            ):
                query = dict(
                    correlation_id=None, start_ns=end - 10**9, end_ns=end, limit=20
                )
                query.update(overrides)
                with self.assertRaises(ValueError):
                    await reader(**query)
            self.assertFalse(client.calls)
        finally:
            await reader.aclose()

    async def test_cancelled_reader_retains_real_worker_capacity_and_close_drains(self):
        class BlockedClient(QueryClient):
            def __init__(self):
                super().__init__()
                self.release = threading.Event()
                self.lock = threading.Lock()

            def request(self, path, *, deadline):
                with self.lock:
                    self.calls.append(path)
                if not self.release.wait(3):
                    raise QueryError("test_worker_wait_expired")
                return (
                    200,
                    b'{"status":"success","data":{"resultType":"streams","result":[]}}',
                    "application/json",
                )

            def close(self):
                super().close()
                self.release.set()

        end = time.time_ns()
        client = BlockedClient()
        reader = FlowEventReader(client)
        query = dict(correlation_id=None, start_ns=end - 10**9, end_ns=end, limit=20)
        tasks = [asyncio.create_task(reader(**query)) for _ in range(4)]
        try:
            async with asyncio.timeout(2):
                while len(client.calls) < 2:
                    await asyncio.sleep(0.005)
            with self.assertRaises(QueryError):
                await reader(**query)
            tasks[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await tasks[0]
            await asyncio.sleep(0)
            self.assertEqual(len(client.calls), 2)
            await reader.aclose()
            self.assertEqual(reader.work.active, 0)
        finally:
            await reader.aclose()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
