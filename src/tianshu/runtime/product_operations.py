"""Operations composition: public recovery ports without BOT or media owners."""

from dataclasses import replace

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.configuration import Configuration
from tianshu.channels.service import Channels
from tianshu.operations.module import build_operations
from tianshu.operations.recovery import RecoveryExecutor
from tianshu.runtime.recovery_policy import RecoveryPolicy


def attach_operations(runtime, authority, actions, channels, *, life_worker=None, index_jobs=None):
    settings = runtime.settings.get("operations", {})
    principal_id = settings.get("principal_id")

    async def diagnostic_context():
        return await authority.identity.system_context(principal_id, permission="config.publish")

    return build_operations(
        runtime, authority.identity, capability_actions=actions, channels=channels,
        life_worker=life_worker, index_jobs=index_jobs,
        diagnostic_context=diagnostic_context if principal_id else None,
        policy=RecoveryPolicy(),
        host_spool_directory=settings.get(
            "host_spool_directory", str(runtime.files.root.parent / "operations" / "host-spool")
        ),
        prometheus_url=runtime.settings.get("observability", {}).get("prometheus_url"),
    )


async def build_operations_product(runtime):
    from .product import Product, build_authority
    from tianshu.companion.service import CompanionService
    from tianshu.conversation.context.assembly import ContextAssembler
    from tianshu.life.clock import LifeClock
    from tianshu.life.contacts import Contacts
    from tianshu.life.service import LifeService
    from tianshu.life.state import synchronize_actor
    from tianshu.life.worker import LifeWorker
    from tianshu.memory.claims import ClaimService
    from tianshu.memory.embedding import Embeddings
    from tianshu.memory.service import MemoryService
    from tianshu.memory.steps import ModelSteps
    from tianshu.memory.workers import MemoryWorkers

    authority = build_authority(runtime)
    identity, sources = authority.identity, authority.sources
    database, jobs, models = runtime.database, runtime.jobs, runtime.models
    catalog = Catalog()
    configuration = Configuration(database, runtime.credentials)
    actions = Actions(database, jobs, catalog, identity)
    actions.sources = sources
    actions.argument_resolver = configuration.command
    channels = Channels(
        runtime, runtime.settings.get("channels", {}), identity=identity, sources=sources,
        actions=actions, catalog=catalog, artifacts=authority.artifacts,
        configuration=configuration,
    )
    memory = MemoryService(
        database, identity, sources, runtime.commands, jobs, models,
        settings=runtime.settings.get("memory", {}),
    )
    memory.bind_claims(ClaimService(memory))
    assembler = ContextAssembler(identity, memory, None, settings=runtime.settings.get("context", {}))
    steps = ModelSteps(database, models, sources)
    embeddings = Embeddings(database, models, steps)
    workers = MemoryWorkers(memory, assembler, embeddings, steps)
    clock = LifeClock(database, runtime.commands, jobs, identity, sources)
    companion = CompanionService(
        database, runtime.commands, identity, memory, actor_registry=identity,
        actor_changed=synchronize_actor, sources=sources, actor_activated=clock.activate_later,
    )
    life = LifeService(
        database, runtime.commands, jobs, identity, companion, Contacts(database),
        sources=sources, memory=memory,
    )
    # This instance supplies the narrow original-Action observation port only.
    # No Life clock, planner, dialogue or Memory consumer starts in Operations.
    life_worker = LifeWorker(life, models, assembler, catalog, actions, None)
    operations = attach_operations(
        runtime, authority, actions, channels, life_worker=life_worker, index_jobs=workers.index_jobs,
    )
    runtime.statistics = operations.statistics
    policy = RecoveryPolicy()
    memory.bind_observability(statistics=operations.statistics, recovery_policy=policy)
    life.bind_statistics(operations.statistics)
    life.bind_recovery_policy(policy)
    channels.bind_recovery_policy(policy)
    channels.bind_statistics(operations.statistics)
    actions.bind_statistics(operations.statistics)
    workers.index_jobs.bind_observability(
        statistics=operations.statistics, recovery_policy=policy, guard_on=RecoveryExecutor.guard_on
    )
    module = operations.module

    async def stop(services):
        if module.stop:
            await module.stop(services)
        await channels.close()

    return Product((replace(module, stop=stop),), authority, None, None, operations)
