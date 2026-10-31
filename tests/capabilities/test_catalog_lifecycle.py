"""Regression cases staged for the integrated runner; never invoke live providers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.catalog import Catalog, CapabilityDefinition, Availability
from tianshu.capabilities.media.books.reading import ReadingAccess
from tianshu.capabilities.withdrawal import redacted
from tianshu.types import Receipt


class CatalogLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_single_catalog_refreshes_and_does_not_expose_another_actor(self):
        catalog = Catalog()

        def handler(identifier, actor):
            return SimpleNamespace(
                definition=CapabilityDefinition(
                    identifier, identifier, "", {"type": "object"}, {"type": "object"}
                ),
                visible_actor_id=actor,
                availability=AsyncMock(return_value=Availability(True, True)),
            )

        catalog.register(handler("plugin:old", "other"))

        async def refresh(context):
            catalog.handlers["plugin:new"] = handler("plugin:new", context.actor_id)

        catalog.refresher = refresh
        rows = await catalog.discover(SimpleNamespace(actor_id="selected", permissions=frozenset()))
        self.assertEqual([x["definition"].id for x in rows], ["plugin:new"])

    async def test_early_reading_reference_becomes_bound_without_replacing_port(self):
        access = ReadingAccess(None, SimpleNamespace(check=AsyncMock()), None)
        reference = access
        reading = object()
        access.bind(reading)
        self.assertIs(reference.reading, reading)

    async def test_revocation_keeps_effect_and_usage_without_private_result(self):
        receipt = Receipt(
            "r",
            "a",
            "t",
            "succeeded",
            "confirmed",
            "not_requested",
            result={"text": "private", "usage": {"tokens": 12}, "scene": {"private": True}},
        )
        cleared = redacted(receipt)
        self.assertEqual(cleared.effect, "confirmed")
        self.assertEqual(cleared.result["usage"], {"tokens": 12})
        self.assertNotIn("text", cleared.result)
        self.assertNotIn("scene", cleared.result)
