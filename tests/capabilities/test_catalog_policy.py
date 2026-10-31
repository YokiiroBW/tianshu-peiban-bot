"""The same actor policy governs discovery and actual invocation."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.types import Action, OriginKind, TrustedContext
from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Catalog, CapabilityDefinition, Availability
from tianshu.capabilities.catalog_configuration import CatalogConfiguration
from tianshu.capabilities.contracts import Fault


class CatalogPolicy(unittest.IsolatedAsyncioTestCase):
    def setup_catalog(self):
        catalog = Catalog()
        definition = CapabilityDefinition(
            "memory.search",
            "真实记忆",
            "trusted",
            {"type": "object"},
            {"type": "object"},
            model_tool={"name": "memory_search"},
        )
        handler = SimpleNamespace(
            definition=definition,
            availability=AsyncMock(return_value=Availability(True, True)),
            execute=AsyncMock(),
        )
        catalog.register(handler)
        configuration = SimpleNamespace(
            get=AsyncMock(
                return_value={"revision": 2, "capabilities": {"memory.search": {"enabled": False}}}
            )
        )
        settings = CatalogConfiguration(catalog, configuration, None, None, None)
        settings.configured_sources = AsyncMock(return_value=[])
        catalog.configuration = settings
        context = TrustedContext(
            "u",
            "actor",
            "person",
            "chat",
            ("scope",),
            ("person",),
            1,
            OriginKind.HUMAN,
            frozenset({"*"}),
        )
        return catalog, settings, handler, context

    async def test_disabled_catalog_entry_cannot_be_invoked_directly(self):
        catalog, settings, handler, context = self.setup_catalog()
        rows = await catalog.discover(context)
        self.assertFalse(rows[0]["can_execute"])
        actions = Actions(None, None, catalog, SimpleNamespace(check=AsyncMock()))
        with self.assertRaises(Fault) as failure:
            await actions.invoke(
                Action("a", "i", "memory.search", "actor", context, {}, OriginKind.HUMAN)
            )
        self.assertEqual(failure.exception.code, "capability_disabled")
        handler.execute.assert_not_awaited()

    async def test_data_manifest_cannot_replace_native_schema_or_install_handler(self):
        catalog, settings, handler, context = self.setup_catalog()
        settings.configured_sources = AsyncMock(
            return_value=[
                {
                    "source_id": "remote",
                    "enabled": True,
                    "entries": [
                        {
                            "id": "memory.search",
                            "title": "untrusted override",
                            "input_schema": {"type": "array"},
                        },
                        {"id": "uninstalled.tool", "title": "缺失适配器"},
                    ],
                }
            ]
        )
        rows = await catalog.discover(context)
        self.assertIs(rows[0]["definition"], handler.definition)
        missing = next(row for row in rows if row["definition"].id == "uninstalled.tool")
        self.assertFalse(missing["availability"].implemented)
        self.assertFalse(missing["can_execute"])
        with self.assertRaises(Fault) as failure:
            catalog.handler("uninstalled.tool")
        self.assertEqual(failure.exception.code, "adapter_not_installed")
