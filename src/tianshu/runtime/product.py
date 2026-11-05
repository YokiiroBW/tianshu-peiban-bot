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
    operations: object | None = None


async def build_product(runtime):
    if runtime.role == "operations":
        from .product_operations import build_operations_product

        return await build_operations_product(runtime)

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
    from tianshu.runtime.recovery_policy import RecoveryPolicy
    from tianshu.operations.recovery import RecoveryExecutor
    from tianshu.operations.statistics import StatisticsProjection
    from tianshu.runtime.jobs import JobSpec
    from tianshu.runtime.serialization import wire
    from tianshu.types import ServiceClass

    db, jobs, commands, models = runtime.database, runtime.jobs, runtime.commands, runtime.models
    api = runtime.role == "interactive"
    background = runtime.role == "bot-background"
    dialogue = runtime.role in {"interactive", "bot-foreground", "bot-background"}
    policy = RecoveryPolicy()
    if runtime.statistics is None:
        runtime.statistics = StatisticsProjection(db, runtime.diagnostics)
    statistics = runtime.statistics
    authority = build_authority(runtime)
    identity, sources, artifacts = authority.identity, authority.sources, authority.artifacts
    memory = MemoryService(db, identity, sources, commands, jobs, models, settings=runtime.settings.get("memory", {}))
    claims = ClaimService(memory)
    memory.bind_claims(claims)
    memory.bind_observability(statistics=statistics, recovery_policy=policy)
    steps = ModelSteps(db, models, sources)
    embeddings = Embeddings(db, models, steps)
    retrieval = MemoryRetrieval(
        memory, embeddings, runtime.settings.get("memory", {}).get("retrieval", {})
    )
    memory.bind_record_candidates(retrieval.candidates)
    memory.bind_index_readiness(retrieval.index_readiness)
    assembler = ContextAssembler(
        identity, memory, retrieval, settings=runtime.settings.get("context", {})
    )
    memory_workers = MemoryWorkers(memory, assembler, embeddings, steps) if api or background else None
    if memory_workers is not None:
        from tianshu.operations.recovery import RecoveryExecutor

        memory_workers.index_jobs.bind_observability(
            statistics=statistics, recovery_policy=policy, guard_on=RecoveryExecutor.guard_on
        )

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
    life.bind_statistics(statistics)
    life.bind_recovery_policy(policy)
    contacts.on_received = life.received
    writing = WritingService(life, models, assembler)
    diaries = DiaryService(life, models, assembler) if api or background else None
    writing.bind_observability(recovery_policy=policy, statistics=statistics)
    if diaries is not None:
        diaries.bind_observability(recovery_policy=policy, statistics=statistics)
        clock.bind_lifecycle(day_plans=life.days, on_day_closed=diaries.day_closed)
    growth = GrowthService(companion, jobs, models, assembler, on_projection_changed=life.signal)
    growth.set_life(life)
    growth.set_observability(statistics=statistics, recovery_policy=policy)
    growth.set_operations_guard(RecoveryExecutor.guard_on)
    assembler.bind_behavior_resolver(growth)
    retrieval.bind_behavior_resolver(growth.resolve_behaviors)
    retrieval.bind_learning_resolver(growth.learning_records)

    async def episode_changed(context, watermark, *, episode=None, connection=None):
        if episode is None:
            return
        if connection is None:
            async with db.transaction() as transaction:
                return await episode_changed(
                    context, watermark, episode=episode, connection=transaction
                )
        # Only durable notifications join the source transaction. Consumers read
        # the original revision and commit or fail independently afterwards.
        scene = identity.scene_id(context)
        generation = await jobs.current_generation_on(connection)
        identity_key = f"{scene}:{context.principal_id}:{episode.episode_id}:{episode.revision}"
        payload = {
            "context": wire(context), "episode_id": episode.episode_id,
            "revision": episode.revision, "watermark": wire(watermark),
            "generation": generation,
        }
        for task in ("companion.episode_changed", "life.diary_episode_changed"):
            await jobs.enqueue(connection, JobSpec(
                task, f"{task}:{identity_key}", payload,
                ServiceClass.ELASTIC, subject_id=context.actor_id, data_generation=generation,
            ))

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
    channels.bind_behavior_resolver(growth)
    channels.bind_recovery_policy(policy)
    channels.bind_statistics(statistics)
    caps.bind_expression(expression)
    from .product_behaviors import validate_action_behaviors

    async def behavior_validator(context, refs, *, purpose):
        return await validate_action_behaviors(growth, context, refs, purpose=purpose)

    caps.actions.bind_behavior_validator(behavior_validator)
    caps.bind_statistics(statistics)
    caps.bind_recovery_policy(policy)
    caps.perception.recovery_policy = policy

    async def capabilities_changed(context, capability_ids):
        if context.actor_id:
            await life.signal(context, "capability-observation:" + caps.catalog.observation_digest(context))

    caps.catalog.on_change = capabilities_changed
    caps.catalog.on_ready = life.planning_ready
    await caps.prepare()
    reading = ReadingService(life, caps.reading)
    writing.bind_completion(life.writing_completed, prepare=life.prepare_writing_completed)
    if dialogue:
        renderer = ExpressionRenderer(
            db, jobs, identity, sources, caps.actions, caps.speech, delivery=expression
        )
        renderer.bind_behavior_resolver(growth)
        life_worker = LifeWorker(life, models, assembler, caps.catalog, caps.actions, renderer, growth)
        life_worker.recovery_guard_on = RecoveryExecutor.guard_on
        life_worker.bind_writing(writing)
        renderer.bind_contact_preflight(life_worker.contact_preflight)
        channels.bind_contact_preflight(life_worker.contact_preflight)
        conversation = ConversationService(
            db, commands, jobs, identity, sources, memory, companion, life, growth,
            assembler, models, caps.catalog, caps.actions, renderer, contacts,
            policy=CollectorPolicy(**runtime.settings.get("conversation", {}).get("collector", {})),
        )
        conversation.bind_statistics(statistics)
        growth.set_conversation(conversation)
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
    if background:
        conversation_dependencies = ConversationDependencies(jobs=jobs)
        conversation_dependencies.bind_statistics(statistics)
        for name, consumer in (
            ("memory", memory), ("companion", CompanionDependencies()),
            ("life", LifeDependencies(life)), ("conversation", conversation_dependencies),
            ("capabilities", caps), ("channels", channels),
        ):
            sources.register_dependant(
                consumer.invalidate_actor, name=name, partitions=consumer.actors_for_source
            )
        from tianshu.models.ledger import purge_source_results_many

        async def models_source_changed(connection, refs, kind):
            await purge_source_results_many(connection, refs)

        sources.register_dependant(models_source_changed, name="models")

    modules = [capabilities_module, channels_module]
    if api:
        modules.insert(0, identity_module(identity))
    if api or background:
        modules.extend((
            source_module(sources, identity),
            memory_module(memory, retrieval, memory_workers, identity),
            companion_module(companion, growth, identity),
            life_module(life, presentation, writing, diaries, reading, life_worker, identity, clock),
        ))
    if dialogue:
        modules.append(conversation_module(conversation, expression, identity, results, renderer))
    operations = None
    if api:
        from .product_operations import attach_operations

        operations = attach_operations(
            runtime, authority, caps.actions, channels,
            life_worker=life_worker, index_jobs=memory_workers.index_jobs,
            memory=memory, growth=growth, infrastructure=caps.web_infrastructure,
        )
        modules.append(operations.module)
    return Product(tuple(modules), authority, caps, channels, operations)
