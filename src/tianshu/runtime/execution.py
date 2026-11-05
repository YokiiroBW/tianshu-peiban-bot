"""Bounded business execution lanes, independent of model admission priority."""

from enum import StrEnum


class ExecutionLane(StrEnum):
    CONTROL = "control"
    LIFE_CONTROL = "life_control"
    CAPABILITY_CONTROL = "capability_control"
    OPERATIONS = "operations"
    INBOUND = "inbound"
    DELIVERY = "delivery"
    CONVERSATION = "conversation"
    PROACTIVE = "proactive"
    SCHEDULE = "schedule"
    LIFE = "life"
    PLANNING = "planning"
    DIARY = "diary"
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
    "channels.statistics_reconcile": ExecutionLane.CONTROL,
    "channels.media_supplement": ExecutionLane.CONVERSATION,
    "channels.media_supplement_expire": ExecutionLane.CONVERSATION,
    "channels.resume_delivery": ExecutionLane.CONVERSATION,
    "conversation.run": ExecutionLane.CONVERSATION,
    "conversation.prepare": ExecutionLane.CONVERSATION,
    "conversation.model_complete": ExecutionLane.CONVERSATION,
    "conversation.statistics_reconcile": ExecutionLane.CONTROL,
    "conversation.supplement_notify": ExecutionLane.CONTROL,
    "conversation.example_usage": ExecutionLane.CONTROL,
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
    "life.generation_control": ExecutionLane.LIFE_CONTROL,
    "life.recovery_observe_activity": ExecutionLane.LIFE_CONTROL,
    "life.statistics_replay": ExecutionLane.LIFE_CONTROL,
    "life.statistics_backfill": ExecutionLane.LIFE_CONTROL,
    "life.activate": ExecutionLane.LIFE_CONTROL,
    "life.clock": ExecutionLane.LIFE_CONTROL,
    "life.commitment_due": ExecutionLane.SCHEDULE,
    "life.commitment_reconcile": ExecutionLane.LIFE_CONTROL,
    "life.contact_due": ExecutionLane.PROACTIVE,
    "life.contact_reconcile": ExecutionLane.LIFE_CONTROL,
    "life.plan": ExecutionLane.PLANNING,
    "life.activity_step": ExecutionLane.LIFE,
    "life.activity_cancel": ExecutionLane.LIFE_CONTROL,
    "life.activity_reconcile": ExecutionLane.LIFE_CONTROL,
    "life.diary_collect": ExecutionLane.DIARY,
    "life.diary_review": ExecutionLane.DIARY,
    "life.diary_generate": ExecutionLane.DIARY,
    "life.diary_episode_changed": ExecutionLane.DIARY,
    "life.experience_statistics_backfill": ExecutionLane.DIARY,
    "life.chapter_generate": ExecutionLane.WRITING,
    "memory.consolidate": ExecutionLane.MEMORY_CONSOLIDATION,
    "memory.index_object": ExecutionLane.MEMORY_INDEX,
    "memory.index_retry": ExecutionLane.MEMORY_INDEX,
    "memory.recover": ExecutionLane.MEMORY_RECOVERY,
    "memory.purge_source": ExecutionLane.SOURCE_LIFECYCLE,
    "companion.organize": ExecutionLane.GROWTH,
    "companion.episode_changed": ExecutionLane.GROWTH,
    "companion.growth.statistics": ExecutionLane.GROWTH,
    "companion.growth.retry": ExecutionLane.GROWTH,
    "companion.growth.input_retry": ExecutionLane.GROWTH,
    "sources.propagate": ExecutionLane.SOURCE_LIFECYCLE,
    "sources.notify": ExecutionLane.SOURCE_LIFECYCLE,
    "sources.dispatch": ExecutionLane.SOURCE_LIFECYCLE,
    "capabilities.execute": ExecutionLane.MAINTENANCE,
    "capabilities.control": ExecutionLane.CAPABILITY_CONTROL,
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
}


# Each logical queue has one process owner, but multiple related queues share a
# native worker/SQL pool rather than creating 35 independent listeners.
PROCESS_ROLES = ("interactive", "model-broker", "bot-foreground", "bot-background",
                 "independent", "operations")
ROLE_GROUPS = {
    "bot-foreground": {
        "control": (ExecutionLane.CONTROL,),
        "ingress": (ExecutionLane.INBOUND, ExecutionLane.DELIVERY),
        "conversation": (ExecutionLane.CONVERSATION,),
    },
    "bot-background": {
        "due": (ExecutionLane.LIFE_CONTROL, ExecutionLane.SCHEDULE, ExecutionLane.PROACTIVE),
        "life": (ExecutionLane.LIFE, ExecutionLane.PLANNING, ExecutionLane.DIARY, ExecutionLane.WRITING),
        "memory": (ExecutionLane.MEMORY_CONSOLIDATION, ExecutionLane.MEMORY_INDEX,
                   ExecutionLane.MEMORY_RECOVERY, ExecutionLane.GROWTH),
        "sources": (ExecutionLane.SOURCE_LIFECYCLE,),
    },
    "independent": {
        "downloads": (ExecutionLane.DOWNLOADS,),
        "media": (ExecutionLane.MEDIA, ExecutionLane.BOOK_SOURCES, ExecutionLane.COMIC_SOURCES,
                  ExecutionLane.VIDEO_SUBSCRIPTIONS, ExecutionLane.MUSIC_PLAYLISTS,
                  ExecutionLane.MUSIC_NAVIDROME, ExecutionLane.MUSIC_LYRICS,
                  ExecutionLane.COMIC_EXTENSIONS, ExecutionLane.COMIC_CLEANUP,
                  ExecutionLane.MEDIA_RETENTION, ExecutionLane.MUSIC_LIBRARY, ExecutionLane.BOOK_AUDIO),
        "tools": (ExecutionLane.IMAGES, ExecutionLane.REACTIONS, ExecutionLane.SPEECH,
                  ExecutionLane.PERCEPTION),
        "services": (ExecutionLane.CAPABILITY_CONTROL, ExecutionLane.PLUGINS,
                     ExecutionLane.INFRASTRUCTURE, ExecutionLane.MAINTENANCE),
    },
    # Operations runs its own agent, without inventing a queue consumer.
    "operations": {},
}


def role_worker_groups(worker_settings, role):
    """Return the actual queues and concurrency started by this process role."""
    counts = lane_concurrency(worker_settings)
    return {
        name: {"queues": [lane.value for lane in lanes],
               "concurrency": sum(counts[lane.value] for lane in lanes)}
        for name, lanes in ROLE_GROUPS.get(role, {}).items()
    }


def role_for_lane(lane):
    value = ExecutionLane(lane)
    if value == ExecutionLane.OPERATIONS:
        return "operations"
    for role, groups in ROLE_GROUPS.items():
        if any(value in lanes for lanes in groups.values()):
            return role
    raise ValueError("execution_lane_without_owner")


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
