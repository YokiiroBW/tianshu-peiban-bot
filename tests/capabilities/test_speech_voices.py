import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tianshu.capabilities.external import ProviderError
from tianshu.capabilities.speech.mcp import FishMCP
from tianshu.capabilities.speech.service import Speech
from tianshu.capabilities.speech.voices import voice_page


def catalog_adapter(properties):
    adapter = FishMCP("synthetic-token")
    adapter.tools = {"search_voices": {"inputSchema": {
        "type": "object", "properties": {"query": {"type": "string"}, **properties},
    }}}
    adapter.initialize = AsyncMock()
    adapter.call = AsyncMock(return_value={"voices": [{"voice_id": "my-voice", "name": "我的声线"}]})
    return adapter


class VoiceCatalog(unittest.TestCase):
    def test_own_catalog_uses_the_connected_self_boolean_and_opaque_cursor(self):
        adapter = catalog_adapter({"self": {"type": "boolean"}, "cursor": {"type": "string"}})
        self.assertEqual(adapter.voice_search_arguments("轻声", owned=True, cursor="next-page"),
                         {"query": "轻声", "self": True, "cursor": "next-page"})

    def test_own_catalog_supports_the_declared_self_mode_and_numbered_pages(self):
        adapter = catalog_adapter({"mode": {"enum": ["search", "popular", "self"]},
                                   "page": {"type": "integer"}})
        self.assertEqual(adapter.voice_search_arguments(owned=True, cursor="20"),
                         {"query": "", "mode": "self", "page": 2})
        self.assertEqual(adapter.voice_search_arguments("轻声")["mode"], "search")

    def test_unsupported_own_filter_never_silently_queries_public_voices(self):
        adapter = catalog_adapter({})
        with self.assertRaises(ProviderError) as error:
            adapter.voice_search_arguments(owned=True)
        self.assertEqual(error.exception.code, "speech_owned_voices_unsupported")

    def test_native_total_yields_next_page_and_stops_at_the_end(self):
        first = voice_page({"total": 21, "items": [{"_id": str(i), "title": "声线"} for i in range(20)]},
                           offset_pagination=True)
        self.assertEqual(first["next_cursor"], "20")
        last = voice_page({"data": {"total": 21, "items": [{"_id": "last", "title": "最后声线"}]}},
                          cursor="20", offset_pagination=True)
        self.assertIsNone(last["next_cursor"])
        self.assertEqual(last["items"][0]["id"], "last")

    def test_voice_id_and_provider_cursor_are_preserved(self):
        result = voice_page({"voices": [{"voice_id": "account-voice", "name": "我的声线"}],
                             "next_cursor": "opaque-cursor"})
        self.assertEqual(result["items"][0]["id"], "account-voice")
        self.assertEqual(result["next_cursor"], "opaque-cursor")

    def test_malformed_catalog_does_not_look_like_an_empty_account(self):
        for answer in ({"text": "authentication required"}, {"voices": [{"name": "missing ID"}]}):
            with self.subTest(answer=answer), self.assertRaises(ProviderError):
                voice_page(answer)
        self.assertEqual(voice_page({"voices": []}), {"items": [], "next_cursor": None})


class SpeechVoiceSearch(unittest.IsolatedAsyncioTestCase):
    async def test_service_forwards_own_filter_and_cursor_to_authenticated_mcp(self):
        adapter = catalog_adapter({"self": {"type": "boolean"}, "cursor": {"type": "string"}})
        permit = SimpleNamespace(submitted=AsyncMock(), finish=AsyncMock())

        @asynccontextmanager
        async def resource(_):
            yield permit

        service = Speech(SimpleNamespace(database=None), SimpleNamespace(resource=resource), None)
        service.selected = AsyncMock(return_value=({}, {"id": "my-account"}, None))
        service.adapter = AsyncMock(return_value=adapter)
        action = SimpleNamespace(arguments={"owned": True, "cursor": "next-page"})
        with patch("tianshu.capabilities.speech.service.request", return_value=object()):
            result = await service.voices(action, object())
        adapter.call.assert_awaited_once_with("search_voices", {"query": "", "self": True, "cursor": "next-page"})
        self.assertEqual(result["items"][0]["id"], "my-voice")
        permit.finish.assert_awaited_once()
