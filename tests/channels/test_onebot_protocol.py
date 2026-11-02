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
    def test_forward_download_empty_rejection_keeps_its_actual_response(self):
        raw = {"status": "failed", "retcode": 100,
            "wording": "download forward message payload is empty", "data": None}
        result = onebot_result(raw)
        self.assertEqual((result.state, result.retcode, result.error_code),
            ("rejected", 100, "onebot_api_failed"))
        self.assertIs(result.raw, raw)
        self.assertIsNone(result.data)
        self.assertFalse(result.message_ids)

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
                "sender": {"user_id": 77, "nickname": "原昵称", "card": "群名片"},
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
        self.assertEqual(event.sender_display_name, "群名片")

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

    def test_nul_display_and_derived_parts_are_safe_without_mutating_transport(self):
        raw = {"time": 1793440000, "self_id": 12345, "post_type": "message",
               "message_type": "private", "message_id": 81, "user_id": 77,
               "sender": {"card": "群\x00名片", "user_id": 77},
               "message": [{"type": "text", "data": {"text": "保留\x00内容"}},
                           {"type": "json", "data": {"data": "扩展\x00原件"}}]}
        event, parts = normalize_event(CONNECTION, raw)
        self.assertEqual(event.sender_display_name, "群\ufffd名片")
        self.assertEqual(parts[0].text, "保留\ufffd内容")
        self.assertNotIn("\x00", parts[1].text)
        self.assertEqual(raw["sender"]["card"], "群\x00名片")

    async def test_unbound_recall_is_consumed_as_ignored_without_source_access(self):
        from tianshu.channels.ingress import operational_event
        from tianshu.runtime.errors import AppError

        service = SimpleNamespace(repository=SimpleNamespace(observe=AsyncMock()),
            identity=SimpleNamespace(channel_context=AsyncMock(side_effect=AppError("channel_target_forbidden", status=403))),
            sources=SimpleNamespace(recall_event=AsyncMock()))
        outcome = await operational_event(service, CONNECTION,
            {"post_type": "notice", "notice_type": "group_recall", "group_id": 999, "message_id": 22},
            SimpleNamespace(event_id="notice"))
        self.assertEqual(outcome["disposition"], "ignored")
        service.sources.recall_event.assert_not_awaited()

    def test_only_admitted_identities_are_attached_to_top_level_parts(self):
        from tianshu.channels.ingress import verified_parts
        from tianshu.conversation.types import InputPart

        decision = SimpleNamespace(mentioned_person_ids=("person1",), original_author_person_id="person2",
                                   reply_to_event_id="message:group:9")
        parts = verified_parts((InputPart("mention"), InputPart("reply"),
            InputPart("quote", "转述", metadata={"forward_node_index": 1, "original_author_external_id": "99"})), decision)
        self.assertEqual(parts[0].metadata["verified_subject_ids"], ["person1"])
        self.assertEqual(parts[1].metadata["verified_original_author_person_id"], "person2")
        self.assertNotIn("verified_original_author_person_id", parts[2].metadata)

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


class LiveEligibility(unittest.TestCase):
    def test_archives_duplicates_and_host_owned_events_cannot_become_autonomous(self):
        from tianshu.channels.ingress import live_eligible

        event = SimpleNamespace(archived=False)
        decision = SimpleNamespace(duplicate=False, reply_owner="tianshu")
        self.assertTrue(live_eligible(event, decision, {"superseded": False}))
        self.assertFalse(live_eligible(event, decision, {"superseded": True}))
        decision.duplicate = True
        self.assertFalse(live_eligible(event, decision, {"superseded": False}))
        decision.duplicate = False
        decision.reply_owner = "external"
        self.assertFalse(live_eligible(event, decision, {"superseded": False}))
        decision.reply_owner = "tianshu"
        event.archived = True
        self.assertFalse(live_eligible(event, decision, {"superseded": False}))
