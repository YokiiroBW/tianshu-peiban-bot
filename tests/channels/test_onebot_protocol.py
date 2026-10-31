"""Frozen v11 native semantics for the integrated runner. No live channel."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.channels.contracts import ChannelError
from tianshu.channels.onebot.registry import APIS, SEGMENTS, validate_call
from tianshu.channels.onebot.messages import normalize_event, validate_outbound, mentions_all
from tianshu.channels.transports import onebot_result
from tianshu.channels.delivery import compile_calls
from tianshu.channels.service import Channels
from tianshu.channels.contracts import TransportResult

CONNECTION = {
    "id": "qq-main",
    "namespace": "qq",
    "adapter": "onebot",
    "provider": "generic",
    "self_external_id": "12345",
    "allow_at_all": False,
}


class OneBotProtocol(unittest.IsolatedAsyncioTestCase):
    def test_full_fixed_catalog_and_provider_extension_boundary(self):
        self.assertEqual(len(SEGMENTS), 23)
        self.assertEqual(len(APIS), 38)
        self.assertEqual(len({entry.name for entry in APIS}), 38)
        validate_call(".handle_quick_operation", {"context": {}, "operation": {}}, CONNECTION)
        with self.assertRaises(ChannelError):
            validate_call("get_group_notice", {"group_id": "22"}, CONNECTION)
        _, params = validate_call(
            "set_group_ban_async", {"group_id": 22, "user_id": 33}, CONNECTION
        )
        self.assertEqual(params["group_id"], "22")

    def test_order_unknown_parts_author_and_references_are_preserved(self):
        event, parts = normalize_event(
            CONNECTION,
            {
                "time": 1793440000,
                "self_id": 12345,
                "post_type": "message",
                "message_type": "group",
                "message_id": -8,
                "user_id": 77,
                "group_id": 22,
                "sender": {"user_id": 77},
                "message": [
                    {"type": "text", "data": {"text": "A"}},
                    {"type": "future", "data": {"opaque": 1}},
                    {"type": "at", "data": {"qq": "all"}},
                    {"type": "reply", "data": {"id": -2}},
                    {"type": "text", "data": {"text": "B"}},
                ],
            },
        )
        self.assertEqual(
            [part.kind for part in parts], ["text", "reference", "mention_all", "reply", "text"]
        )
        self.assertEqual(event.producer, "qq:qq-main")
        self.assertEqual(event.event_id, "message:group:-8")
        self.assertEqual(event.reply_to_event_id, "message:group:-2")
        self.assertIn("12345", event.mentioned_external_ids)
        self.assertEqual(event.original_author_external_id, "77")

    def test_notices_are_not_human_input_and_account_mismatch_is_rejected(self):
        event, parts = normalize_event(
            CONNECTION,
            {
                "time": 1793440000,
                "self_id": 12345,
                "post_type": "notice",
                "notice_type": "group_recall",
                "group_id": 22,
                "user_id": 77,
                "message_id": 3,
            },
        )
        self.assertFalse(parts)
        self.assertTrue(event.archived)
        self.assertEqual(event.reply_owner, "none")
        with self.assertRaises(ChannelError):
            normalize_event(CONNECTION, {"self_id": 999})

    async def test_plain_text_cannot_inject_cq_segments(self):
        calls = await compile_calls(
            SimpleNamespace(),
            None,
            {"target_ref": "group:22", "adapter": "qq"},
            CONNECTION,
            [{"kind": "text", "text": "[CQ:at,qq=all]"}],
        )
        self.assertEqual(
            calls,
            [
                (
                    "send_group_msg",
                    {
                        "group_id": "22",
                        "message": [{"type": "text", "data": {"text": "[CQ:at,qq=all]"}}],
                    },
                )
            ],
        )

    def test_async_acceptance_is_not_delivery_and_malformed_status_is_unknown(self):
        self.assertEqual(onebot_result({"status": "async", "retcode": 1}).state, "accepted")
        self.assertEqual(onebot_result({"status": "ok", "retcode": 1}).state, "accepted")
        self.assertEqual(onebot_result({"status": "failed", "retcode": 0}).state, "unknown")
        self.assertEqual(
            onebot_result({"status": "ok", "retcode": 0, "data": {"message_id": -4}}).message_ids,
            ("-4",),
        )
        self.assertEqual(
            Channels.checked_identity(
                CONNECTION, TransportResult("confirmed", {"user_id": 999})
            ).state,
            "rejected",
        )

    def test_nested_at_all_cannot_skip_configuration(self):
        node = [
            {
                "type": "node",
                "data": {
                    "user_id": "77",
                    "nickname": "x",
                    "content": [{"type": "at", "data": {"qq": "all"}}],
                },
            }
        ]
        self.assertTrue(mentions_all(node))
        with self.assertRaises(ChannelError):
            validate_outbound(node, CONNECTION)
