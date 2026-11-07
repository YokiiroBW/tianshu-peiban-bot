"""The overview reuses the music owner's scan projection independently of playlists."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tianshu.capabilities.media.music.imports import Imports
from tianshu.capabilities.media.runtime import Media


@pytest.mark.asyncio
async def test_overview_includes_scan_progress_without_playlist_subscriptions():
    scan = {"state": "scanning", "total": 1702, "indexed": 231, "failed": 0}
    schedule = {"state": "blocked", "code": "subscription_context_required"}
    documents = {
        ("music_import_scan", "library"): scan,
        ("music_library", "library"): {"schedule": schedule},
    }
    media = object.__new__(Media)
    media.config = {"targets": {}}
    media.repo = SimpleNamespace(
        list=lambda _kind: [],
        jobs=lambda: ([], None),
        job_overview=lambda: {"total": 1, "processing": 1},
        dashboard_details=lambda: {"music": {"total": 0, "files": 236}},
        get=Mock(side_effect=lambda kind, key: documents.get((kind, key))),
    )

    async def local(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    media.local = local
    media.storage_breakdown = SimpleNamespace(view=lambda value: value)
    media.runtime_sample = SimpleNamespace(view=dict)
    media.background = SimpleNamespace(snapshot=AsyncMock(return_value=[]))
    media._engine_observation = AsyncMock(return_value={"state": "not_verified"})
    media.music = SimpleNamespace(media=media, library=SimpleNamespace(catalog=None))
    media.music.imports = Imports(media.music)

    result = await media.view()

    assert result["overview"]["music"] == {
        "total": 0, "files": 236, "scan": {**scan, "schedule": schedule}
    }
    assert result["subscriptions"] == []
    assert scan == {"state": "scanning", "total": 1702, "indexed": 231, "failed": 0}
