"""Decoded source originals and transport wrappers remain distinct."""

from dataclasses import replace

import pytest

from tianshu.memory.material_media import read_text_media_original, read_view
from tianshu.types import Coverage, MediaRef


def read_source(mime, content, *, locator=None, representation="text"):
    media = MediaRef("source:fixture:1", "sha256:"+"a"*64, mime, 32, "a"*64)
    size = len(content) if isinstance(content, str) else None
    return {"content": content, "media": media, "locator": locator or {"kind": "captured_result"},
        "coverage": Coverage(True, size, ranges=({"start": 0, "end": size},) if size is not None else (),
            representation=representation)}


@pytest.mark.parametrize(("mime", "content"), [
    ("text/plain", "普通文本"),
    ("text/html; charset=utf-8", "<p>文本</p>"),
    (" TEXT/XML ; Charset=UTF-8", "<item>文本</item>"),
    ("application/json", '{"value":22}'),
    ("application/xml", "<item>22</item>"),
    ("application/problem+json; charset=UTF-8", '{"title":"问题"}'),
    ("application/soap+xml", "<Envelope/>"),
    ("text/plain", ""),
    ("application/json", ""),
])
def test_decoded_text_media_matches_the_model_visible_source(mime, content):
    original = read_source(mime, content)
    view = read_view(original)
    assert view["text"] == content and view["media"] is None
    assert read_text_media_original(original, view)


def test_captured_file_original_is_not_a_file_part_caption():
    original = read_source("application/json", '{"name":"actual document"}', locator={"kind":"file", "part":0})
    assert read_text_media_original(original, read_view(original))


@pytest.mark.parametrize(("mime", "content", "locator"), [
    ("application/json", None, {"kind":"captured_result"}),
    ("application/octet-stream", "binary-like caption", {"kind":"captured_result"}),
    ("audio/mpeg", "歌曲标题", {"kind":"captured_result"}),
    ("video/mp4", "字幕内容", {"kind":"captured_result"}),
    ("image/svg+xml", "<svg/>", {"kind":"captured_result"}),
    ("text/plain", "字幕内容", {"part_kind":"audio", "part":0}),
    ("text/plain", "标题", {"part_kind":"video", "part":0}),
    ("text/plain", "文件标题", {"part_kind":"file", "part":0}),
    ("text/plain", "附件说明", {"part_kind":"text", "part":0}),
    ("application/json", '{"kind":"reply","metadata":{}}', {"part_kind":"reply", "part":0}),
    ("application/json", '{"kind":"mention","metadata":{}}', {"part_kind":"mention", "part":0}),
    ("application/json", '{"kind":"file","metadata":{}}', {"part_kind":"file", "part":0}),
    ("application/json", '{"segments":[{"type":"file"}]}', {"kind":"channel_transport_original"}),
    ("application/json", '{"messages":[{"type":"file"}]}', {"kind":"channel_forward"}),
    ("application/json", '{"x":1}', {"producer":"channel", "event_id":"event", "part":0}),
])
def test_captions_unread_bytes_and_transport_parts_are_not_confirmed_originals(mime, content, locator):
    original = read_source(mime, content, locator=locator)
    assert not read_text_media_original(original, read_view(original))


def test_source_text_evidence_must_match_the_visible_view_and_representation():
    original = read_source("application/json", '{"value":22}')
    view = read_view(original)
    changed_view = {**view, "text":"different text"}
    assert not read_text_media_original(original, changed_view)
    unread = {**original, "content":None}
    assert not read_text_media_original(unread, read_view(unread))
    no_text_read = {**original, "coverage":replace(original["coverage"], representation="original")}
    assert not read_text_media_original(no_text_read, read_view(no_text_read))
