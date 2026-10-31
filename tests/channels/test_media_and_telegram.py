"""Encoding, scoped path mappings, edits and actual media ordering."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.types import MediaRef
from tianshu.runtime.serialization import wire
from tianshu.channels.media import Media, sniff
from tianshu.channels.contracts import ChannelError
from tianshu.channels.telegram import normalize_update
from tianshu.channels.delivery import compile_calls


class MediaAndTelegram(unittest.IsolatedAsyncioTestCase):
    def test_path_mapping_cannot_escape_but_keeps_real_gif(self):
        media = Media(None, None, None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actual.gif"
            path.write_bytes(b"GIF89a")
            connection = {
                "media_path_mappings": [{"remote_root": "/qq/cache", "local_root": directory}]
            }
            self.assertEqual(
                media.local_mapping(connection, "/qq/cache/actual.gif"), path.resolve()
            )
            with self.assertRaises(ChannelError):
                media.local_mapping(connection, "/qq/cache/../private")
        self.assertEqual(sniff(b"GIF89a", "image/jpeg"), "image/gif")
        self.assertEqual(sniff(b"\x02#!SILK_V3", None), "audio/silk")

    def test_edits_same_second_keep_distinct_revision_and_utf16_mentions(self):
        connection = {
            "id": "tg",
            "namespace": "telegram",
            "self_external_id": "22",
            "username": "bot",
        }
        message = {
            "message_id": 9,
            "date": 1793440000,
            "edit_date": 1793440001,
            "chat": {"id": -88, "type": "supergroup"},
            "from": {"id": 77},
            "text": "😀 @bot",
            "entities": [{"type": "mention", "offset": 3, "length": 4}],
        }
        first, _ = normalize_update(connection, {"update_id": 100, "edited_message": message})
        second, _ = normalize_update(connection, {"update_id": 101, "edited_message": message})
        self.assertEqual(first.event_id, second.event_id)
        self.assertGreater(second.revision, first.revision)
        self.assertEqual(first.mentioned_external_ids, ("22",))

    async def test_gif_voice_and_text_use_actual_sequential_apis(self):
        gif = MediaRef("source:g:1", "sha256:g", "image/gif", 6, "g")
        voice = MediaRef("artifact:v", "sha256:v", "audio/mpeg", 12, "v")
        calls = await compile_calls(
            SimpleNamespace(),
            None,
            {"target_ref": "group:-88:thread:4"},
            {"adapter": "telegram"},
            [
                {"kind": "text", "text": "before"},
                {"kind": "image", "media": wire(gif)},
                {"kind": "audio", "media": wire(voice)},
                {"kind": "text", "text": "after"},
            ],
        )
        self.assertEqual(
            [call[0] for call in calls],
            ["sendMessage", "sendAnimation", "sendVoice", "sendMessage"],
        )
        self.assertTrue(all(call[1]["message_thread_id"] == 4 for call in calls))
        self.assertIn("_uploads", calls[1][1])
