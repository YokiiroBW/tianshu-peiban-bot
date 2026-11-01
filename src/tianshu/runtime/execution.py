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
    GROWTH = "growth"
    IMAGES = "images"
    SPEECH = "speech"
    BOOK_AUDIO = "book_audio"
    PERCEPTION = "perception"
    DOWNLOADS = "downloads"
    MEDIA = "media"
    PLUGINS = "plugins"
    INFRASTRUCTURE = "infrastructure"
    MAINTENANCE = "maintenance"


DEFAULT_CONCURRENCY = {
    lane: 2 if lane in {
        ExecutionLane.CONTROL, ExecutionLane.CONVERSATION,
        ExecutionLane.INBOUND, ExecutionLane.DELIVERY,
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
    "conversation.participation": ExecutionLane.CONVERSATION,
    "conversation.seal": ExecutionLane.INBOUND,
    "conversation.cancel": ExecutionLane.CONTROL,
    "conversation.reconcile": ExecutionLane.CONTROL,
    "conversation.result": ExecutionLane.DELIVERY,
    "conversation.expire": ExecutionLane.CONTROL,
    "conversation.expression_expire": ExecutionLane.CONTROL,
    "conversation.expression_release": ExecutionLane.DELIVERY,
    "conversation.expression_render": ExecutionLane.CONTROL,
    "conversation.render_expire": ExecutionLane.CONTROL,
    "conversation.expression_result": ExecutionLane.CONTROL,
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
    "memory.recover": ExecutionLane.MAINTENANCE,
    "memory.purge_source": ExecutionLane.MAINTENANCE,
    "companion.organize": ExecutionLane.GROWTH,
    "sources.propagate": ExecutionLane.MAINTENANCE,
    "capabilities.execute": ExecutionLane.MAINTENANCE,
    "capabilities.control": ExecutionLane.CONTROL,
    "capabilities.media_job": ExecutionLane.DOWNLOADS,
    "capabilities.plugin_event": ExecutionLane.PLUGINS,
    "capabilities.plugin_schedule": ExecutionLane.PLUGINS,
    "capabilities.infrastructure_job": ExecutionLane.INFRASTRUCTURE,
    "capabilities.media.book_audio": ExecutionLane.BOOK_AUDIO,
    "capabilities.media.book_subscriptions": ExecutionLane.DOWNLOADS,
    "capabilities.media.book_sources": ExecutionLane.DOWNLOADS,
    "capabilities.media.comic_extensions": ExecutionLane.MAINTENANCE,
    "capabilities.media.comic_cleanup": ExecutionLane.MAINTENANCE,
    "capabilities.media.comic_subscriptions": ExecutionLane.DOWNLOADS,
    "capabilities.media.subscriptions": ExecutionLane.DOWNLOADS,
    "capabilities.media.retention": ExecutionLane.MAINTENANCE,
    "capabilities.media.music_playlists": ExecutionLane.MEDIA,
    "capabilities.media.music_imports": ExecutionLane.MEDIA,
    "capabilities.media.music_navidrome": ExecutionLane.MEDIA,
    "capabilities.media.music_maintenance": ExecutionLane.MEDIA,
    "capabilities.media.music_lyrics": ExecutionLane.MEDIA,
    "capabilities.media.infrastructure_observe": ExecutionLane.INFRASTRUCTURE,
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
