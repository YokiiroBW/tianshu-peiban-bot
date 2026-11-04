"""HTTP registration and explicit product composition for the operations role."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from tianshu.runtime.api import command_body, json_response, require_context
from tianshu.runtime.composition import DomainModule
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration
from .agent import OperationsAgent
from .diagnosis import Diagnosis
from .incidents import IncidentStore
from .notifications import NotificationOutbox
from .recovery import RecoveryAction, RecoveryExecutor
from .statistics import StatisticsProjection


@dataclass(frozen=True, slots=True)
class OperationsComponents:
    module: DomainModule
    statistics: StatisticsProjection
    incidents: IncidentStore
    executor: RecoveryExecutor
    agent: OperationsAgent


def build_operations(runtime, identity, *, actions=(), diagnostic_context=None,
                     qq_send=None, progress=None, policy=None, host_spool_directory=None,
                     prometheus_url=None, capability_actions=None, context_for_action=None,
                     life_worker=None, index_jobs=None, growth=None, channels=None, infrastructure=None):
    if policy is None:
        from tianshu.runtime.recovery_policy import RecoveryPolicy
        policy = RecoveryPolicy()
    incidents = IncidentStore(runtime.database, runtime.commands, identity, runtime.credentials)
    from .resources import PrometheusResources
    statistics = StatisticsProjection(runtime.database, runtime.diagnostics, PrometheusResources(prometheus_url), infrastructure)
    if qq_send is None and channels is not None:
        from .qq_notification import QQOperationsNotifier
        qq_send = QQOperationsNotifier(identity, channels)
    outbox = NotificationOutbox(runtime.database, runtime.credentials, qq_send=qq_send)
    from .model_recovery import model_configuration_refresh
    actions = tuple(RecoveryAction(**entry) if isinstance(entry, dict) else entry for entry in actions)
    if life_worker is not None:
        from tianshu.life.operations_recovery import LifeOperationsRecovery
        registration = LifeOperationsRecovery(life_worker, RecoveryExecutor.guard_on).registration()
        registration["observation_only"] = True
        registration["domain_managed"] = True
        actions = (RecoveryAction(**registration), *actions)
    if index_jobs is not None:
        index_jobs.bind_observability(statistics=statistics, recovery_policy=policy, guard_on=RecoveryExecutor.guard_on)
        actions = (RecoveryAction("memory.index.rebuild",
                    frozenset({"memory_index_failed", "memory_index_missing", "memory_vector_candidate_budget"}),
                    index_jobs.recovery_inspect, index_jobs.recovery_invoke, index_jobs.recovery_observe,
                    domain_managed=True), *actions)
    if growth is not None:
        growth.set_operations_guard(RecoveryExecutor.guard_on)
        actions = (RecoveryAction("growth.retry.resume", frozenset({"growth_retry_stalled", "growth_retry_blocked"}),
                    growth.operations.inspect, growth.operations.invoke, growth.operations.observe,
                    observation_only=True, domain_managed=True), *actions)
    if (capability_actions is not None and context_for_action is None
            and hasattr(capability_actions, "context_for_action")
            and hasattr(identity, "refresh_scheduled_context")):
        async def context_for_action(action_id):
            original = await capability_actions.context_for_action(action_id)
            return await identity.refresh_scheduled_context(original, permission=None, require_actor=False)
    if capability_actions is not None and context_for_action is not None:
        from .capability_recovery import capability_reroute
        actions = (capability_reroute(capability_actions, context_for_action), *actions)
    executor = RecoveryExecutor(runtime.database, policy, outbox,
                                (model_configuration_refresh(runtime), *actions), statistics=statistics)
    diagnosis = Diagnosis(runtime.database, runtime.models, diagnostic_context or incidents.diagnostic_context, executor, outbox)
    if host_spool_directory:
        from .host_spool import HostSpool
        host_spool = HostSpool(host_spool_directory, incidents, runtime.database, outbox)
    else:
        host_spool = None
    agent = OperationsAgent(runtime.database, runtime.diagnostics, incidents, executor, diagnosis, outbox,
                            progress=progress, host_spool=host_spool)

    def routes(app, _runtime):
        async def read(request):
            require_context(request, "config.publish")
            try:
                value = await request.json()
            except (ValueError, UnicodeError):
                raise AppError("invalid_json") from None
            if not isinstance(value, dict):
                raise AppError("invalid_input")
            return value

        async def stats(request):
            body = await read(request)
            try:
                result = await statistics.query(body, context=require_context(request, "config.publish"))
            except AppError:
                raise
            except Exception:
                return json_response({"status": "unavailable", "overview": None, "series": [],
                                      "breakdowns": {}, "coverage": {"complete": False,
                                      "missing": ["statistics_unavailable"]}, "as_of": None})
            return json_response({"status": "available", **result})

        async def incident_list(request):
            return json_response(await incidents.list(await read(request)))

        async def incident_get(request):
            body = await read(request)
            incident = await incidents.get(body.get("incident_id"))
            incident["registered_actions"] = list(executor.applicable(incident))
            return json_response(incident)

        async def settings(request):
            await read(request)
            return json_response(await incidents.settings())

        async def settings_save(request):
            context = require_context(request, "config.publish")
            return json_response(await incidents.save_settings(context, await command_body(request)))

        async def recover(request):
            context = require_context(request, "config.publish")
            body = await command_body(request)
            if body.get("action") not in executor.actions:
                raise AppError("recovery_action_unavailable", status=503)
            return json_response(await incidents.request_recovery(context, body))

        app.router.add_post("/api/v1/system/statistics", stats)
        app.router.add_post("/api/v1/operations/incidents", incident_list)
        app.router.add_post("/api/v1/operations/incidents/get", incident_get)
        app.router.add_post("/api/v1/operations/settings", settings)
        app.router.add_post("/api/v1/operations/settings/save", settings_save)
        app.router.add_post("/api/v1/operations/recover", recover)

    async def start(services):
        if services.role == "operations":
            await agent.start()
            services.role_ready = agent.status

    async def stop(services):
        if services.role == "operations":
            await agent.stop()

    module = DomainModule("operations", routes, start=start, stop=stop, start_roles=("operations",),
        migrations=(Migration("operations-0001", files("tianshu.operations").joinpath("schema.sql").read_text()),))
    return OperationsComponents(module, statistics, incidents, executor, agent)
