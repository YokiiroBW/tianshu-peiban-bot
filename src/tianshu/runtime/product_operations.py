"""Operations composition: public recovery ports without BOT or media owners."""

from dataclasses import replace
import asyncio

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.configuration import Configuration
from tianshu.channels.service import Channels
from tianshu.operations.module import build_operations
from tianshu.operations.recovery import RecoveryAction, RecoveryExecutor
from tianshu.runtime.recovery_policy import RecoveryPolicy


def attach_operations(runtime, authority, actions, channels, *, life_worker=None, index_jobs=None,
                      memory=None, growth=None, infrastructure=None):
    settings = runtime.settings.get("operations", {})
    principal_id = settings.get("principal_id")
    from tianshu.conversation.rbg_result_recovery import ResultRecovery

    result_recovery = ResultRecovery(
        runtime.database, runtime.jobs, authority.identity, RecoveryExecutor.guard_on,
        actions=actions, life_worker=life_worker,
    )
    plan_recovery = None
    if life_worker is not None:
        from tianshu.life.rbg_plan_operations import RBGPlanOperationsRecovery

        plan_recovery = RBGPlanOperationsRecovery(life_worker, RecoveryExecutor.guard_on)

    async def diagnostic_context():
        return await authority.identity.system_context(principal_id, permission="config.publish")

    async def reconcile_transport(domain, callback):
        key = "operations.transport." + domain
        try:
            result = await callback(limit=32)
        except Exception as error:
            runtime.module_status[key] = {"state": "unavailable", "error": type(error).__name__}
            return
        runtime.module_status[key] = {
            "state": "ready", "inspected": result["inspected"],
            "scheduled": result["scheduled"], "ineligible": result["ineligible"],
            "changes": len(result["changes"]),
        }

    async def plan_progress():
        await reconcile_transport("life", plan_recovery.reconcile_transport_waits)
        observed = await plan_recovery.observe_due_retirements(limit=32)
        runtime.module_status["operations.plan_retirements"] = {
            "state": "unavailable" if observed["unavailable"] else "ready",
            "observed": observed["observed"],
            "unavailable": observed["unavailable"],
            "changes": len(observed["changes"]),
        }
        return tuple(
            fact for fact in await plan_recovery.candidates(limit=32)
            if fact.get("business_blocked") is not False
        )

    async def memory_progress():
        await reconcile_transport("memory", memory.consolidation.reconcile_transport_waits)
        return await memory.recovery_candidates(limit=32)

    async def growth_progress():
        await reconcile_transport("growth", growth.retries.reconcile_transport_waits)
        return await growth.operations.progress(limit=32)

    async def progress():
        readers = []
        if life_worker is not None:
            readers.append(("life", life_worker.recovery_candidates(limit=32)))
        if plan_recovery is not None:
            readers.append(("life_plan", plan_progress()))
        readers.append(("conversation_result", result_recovery.candidates(limit=32)))
        if index_jobs is not None:
            readers.append(("memory_index", index_jobs.recovery_candidates(limit=32)))
        if memory is not None:
            readers.append(("memory_consolidation", memory_progress()))
        if growth is not None:
            readers.append(("growth", growth_progress()))
        values = await asyncio.gather(*(reader for _, reader in readers), return_exceptions=True)
        facts = []
        for (domain, _), value in zip(readers, values):
            if isinstance(value, BaseException):
                runtime.module_status["operations.progress." + domain] = {
                    "state": "unavailable", "error": type(value).__name__,
                }
            else:
                runtime.module_status["operations.progress." + domain] = {"state": "ready"}
                facts.extend(value)
        return tuple(facts)

    recovery_actions = (result_recovery.registration(),)
    if plan_recovery is not None:
        recovery_actions += (plan_recovery.registration(),)
    if memory is not None:
        memory.set_operations_guard(RecoveryExecutor.guard_on)
        recovery_actions += (RecoveryAction(
            "memory.consolidation.resume", frozenset({"memory_consolidation_failed"}),
            memory.operations.inspect, memory.operations.invoke, memory.operations.observe,
            observation_only=True, domain_managed=True,
        ),)
    return build_operations(
        runtime, authority.identity, capability_actions=actions, channels=channels,
        actions=recovery_actions,
        life_worker=life_worker, index_jobs=index_jobs,
        growth=growth, infrastructure=infrastructure, progress=progress,
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
    from tianshu.companion.growth import GrowthService
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
    growth = GrowthService(companion, jobs, models, assembler, on_projection_changed=life.signal)
    growth.set_life(life)
    operations = attach_operations(
        runtime, authority, actions, channels, life_worker=life_worker, index_jobs=workers.index_jobs,
        memory=memory, growth=growth,
    )
    runtime.statistics = operations.statistics
    policy = RecoveryPolicy()
    memory.bind_observability(statistics=operations.statistics, recovery_policy=policy)
    life.bind_statistics(operations.statistics)
    life.bind_recovery_policy(policy)
    growth.set_observability(statistics=operations.statistics, recovery_policy=policy)
    channels.bind_recovery_policy(policy)
    channels.bind_statistics(operations.statistics)
    actions.statistics = operations.statistics
    workers.index_jobs.bind_observability(
        statistics=operations.statistics, recovery_policy=policy, guard_on=RecoveryExecutor.guard_on
    )
    module = operations.module

    async def stop(services):
        if module.stop:
            await module.stop(services)
        await channels.close()

    return Product((replace(module, stop=stop),), authority, None, None, operations)
