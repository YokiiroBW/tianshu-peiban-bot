import asyncio
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "probe_trace", Path(__file__).with_name("linux_platform_probe_trace.py")
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class ProbeTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_probe_timeout_worker_and_cancel_preserve_original_semantics(
        self,
    ):
        from services.platform.runtime_health import HealthInputs, Probe

        entered, release = threading.Event(), threading.Event()

        def backend():
            entered.set()
            release.wait(2)
            return "ok"

        original = Probe.ready
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            trace = tool.Trace(Probe, directory)
            trace.install()
            try:
                probe = Probe(
                    HealthInputs(mode="local", backend_check=backend), budget=0.02
                )
                answer = await probe.ready()
                self.assertTrue(entered.is_set())
                self.assertEqual(set(answer["checks"].values()), {"not_verified"})
                self.assertTrue(probe._inflight)
                self.assertEqual(await probe.ready(), answer)
                release.set()
                for _ in range(100):
                    if not probe._inflight:
                        break
                    await asyncio.sleep(0.002)
                self.assertFalse(probe._inflight)
                records = [
                    json.loads(line)
                    for line in (directory / "phases.jsonl").read_text().splitlines()
                ]
                self.assertTrue(
                    any(
                        row["phase"] == "_store" and row["event"] == "end"
                        for row in records
                    )
                )
                self.assertTrue(
                    any(
                        row["phase"] == "_settle" and row["event"] == "end"
                        for row in records
                    )
                )
                self.assertGreater(len({row["thread"] for row in records}), 1)
                self.assertNotIn("backend", (directory / "phases.jsonl").read_text())
            finally:
                release.set()
                trace.close()
        self.assertIs(Probe.ready, original)

    async def test_original_async_exception_is_not_replaced(self):
        class Fake:
            _inflight = False

            async def ready(self):
                raise asyncio.CancelledError("not-recorded-private-text")

        for phase in tool.PHASES:
            setattr(Fake, phase, lambda self: None)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            trace = tool.Trace(Fake, directory)
            trace.install()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await Fake().ready()
                contents = (directory / "phases.jsonl").read_text()
                self.assertIn("CancelledError", contents)
                self.assertNotIn("not-recorded-private-text", contents)
            finally:
                trace.close()


if __name__ == "__main__":
    unittest.main()
