"""Bounded business execution lanes, independent of model admission priority."""

from enum import StrEnum


class ExecutionLane(StrEnum):
    CONTROL = "control"
    INBOUND = "inbound"
    DELIVERY = "delivery"
    CONVERSATION = "conversation"
    PROACTIVE = "proactive"
    SCHEDULE = "schedule"
    LIFE = "life"
    PLANNING = "planning"
    WRITING = "writing"
    MEMORY_CONSOLIDATION = "memory_consolidation"
    MEMORY_INDEX = "memory_index"
    MEMORY_RECOVERY = "memory_recovery"
    SOURCE_LIFECYCLE = "source_lifecycle"
    GROWTH = "growth"
    IMAGES = "images"
    REACTIONS = "reactions"
    SPEECH = "speech"
    BOOK_AUDIO = "book_audio"
    PERCEPTION = "perception"
    DOWNLOADS = "downloads"
    MEDIA = "media"
    BOOK_SOURCES = "book_sources"
    COMIC_SOURCES = "comic_sources"
    VIDEO_SUBSCRIPTIONS = "video_subscriptions"
    MUSIC_PLAYLISTS = "music_playlists"
    MUSIC_NAVIDROME = "music_navidrome"
    MUSIC_LYRICS = "music_lyrics"
    COMIC_EXTENSIONS = "comic_extensions"
    COMIC_CLEANUP = "comic_cleanup"
    MEDIA_RETENTION = "media_retention"
    MUSIC_LIBRARY = "music_library"
    PLUGINS = "plugins"
    INFRASTRUCTURE = "infrastructure"
    MAINTENANCE = "maintenance"


DEFAULT_CONCURRENCY = {
    lane: 2 if lane in {
        ExecutionLane.CONTROL, ExecutionLane.CONVERSATION,
        ExecutionLane.INBOUND, ExecutionLane.DELIVERY, ExecutionLane.BOOK_SOURCES,
    } else 1
    for lane in ExecutionLane
}

