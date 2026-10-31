"""Immutable module-local payloads; SQL retains business state and exact digests.

FileStore fsyncs and seals bytes before a SQL reference can commit. No cache TTL
or background deletion owns these recovery files.
"""

import copy
from pathlib import Path

from tianshu.runtime.files import FileStore
from tianshu.runtime.serialization import wire
from tianshu.types import MediaRef


class MediaPayloads:
    def __init__(self,files):
        self.books=self.lyrics=files

    def store(self, kind, value):
        result = copy.deepcopy(value)
        field = {"book_chapter": "content", "book_audio_job": "text"}.get(kind)
        if field and field in result:
            result[field + "_ref"] = wire(self.books.put_text(result.pop(field)))
        if kind == "music_lyrics":
            for item in (result.get("pending") or {}).get("writes", []):
                if "content" in item:
                    item["content_ref"] = wire(self.lyrics.put_text(item.pop("content")))
        return result

    def read(self, kind, value):
        if value is None:
            return None
        result = copy.deepcopy(value)
        field = {"book_chapter": "content", "book_audio_job": "text"}.get(kind)
        if field and field + "_ref" in result:
            result[field] = self.books.read_text(MediaRef(**result.pop(field + "_ref")))
        if kind == "music_lyrics":
            for item in (result.get("pending") or {}).get("writes", []):
                if "content_ref" in item:
                    item["content"] = self.lyrics.read_text(MediaRef(**item.pop("content_ref")))
        return result
