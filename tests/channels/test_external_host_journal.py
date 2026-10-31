"""External SDK journal is durable across restart and never blindly resends."""

import tempfile
import unittest
from pathlib import Path
from tianshu_plugin.channel_bridge.journal import Journal
from tianshu_plugin.channel_bridge.host import Host, PROTOCOL


class ExternalHostJournal(unittest.IsolatedAsyncioTestCase):
    def test_restart_unknown_and_conflicting_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.sqlite3"
            journal = Journal(path)
            self.assertTrue(journal.begin("original", {"text": "private"})["fresh"])
            journal.close()
            journal = Journal(path)
            self.assertEqual(journal.get("original")["state"], "unknown")
            self.assertFalse(journal.begin("original", {"text": "private"})["fresh"])
            with self.assertRaises(ValueError):
                journal.begin("original", {"text": "changed"})
            journal.close()

    async def test_same_call_executes_once_and_ack_retains_only_actual_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            async def sdk(name, params):
                calls.append((name, params))
                return {"message_id": 17, "private": "do not retain after core ack"}

            host = Host(
                journal_path=Path(directory) / "host.sqlite3",
                instance_id="host",
                self_id="22",
                token="synthetic",
                core_url="http://synthetic.invalid",
                connection_id="c",
                sdk_call=sdk,
                framework="fixture",
                version="fixture",
            )
            body = {
                "protocol": PROTOCOL,
                "host_instance_id": "host",
                "self_id": "22",
                "call_id": "original",
                "api_name": "send_group_msg",
                "params": {"group_id": "33", "message": "private"},
            }
            first = await host.call(body)
            second = await host.call(body)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first["state"], "confirmed")
            self.assertEqual(second["response"]["data"]["message_id"], 17)
            await host.acknowledge("original")
            retained = (await host.status("original"))["response"]["data"]
            self.assertEqual(retained, {"message_id": 17})
            host.journal.close()