# This is the only default routing table. Tasks whose business kind is stored in
# their payload's object register a server-side resolver instead of using origin
# or model ServiceClass to choose a queue.
TASK_LANES = {
    "channels.ingest": ExecutionLane.INBOUND,
    "channels.cancel": ExecutionLane.CONTROL,
    "conversation.run": ExecutionLane.CONVERSATION,
    "conversation.participation": ExecutionLane.PROACTIVE,
    "conversation.seal": ExecutionLane.INBOUND,
    "conversation.cancel": ExecutionLane.CONTROL,
    "conversation.reconcile": ExecutionLane.CONTROL,
    "conversation.result": ExecutionLane.DELIVERY,
    "conversation.expire": ExecutionLane.CONTROL,
    "conversation.expression_expire": ExecutionLane.CONTROL,
    "conversation.expression_release": ExecutionLane.DELIVERY,
    "conversation.expression_render": ExecutionLane.CONTROL,
    "conversation.render_expire": ExecutionLane.CONTROL,
    "conversation.expression_result": ExecutionLane.DELIVERY,
    "conversation.expression_reconcile": ExecutionLane.CONTROL,
    "life.generation_control": ExecutionLane.CONTROL,
    "life.activate": ExecutionLane.CONTROL,
    "life.clock": ExecutionLane.CONTROL,
    "life.commitment_due": ExecutionLane.SCHEDULE,
    "life.contact_due": ExecutionLane.PROACTIVE,
    "life.plan": ExecutionLane.PLANNING,
    "life.activity_step": ExecutionLane.LIFE,
    "life.activity_cancel": ExecutionLane.CONTROL,
    "life.activity_reconcile": ExecutionLane.CONTROL,
    "life.diary_collect": ExecutionLane.WRITING,
    "life.diary_generate": ExecutionLane.WRITING,
    "life.chapter_generate": ExecutionLane.WRITING,
    "memory.extract": ExecutionLane.MEMORY_CONSOLIDATION,
    "memory.consolidate": ExecutionLane.MEMORY_CONSOLIDATION,
    "memory.observe_legacy": ExecutionLane.MEMORY_CONSOLIDATION,
    "memory.index_object": ExecutionLane.MEMORY_INDEX,
    "memory.index": ExecutionLane.MEMORY_INDEX,
    "memory.recover": ExecutionLane.MEMORY_RECOVERY,
    "memory.purge_source": ExecutionLane.SOURCE_LIFECYCLE,
    "companion.organize": ExecutionLane.GROWTH,
    "sources.propagate": ExecutionLane.SOURCE_LIFECYCLE,
    "capabilities.execute": ExecutionLane.MAINTENANCE,
    "capabilities.control": ExecutionLane.CONTROL,
    "capabilities.media_job": ExecutionLane.DOWNLOADS,
    "capabilities.plugin_event": ExecutionLane.PLUGINS,
    "capabilities.plugin_schedule": ExecutionLane.PLUGINS,
    "capabilities.infrastructure_job": ExecutionLane.INFRASTRUCTURE,
    # These maintenance entries only dispatch their persisted work generations.
    "capabilities.media.book_audio": ExecutionLane.MAINTENANCE,
    "capabilities.media.book_subscriptions": ExecutionLane.MAINTENANCE,
    "capabilities.media.book_sources": ExecutionLane.MAINTENANCE,
    "capabilities.media.comic_extensions": ExecutionLane.MAINTENANCE,
    "capabilities.media.comic_cleanup": ExecutionLane.MAINTENANCE,
    "capabilities.media.comic_subscriptions": ExecutionLane.MAINTENANCE,
    "capabilities.media.subscriptions": ExecutionLane.MAINTENANCE,
    "capabilities.media.retention": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_playlists": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_imports": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_navidrome": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_maintenance": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_lyrics": ExecutionLane.MAINTENANCE,
    "capabilities.media.infrastructure_observe": ExecutionLane.MAINTENANCE,
    "capabilities.media.work.book_audio": ExecutionLane.BOOK_AUDIO,
    "capabilities.media.work.book_subscriptions": ExecutionLane.BOOK_SOURCES,
    "capabilities.media.work.book_sources": ExecutionLane.BOOK_SOURCES,
    "capabilities.media.work.comic_extensions": ExecutionLane.COMIC_EXTENSIONS,
    "capabilities.media.work.comic_cleanup": ExecutionLane.COMIC_CLEANUP,
    "capabilities.media.work.comic_subscriptions": ExecutionLane.COMIC_SOURCES,
    "capabilities.media.work.subscriptions": ExecutionLane.VIDEO_SUBSCRIPTIONS,
    "capabilities.media.work.retention": ExecutionLane.MEDIA_RETENTION,
    "capabilities.media.work.music_playlists": ExecutionLane.MUSIC_PLAYLISTS,
    "capabilities.media.work.music_imports": ExecutionLane.MAINTENANCE,
    "capabilities.media.work.music_navidrome": ExecutionLane.MUSIC_NAVIDROME,
    "capabilities.media.work.music_maintenance": ExecutionLane.MAINTENANCE,
    "capabilities.media.work.music_lyrics": ExecutionLane.MUSIC_LYRICS,
    "capabilities.media.work.infrastructure_observe": ExecutionLane.MAINTENANCE,
    "capabilities.media.library_job": ExecutionLane.MUSIC_LIBRARY,
}


def lane_concurrency(worker_settings):
    configured = worker_settings.get("lanes", {})
    unknown = set(configured) - {lane.value for lane in ExecutionLane}
    if unknown:
        raise ValueError("unknown_execution_lane")
    counts = {
        lane.value: configured.get(lane.value, default)
        for lane, default in DEFAULT_CONCURRENCY.items()
    }
    if any(type(count) is not int or not 1 <= count <= 32 for count in counts.values()):
        raise ValueError("invalid_execution_lane_concurrency")
    return counts


def job_observation(row):
    """Describe actual native queue metadata without inferring model admission."""
    result = dict(row)
    scheduled = result.pop("scheduled", False)
    queue = result["queue_name"]
    lane = queue if queue in {value.value for value in ExecutionLane} else None
    result["execution_lane"] = lane
    state = result.get("job_state", result.get("status"))
    if state == "todo":
        result["wait_reason"] = (
            "execution_lane_unresolved" if lane is None
            else "scheduled" if scheduled else "native_task_queue")
    elif state == "doing":
        result["wait_reason"] = "executing"
    else:
        result["wait_reason"] = None
    return result
