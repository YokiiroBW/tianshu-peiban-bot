"""The same authorized provider/source options serve execution and planning."""

from ..catalog import Availability
from ..contracts import Fault, require
from ..content_discovery.network import public_target, search_endpoint

SOURCES = ("web", "forum", "bilibili", "music", "books", "comics")


async def providers(configuration, context):
    return [item for item in await configuration.list("content_provider")
            if item.get("enabled", True)
            and (not item.get("actor_id") or item["actor_id"] == context.actor_id)
            and set(item.get("scope_ids", [])) <= set(context.scope_ids)
            and set(item.get("audience", [])) <= set(context.audience)]


def source_states(config, media):
    reasons = {"web": "search_provider_not_configured", "forum": "followed_forums_not_configured",
               "bilibili": None, "music": "music_catalog_not_configured",
               "books": "book_catalog_not_configured", "comics": "comic_catalog_not_configured"}
    if config.get("base_url"):
        try:
            search_endpoint(config)
            reasons["web"] = None
        except Fault as error:
            reasons["web"] = error.code
    if config.get("followed_forums"):
        try:
            for topic in config["followed_forums"]:
                public_target(topic["url"])
            reasons["forum"] = None
        except (Fault, KeyError, TypeError):
            reasons["forum"] = "followed_forums_invalid"
    for source, flag in (("music", "include_music"), ("books", "include_books"),
                         ("comics", "include_comics")):
        if config.get(flag):
            reasons[source] = (None if media is not None and getattr(media, source, None) is not None
                               else "media_catalog_unavailable")
    return {source: {"configured": reason is None, "can_execute": reason is None,
                     "reason": reason, "requires_query": source == "web"}
            for source, reason in reasons.items()}


async def availability(content, context):
    configs = await providers(content.config, context)
    options = [(None, source_states({}, content.media)),
               *((item["id"], source_states(item, content.media)) for item in configs)]
    states = {}
    for source in SOURCES:
        configured = any(values[source]["configured"] for _, values in options)
        states[source] = {
            "configured": configured,
            "can_execute": configured and content.sources is not None,
            "reason": ("source_port_unavailable" if content.sources is None else None
                       if configured else next(values[source]["reason"] for _, values in reversed(options))),
            "provider_ids": [identifier for identifier, values in options
                             if identifier is not None and values[source]["can_execute"]],
            "requires_query": source == "web",
        }
    choices = [{"provider_id": identifier,
                "sources": [source for source in SOURCES
                            if values[source]["can_execute"] and content.sources is not None]}
               for identifier, values in options]
    return Availability(True, any(value["can_execute"] for value in states.values()),
        reason="source_port_unavailable" if content.sources is None else None,
        constraints={"sources": states, "source_options": [item for item in choices if item["sources"]]})


def select_provider(configs, body, media):
    identifier = body.get("provider_id")
    if identifier:
        selected = next((item for item in configs if item["id"] == identifier), None)
        require(selected is not None, "content_provider_not_authorized", 403)
        return selected
    # A browser-only profile sorted before the search service must not hide it.
    requested = body.get("sources", ["web", "bilibili"])
    return max(configs, key=lambda item: sum(source_states(item, media)[source]["can_execute"]
                                            for source in requested), default={})


def validate_provider(value):
    require(type(value.get("allow_private_network", False)) is bool,
            "search_private_network_invalid", 400)
    if value.get("base_url"):
        search_endpoint(value)
    topics = value.get("followed_forums", [])
    require(isinstance(topics, list) and len(topics) <= 20, "followed_forums_invalid", 400)
    for topic in topics:
        require(isinstance(topic, dict) and isinstance(topic.get("url"), str),
                "followed_forums_invalid", 400)
        public_target(topic["url"])
