"""Server-owned business lanes, independent of model priority and asynchronous work."""

from tianshu.runtime.execution import ExecutionLane


def execution_lane(capability_id: str) -> ExecutionLane:
    if capability_id.startswith(("plugin:", "plugins.")):
        return ExecutionLane.PLUGINS
    if capability_id == "images.generate":
        return ExecutionLane.IMAGES
    if capability_id in {"speech.synthesize", "speech.preview", "speech.voices.clone"}:
        return ExecutionLane.SPEECH
    if capability_id in {"video.observe", "speech.transcribe"}:
        return ExecutionLane.PERCEPTION
    if capability_id in {"reactions.search", "reactions.select"}:
        return ExecutionLane.CONVERSATION
    if capability_id.startswith("reactions."):
        return ExecutionLane.REACTIONS
    if capability_id in {
        "media.acquire", "books.downloads.create", "music.playlists.download",
        "comics.downloads.enqueue", "comics.imports.enqueue",
    }:
        return ExecutionLane.DOWNLOADS
    if capability_id.startswith("books.audio.") and capability_id.rsplit(".", 1)[-1] in {
        "generate", "retry",
    }:
        return ExecutionLane.BOOK_AUDIO
    if capability_id in {"books.subscriptions.scan", "books.feeds.refresh"}:
        return ExecutionLane.BOOK_SOURCES
    if capability_id == "comics.subscriptions.scan":
        return ExecutionLane.COMIC_SOURCES
    if capability_id == "media.subscriptions.scan":
        return ExecutionLane.VIDEO_SUBSCRIPTIONS
    if capability_id == "music.playlists.scan":
        return ExecutionLane.MUSIC_PLAYLISTS
    if capability_id.startswith(("media.", "books.", "music.", "comics.", "content.")):
        return ExecutionLane.MEDIA
    if capability_id.startswith("infrastructure."):
        return ExecutionLane.INFRASTRUCTURE
    if capability_id.startswith(("images.", "speech.", "capabilities.")):
        return ExecutionLane.MAINTENANCE
    return ExecutionLane.CONVERSATION
