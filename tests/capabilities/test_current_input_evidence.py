"""Trusted input provenance cases for the integrated runner; no provider calls."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.types import Action, OriginKind, SourceRef, TrustedContext
from tianshu.runtime.serialization import wire
from tianshu.capabilities.actions import Actions
from tianshu.capabilities.serialization import action_from
from tianshu.capabilities.contracts import Fault


class CurrentInputEvidence(unittest.IsolatedAsyncioTestCase):
    def action(self):
        context = TrustedContext(
            "user",
            "actor",
            "person",
            "chat",
            ("scope",),
            ("person",),
            1,
            OriginKind.HUMAN,
            frozenset({"*"}),
        )
        source = SourceRef("original", 2, {"unit": "text", "start": 4, "end": 8})
        return Action(
            "a",
            "i",
            "memory.matter.save",
            "actor",
            context,
            {},
            OriginKind.HUMAN,
            (source,),
            current_input_refs=(source,),
        )

    def test_codec_keeps_exact_selected_input_and_denies_missing_delivery(self):
        value = wire(self.action())
        value["context"].pop("delivery_allowed", None)
        restored = action_from(value)
        self.assertEqual(restored.current_input_refs, self.action().current_input_refs)
        self.assertFalse(restored.context.delivery_allowed)

    async def test_broader_locator_cannot_be_claimed_as_current_input(self):
        action = self.action()
        handler = SimpleNamespace(
            definition=SimpleNamespace(long_running=False, permissions=frozenset())
        )
        owner = Actions(None, None, None, SimpleNamespace(check=AsyncMock()))
        owner.handler = AsyncMock(return_value=handler)
        with self.assertRaises(Fault) as failure:
            await owner.invoke(replace(action, current_input_refs=(SourceRef("original", 2),)))
        self.assertEqual(failure.exception.code, "current_input_source_not_authorized")
        owner.authority.check.assert_not_awaited()
