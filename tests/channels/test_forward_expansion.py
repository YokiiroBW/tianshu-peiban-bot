"""Partial forward content never becomes an all-or-nothing synthetic reading."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.channels.contracts import ChannelError
from tianshu.channels.forward import expand_forward
from tianshu.channels.ingress import verified_parts
from tianshu.conversation.types import InputPart
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef


CONNECTION = {"id": "synthetic-qq", "namespace": "qq"}
ORIGINAL = SourceRef("synthetic-forward-original", 1)


def forward(identifier="opaque+/forward-id_="):
    return InputPart("forward", metadata={"channel": "onebot.v11",
        "segment": {"type": "forward", "data": {"id": identifier}}})


def node(text="实际引用正文", *, content=None):
    return {"sender": {"user_id": 88, "nickname": "引用作者"}, "content": content
        if content is not None else [{"type": "text", "data": {"text": text}}]}


class ForwardExpansion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = object()
        self.service = SimpleNamespace(sources=SimpleNamespace(capture=AsyncMock(return_value=ORIGINAL)),
            media=SimpleNamespace(receive=AsyncMock()))

    async def expand(self, raw, *, original=None, **kwargs):
        api = AsyncMock(return_value=raw)
        part = original or forward()
        parts, refs = await expand_forward(self.service, self.context, CONNECTION, part, api,
            "message:group:8", event_revision=3, **kwargs)
        return parts, refs, api

    async def test_bad_middle_node_keeps_both_quotes_and_the_original_forward_identity(self):
        raw = {"messages": [node("前一条正文"), None, node("后一条正文")]}
        original = forward()
        parts, refs, api = await self.expand(raw, original=original)
        self.assertEqual([part.kind for part in parts], ["forward", "quote", "reference", "quote"])
        self.assertEqual([part.text for part in parts if part.kind == "quote"], ["前一条正文", "后一条正文"])
        self.assertEqual(parts[0].metadata["segment"], original.metadata["segment"])
        self.assertEqual(parts[0].metadata["node_count"], 3)
        self.assertEqual(parts[0].metadata["missing"], [{"node_index": 1, "reason": "forward_node_format"}])
        self.assertEqual(parts[2].metadata["lookup_unavailable"], "forward_node_format")
        self.assertEqual(refs, [ORIGINAL])
        self.assertTrue(all(part.metadata["forward_source_ref"] == wire(ORIGINAL) for part in parts))
        api.assert_awaited_once_with("get_forward_msg", {"id": original.metadata["segment"]["data"]["id"]})
        captured = self.service.sources.capture.await_args
        self.assertIs(captured.args[0], self.context)
        self.assertEqual(json.loads(captured.kwargs["raw"]), raw)
        self.assertEqual(captured.kwargs["locator"]["revision"], 3)
        admitted = verified_parts(parts, SimpleNamespace(mentioned_person_ids=("admitted-person",),
            original_author_person_id="admitted-author", reply_to_event_id=None))
        self.assertNotIn("verified_original_author_person_id", admitted[1].metadata)
        self.assertNotIn("verified_subject_ids", admitted[1].metadata)

    async def test_failed_media_does_not_discard_quotes_before_or_after_it(self):
        for returned in (False, True):
            with self.subTest(media_returns_unavailable=returned):
                async def unavailable(_context, _connection, part, *_args, **_kwargs):
                    if not returned:
                        raise ChannelError("provider_media_unavailable", 502)
                    return InputPart(part.kind, metadata={**part.metadata, "original_pending": False,
                        "media_unavailable": True, "error_code": "provider_media_unavailable"}), None
                self.service.media.receive.side_effect = unavailable
                raw = {"messages": [node(content=[{"type": "text", "data": {"text": "保留前文"}},
                    {"type": "image", "data": {"file": "synthetic-image"}},
                    {"type": "text", "data": {"text": "保留后文"}}])]}
                parts, refs, api = await self.expand(raw)
                self.assertEqual([part.text for part in parts if part.kind == "quote"], ["保留前文", "保留后文"])
                self.assertEqual(parts[0].metadata["missing"], [{"node_index": 0, "part_index": 1,
                    "reason": "provider_media_unavailable"}])
                self.assertTrue(parts[2].metadata["media_unavailable"])
                self.assertFalse(parts[2].metadata["original_pending"])
                self.assertIsNone(parts[2].media)
                self.assertEqual(refs, [ORIGINAL])
                api.assert_awaited_once()

    async def test_nested_rejected_read_keeps_its_id_and_other_actual_text(self):
        raw = {"messages": [node(content=[{"type": "text", "data": {"text": "嵌套之前"}},
            {"type": "forward", "data": {"id": "nested-unavailable-id"}},
            {"type": "text", "data": {"text": "嵌套之后"}}])]}
        api = AsyncMock(side_effect=[raw, ChannelError("onebot_api_failed", 503)])
        parts, refs = await expand_forward(self.service, self.context, CONNECTION, forward("root-id"), api,
            "message:group:8")
        self.assertEqual([part.text for part in parts if part.kind == "quote"], ["嵌套之前", "嵌套之后"])
        nested = parts[2]
        self.assertEqual(nested.metadata["segment"]["data"]["id"], "nested-unavailable-id")
        self.assertEqual(nested.metadata["lookup_unavailable"], "onebot_api_failed")
        self.assertFalse(nested.metadata["analysis_completed"])
        self.assertEqual(parts[0].metadata["missing"], [{"node_index": 0, "part_index": 1,
            "reason": "forward_nested_incomplete"}])
        self.assertEqual([call.args for call in api.await_args_list], [
            ("get_forward_msg", {"id": "root-id"}), ("get_forward_msg", {"id": "nested-unavailable-id"})])
        self.assertEqual(refs, [ORIGINAL])
        self.service.sources.capture.assert_awaited_once()

    async def test_confirmed_empty_or_malformed_body_is_missing_not_a_fabricated_quote(self):
        for raw, reason in ((None, "forward_response_format"), ({"messages": None}, "forward_nodes_format"),
                            ({"messages": []}, "forward_empty_body")):
            with self.subTest(raw=raw):
                original = forward()
                parts, refs, api = await self.expand(raw, original=original)
                self.assertEqual(len(parts), 1)
                self.assertEqual(parts[0].metadata["segment"], original.metadata["segment"])
                self.assertEqual(parts[0].metadata["lookup_unavailable"], reason)
                self.assertFalse(parts[0].metadata["analysis_completed"])
                self.assertEqual(parts[0].metadata["forward_source_ref"], wire(ORIGINAL))
                self.assertIsNone(parts[0].text)
                self.assertEqual(refs, [ORIGINAL])
                api.assert_awaited_once()

    async def test_part_node_and_depth_limits_remain_explicit_without_another_read(self):
        parts, refs, api = await self.expand({"messages": [node(str(index)) for index in range(130)]}, max_parts=4)
        self.assertEqual(len(parts), 4)
        self.assertEqual([part.text for part in parts[1:]], ["0", "1", "2"])
        self.assertEqual(parts[0].metadata["node_count"], 130)
        self.assertEqual(parts[0].metadata["omitted"], "forward_part_budget")
        self.assertEqual(parts[0].metadata["missing"], [{"node_index": 128, "reason": "forward_node_budget"},
            {"node_index": 3, "reason": "forward_part_budget"}])
        self.assertEqual(refs, [ORIGINAL])
        api.assert_awaited_once()
        self.service.sources.capture.reset_mock()
        parts, refs, api = await self.expand({}, depth=3)
        self.assertEqual(parts[0].metadata["lookup_unavailable"], "forward_depth_budget")
        self.assertEqual(parts[0].metadata["segment"], forward().metadata["segment"])
        self.assertFalse(refs)
        api.assert_not_awaited()
        self.service.sources.capture.assert_not_awaited()

    async def test_cancelled_lookup_or_media_expansion_never_becomes_an_unavailable_completion(self):
        for media in (False, True):
            with self.subTest(during_media=media):
                api = AsyncMock(return_value={"messages": [node(content=[
                    {"type": "image", "data": {"file": "synthetic-image"}}])]})
                if media:
                    self.service.media.receive.side_effect = asyncio.CancelledError()
                else:
                    api.side_effect = asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError):
                    await expand_forward(self.service, self.context, CONNECTION, forward(), api, "message:group:8")
