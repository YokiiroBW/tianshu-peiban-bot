"""All provider I/O enters the model Broker's one physical-resource budget."""

from datetime import UTC, datetime, timedelta
from tianshu.types import RequestBudget, ServiceClass
from tianshu.models.types import ExternalRequest
from .contracts import Fault


def request(action, attempt, profile, function, *, units=None, service_class=None):
    quota = profile.get("quota_group")
    if not quota:
        raise Fault("quota_group_not_configured", 503)
    return ExternalRequest(
        intent_id=action.action_id,
        attempt_id=attempt.attempt_id,
        context=action.context,
        budget=RequestBudget(action.deadline_at or datetime.now(UTC) + timedelta(minutes=10)),
        execution_timeout_seconds=600, business_deadline_at=action.deadline_at,
        quota_group=quota,
        function=function,
        service_class=service_class or execution_service_class(action),
        provider_id=profile.get("provider_id", profile.get("engine", "fish")),
        billing_account=profile.get("account_id", profile.get("id")),
        estimated_units=units or {},
        source_refs=action.source_refs,
    )


class ProviderError(Fault):
    def __init__(self, code, status=503, *, effect="none"):
        super().__init__(code, status)
        self.effect = effect


def execution_service_class(action):
    """Nested work inherits its admitted class; a direct caller supplies trusted origin."""
    from .execution_context import CURRENT_EXECUTION

    current = CURRENT_EXECUTION.get()
    return (
        current.get("service_class")
        if current and current.get("service_class")
        else ServiceClass.INTERACTIVE
        if action.origin_kind.value == "human"
        else ServiceClass.ELASTIC
    )
