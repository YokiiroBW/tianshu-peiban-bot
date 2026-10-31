"""AssetLink metadata boundaries remain intact after the native PG migration."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.resources import asset_queries as query
from tianshu.capabilities.resources.service import Resources
from tianshu.capabilities.contracts import Fault


class AssetResources(unittest.IsolatedAsyncioTestCase):
    def test_cursor_is_bound_to_identity_connection_and_exact_query(self):
        scope = query.Scope("principal-a", "asset-one", "browse")
        encoded = scope.encode("upstream-cursor", "options-a")
        self.assertEqual(scope.decode(encoded, "options-a"), "upstream-cursor")
        for other in (
            query.Scope("principal-b", "asset-one", "browse"),
            query.Scope("principal-a", "asset-two", "browse"),
        ):
            with self.assertRaises(Fault):
                other.decode(encoded, "options-a")
        with self.assertRaises(Fault):
            scope.decode(encoded, "options-b")

    def test_native_paths_and_metadata_do_not_claim_originals(self):
        for invalid in ("/root", "C:\\private", "folder/../private", "folder\\..\\private"):
            with self.assertRaises(Fault):
                query.relative(invalid)
        value = query.library(
            {
                "library_id": "library",
                "display_name": "Actual",
                "availability": "offline",
                "access_level": "read_only",
            }
        )
        self.assertFalse(value["index_available"])
        self.assertEqual(value["original_available"], "not_verified")
        self.assertFalse(value["preview"]["available"])

    async def test_connection_cannot_borrow_a_deployment_master_reference(self):
        configuration = SimpleNamespace(get=AsyncMock(return_value=None), save=AsyncMock())
        owner = Resources.__new__(Resources)
        owner.configuration = configuration
        context = SimpleNamespace(permissions=frozenset({"*"}))
        with self.assertRaises(Fault) as failure:
            await owner.save(
                context,
                {
                    "id": "asset",
                    "endpoint": "https://asset.invalid/assetlink/v1/control",
                    "credential_ref": "TIANSHU_MASTER_KEY",
                    "expected_revision": 0,
                },
            )
        self.assertEqual(failure.exception.code, "asset_credential_must_be_written")
        configuration.save.assert_not_awaited()
