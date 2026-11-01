"""The single product object graph. Domains receive only the ports they consume."""

from dataclasses import dataclass

from tianshu.identity.service import IdentityService
from tianshu.identity.sessions import SessionService
from tianshu.sources.service import SourceService
from tianshu.capabilities.artifacts import Artifacts
from .media import OwnedMedia


@dataclass(frozen=True, slots=True)
class AuthorityServices:
    identity: IdentityService
    sources: SourceService
    artifacts: Artifacts
    media: OwnedMedia


def build_authority(runtime):
    identity = IdentityService(
        runtime.database,
        runtime.commands,
        sessions=SessionService(runtime.database, runtime.settings),
    )
    sources = SourceService(
        runtime.database, identity, runtime.files, runtime.commands, runtime.jobs
    )
    identity.bind_sources(sources)
    artifacts = Artifacts(runtime.database, runtime.files, identity, sources)
    return AuthorityServices(identity, sources, artifacts, OwnedMedia(sources, artifacts))


@dataclass(frozen=True, slots=True)
class Product:
    modules: tuple
    authority: AuthorityServices
    capabilities: object
    channels: object


async def build_product(runtime):
    from tianshu.identity.module import create_module as identity_module
    from tianshu.sources.module import create_module as source_module
    from tianshu.memory.service import MemoryService
    from tianshu.memory.claims import ClaimService
    from tianshu.memory.steps import ModelSteps
    from tianshu.memory.embedding import Embeddings
    from tianshu.memory.retrieval import MemoryRetrieval
    from tianshu.memory.workers import MemoryWorkers
    from tianshu.memory.module import create_module as memory_module
    from tianshu.memory.tools import register_tools
    from tianshu.conversation.context.assembly import ContextAssembler
    from tianshu.companion.service import CompanionService
    from tianshu.companion.growth import GrowthService
    from tianshu.companion.module import create_module as companion_module
    from tianshu.companion.dependencies import CompanionDependencies
    from tianshu.life.clock import LifeClock
    from tianshu.life.state import synchronize_actor
    from tianshu.life.contacts import Contacts
    from tianshu.life.service import LifeService
    from tianshu.life.presentation import LifePresentation
    from tianshu.life.worker import LifeWorker
    from tianshu.life.writing import WritingService
    from tianshu.life.diaries import DiaryService
    from tianshu.life.reading import ReadingService
    from tianshu.life.module import create_module as life_module
    from tianshu.life.capabilities import register_handlers
    from tianshu.life.dependencies import LifeDependencies
    from tianshu.conversation.service import ConversationService
    from tianshu.conversation.expression import ExpressionService
    from tianshu.conversation.rendering import ExpressionRenderer
    from tianshu.conversation.publication import WebPublication
    from tianshu.conversation.results import ActionResultEvents, ResultConsumer
    from tianshu.conversation.module import create_module as conversation_module
    from tianshu.conversation.dependencies import ConversationDependencies
    from tianshu.conversation.types import CollectorPolicy
    from tianshu.capabilities.module import build_module as capability_module
    from tianshu.capabilities.weather import WeatherService
    from tianshu.channels.module import build_module as channel_module

    db, jobs, commands, models = runtime.database, runtime.jobs, runtime.commands, runtime.models
    authority = build_authority(runtime)
    identity, sources, artifacts = authority.identity, authority.sources, authority.artifacts
    memory = MemoryService(db, identity, sources, commands, jobs, models, settings=runtime.settings.get("memory", {}))
    claims = ClaimService(memory)
    memory.bind_claims(claims)
    steps = ModelSteps(db, models, sources)
    embeddings = Embeddings(db, models, steps)
    retrieval = MemoryRetrieval(
        memory, embeddings, runtime.settings.get("memory", {}).get("retrieval", {})
    )
    memory.bind_record_candidates(retrieval.candidates)
    assembler = ContextAssembler(
        identity, memory, retrieval, settings=runtime.settings.get("context", {})
    )
    memory_workers = MemoryWorkers(memory, assembler, embeddings, steps)

    clock = LifeClock(db, commands, jobs, identity, sources)
    companion = CompanionService(
        db,
        commands,
        identity,
        memory,
        actor_registry=identity,
        actor_changed=synchronize_actor,
        sources=sources,
        actor_activated=clock.activate_later,
    )
    contacts = Contacts(db)
    weather = WeatherService(runtime, authority=identity, sources=sources)
    life = LifeService(
        db,
        commands,
        jobs,
        identity,
        companion,
        contacts,
        sources=sources,
        memory=memory,
        weather=weather,
    )
    presentation = LifePresentation(life, media=artifacts)
    contacts.on_received = life.received
    writing, diaries = (
        WritingService(life, models, assembler),
        DiaryService(life, models, assembler),
    )
    clock.bind_lifecycle(day_plans=life.days, on_day_closed=diaries.day_closed)
    growth = GrowthService(companion, jobs, models, assembler, on_projection_changed=life.signal)

    async def episode_changed(context, watermark, *, episode=None, connection=None):
        # Both consumers register in the original Episode transaction. Neither
        # assembles, generates or waits for a background model on this path.
        await growth.changed(context,watermark,episode=episode,connection=connection)
        await diaries.changed(context,watermark,episode=episode,connection=connection)

    memory.bind_episode_observer(episode_changed)
    result_events = ActionResultEvents(jobs)

    async def actor_directory(context):
        items, cursor = [], None
        while True:
            page = await companion.roles(context, cursor=cursor, limit=100)
            items.extend(
                {
                    "id": row["actor_id"],
                    "name": row["name"],
                    "revision": row["revision"],
                    "enabled": row["enabled"],
                }
                for row in page.items
            )
            if page.next_cursor is None:
                return tuple(items)
            cursor = page.next_cursor

    capabilities_module, caps = capability_module(
        runtime,
        settings=runtime.settings.get("capabilities", {}),
        authority=identity,
        sources=sources,
        life=presentation,
        result_events=result_events,
        authorize_actor=identity.authorize_actor,
        authorize_conversation=identity.authorize_conversation,
        actor_directory=actor_directory,
        artifacts=artifacts,
        weather=weather,
    )
    channels_module, channels = channel_module(
        runtime,
        settings=runtime.settings.get("channels", {}),
        identity=identity,
        sources=sources,
        actions=caps.actions,
        catalog=caps.catalog,
        artifacts=artifacts,
        configuration=caps.configuration,
        web=WebPublication(db, sources),
    )
    expression = ExpressionService(db, jobs, identity, channels.delivery, contacts, sources=sources)
    channels.bind_expression(expression)
    caps.bind_expression(expression)
    await caps.prepare()
    renderer = ExpressionRenderer(
        db, jobs, identity, sources, caps.actions, caps.speech, delivery=expression
    )
    life_worker = LifeWorker(life, models, assembler, caps.catalog, caps.actions, renderer, growth)
    reading = ReadingService(life, caps.reading)
    conversation = ConversationService(
        db,
        commands,
        jobs,
        identity,
        sources,
        memory,
        companion,
        life,
        growth,
        assembler,
        models,
        caps.catalog,
        caps.actions,
        renderer,
        contacts,
        policy=CollectorPolicy(**runtime.settings.get("conversation", {}).get("collector", {})),
    )
    results = ResultConsumer(
        db, identity, caps.actions, memory, growth, conversation, life_worker, renderer
    )
    memory.bind_conversation(conversation.memory_neighborhood)
    channels.bind_conversation(conversation)
    register_tools(caps.catalog, memory, retrieval)
    register_handlers(
        caps.catalog,
        life=life,
        reading=reading,
        writing=writing,
        reactions=caps.reactions,
        artifacts=artifacts,
    )
    for callback in (
        memory.source_changed,
        CompanionDependencies().invalidate,
        LifeDependencies(life).invalidate,
        ConversationDependencies().invalidate,
        caps.withdraw_sources,
        channels.withdraw_sources,
    ):
        sources.register_dependant(callback)
    modules = (
        identity_module(identity),
        source_module(sources, identity),
        memory_module(memory, retrieval, memory_workers, identity),
        companion_module(companion, growth, identity),
        capabilities_module,
        channels_module,
        life_module(life, presentation, writing, diaries, reading, life_worker, identity, clock),
        conversation_module(conversation, expression, identity, results, renderer),
    )
    return Product(modules, authority, caps, channels)
