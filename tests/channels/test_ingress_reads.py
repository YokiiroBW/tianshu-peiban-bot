"""Internal input expansion retains its human original and observation-only audit."""

import json
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.channels.ingress import process_event
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, Receipt, SourceRef, TrustedContext


class IngressReads(unittest.IsolatedAsyncioTestCase):
    async def test_reply_and_forward_lookups_do_not_relabel_the_admitted_input(self):
        context = TrustedContext(
            "synthetic-principal", "synthetic-actor", "synthetic-person", "synthetic-chat",
            ("synthetic-scope",), ("synthetic-scope",), 3, OriginKind.HUMAN,
            frozenset({"conversation.read"}), conversation_kind="group",
        )
        channel = {
            "id": "synthetic-qq", "namespace": "qq", "adapter": "onebot",
            "self_external_id": "12345", "enabled": True,
        }
        raw = {
            "time": 1793440000, "self_id": 12345, "post_type": "message",
            "message_type": "group", "message_id": 8, "user_id": 77, "group_id": 22,
            "sender": {"user_id": 77},
            "message": [
                {"type": "reply", "data": {"id": 2}},
                {"type": "forward", "data": {"id": "synthetic-forward"}},
                {"type": "text", "data": {"text": "这是这次实际收到的消息。"}},
            ],
        }
        forward = {"messages": [{"sender": {"user_id": 88, "nickname": "引用作者"},
                                 "content": [{"type": "text", "data": {"text": "展开的原文。"}}]}]}
        row = {
            "id": "synthetic-event", "connection_id": channel["id"], "state": "pending",
            "payload": "synthetic-envelope", "outcome": None, "superseded": False,
            "event_id": "message:group:8", "created_at": datetime.now(UTC),
        }
        cursor = SimpleNamespace(fetchone=AsyncMock(return_value=row))
        connection = SimpleNamespace(execute=AsyncMock(return_value=cursor))

        @asynccontextmanager
        async def transaction(**_kwargs):
            yield connection

        actions, receipts = [], []

        async def invoke(action, *, background):
            actions.append(action)
            data = forward if action.arguments["api_name"] == "get_forward_msg" else {
                "message_id": 2, "group_id": 22, "sender": {"user_id": 88},
            }
            actual = Receipt("receipt:" + action.action_id, action.action_id, "synthetic-attempt",
                             "succeeded", "none", "not_requested", result={"data": data})
            receipts.append(actual)
            return actual

        original = SourceRef("synthetic-forward-original", 1)
        service = SimpleNamespace(
            runtime=SimpleNamespace(database=SimpleNamespace(transaction=transaction)),
            repository=SimpleNamespace(
                connection=AsyncMock(return_value=channel),
                unseal=lambda *_args: {"raw": raw}, bind_event_sources=AsyncMock(),
            ),
            identity=SimpleNamespace(admit_event=AsyncMock(return_value=SimpleNamespace(
                context=context, disposition="observe", duplicate=False, reply_owner="tianshu",
                mentioned_person_ids=(), original_author_person_id="quoted-person",
                reply_to_event_id="message:group:2",
            ))),
            actions=SimpleNamespace(find=AsyncMock(return_value=None),
                                    invoke=AsyncMock(side_effect=invoke)),
            sources=SimpleNamespace(capture=AsyncMock(return_value=original),
                                    validate_persistence=AsyncMock(return_value=True)),
            conversation=SimpleNamespace(ingest=AsyncMock(return_value={})),
        )
        await process_event(service, row["id"])

        self.assertEqual([action.arguments["api_name"] for action in actions],
                         ["get_msg", "get_forward_msg"])
        for action in actions:
            self.assertEqual(action.origin_kind, OriginKind.OBSERVATION)
            self.assertEqual(action.context, replace(context, origin_kind=OriginKind.OBSERVATION))
        admitted_context, message = service.conversation.ingest.await_args.args
        self.assertIs(admitted_context, context)
        self.assertEqual(context.origin_kind, OriginKind.HUMAN)
        self.assertEqual([part.kind for part in message.parts], ["reply", "forward", "quote", "text"])
        self.assertEqual(message.parts[0].metadata["original_message_id"], "2")
        self.assertEqual(message.parts[2].text, "展开的原文。")
        self.assertEqual(message.parts[2].metadata["original_author_external_id"], "88")
        self.assertEqual(message.source_refs, (original,))
        capture = service.sources.capture.await_args
        self.assertIs(capture.args[0], context)
        self.assertEqual(json.loads(capture.kwargs["raw"]), forward)
        self.assertEqual(capture.kwargs["locator"]["kind"], "channel_forward")

        # A retry can encounter an original read whose authority context still
        # says human. Read its same receipt; do not mutate or resubmit that action.
        originals = [{"action": wire(replace(action, context=context)), "receipt": wire(actual)}
                     for action, actual in zip(actions, receipts, strict=True)]
        service.actions.find.side_effect = originals
        service.actions.invoke.reset_mock()
        await process_event(service, row["id"])
        service.actions.invoke.assert_not_awaited()
        self.assertEqual(len(actions), 2)
        restored_context, restored = service.conversation.ingest.await_args.args
        self.assertIs(restored_context, context)
        self.assertEqual(restored.parts, message.parts)
        self.assertEqual(restored.source_refs, message.source_refs)

        service.sources.capture.reset_mock()
        service.actions.find.side_effect = [
            {**entry, "receipt": wire(replace(actual, execution="unknown", effect="unknown",
                                             error_code="synthetic_lookup_unknown"))}
            for entry, actual in zip(originals, receipts, strict=True)
        ]
        await process_event(service, row["id"])
        service.actions.invoke.assert_not_awaited()
        service.sources.capture.assert_not_awaited()
        uncertain = service.conversation.ingest.await_args.args[1]
        self.assertEqual(uncertain.parts[0].metadata["lookup_unavailable"], "synthetic_lookup_unknown")
        self.assertEqual(uncertain.parts[1].metadata["lookup_unavailable"], "synthetic_lookup_unknown")
        self.assertFalse(uncertain.source_refs)
