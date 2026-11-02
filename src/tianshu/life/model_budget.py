"""Life output headroom uses the selected existing native route."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import re

from tianshu.models.budgets import with_output_tokens
from tianshu.models.types import ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget, ServiceClass


INPUT_TOKENS = 16000
DIARY_INPUT_TOKENS = 200000
DEFAULT_OUTPUT_TOKENS = 16384
DEFAULT_EXECUTION_SECONDS = 300


async def generation_profile(models, context, *, function):
    # Configuration is legitimate decision material, including a not-yet-probed
    # route. Read it without claiming the capability being examined.
    draft = ModelRequest("life-profile", "life-profile", function, ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=30)), (),
        max_output_tokens=DEFAULT_OUTPUT_TOKENS, required_capabilities=frozenset(),
        execution_timeout_seconds=30)
    return await models.generation_profile(draft)


async def generation_request(models, context, *, key, attempt_id, function, profile=None, business_deadline_at=None):
    """Profile only: no claim, permit, provider request or synthetic continuation."""
    draft = ModelRequest(key, attempt_id, function, ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC) + timedelta(seconds=30)), (),
        max_output_tokens=DEFAULT_OUTPUT_TOKENS, required_capabilities=frozenset({"text", "stream"}),
        execution_timeout_seconds=DEFAULT_EXECUTION_SECONDS, business_deadline_at=business_deadline_at)
    profile = profile if profile is not None else await generation_profile(models, context, function=function)
    if not {"text", "stream"} <= set(profile["verified_capabilities"]):
        raise AppError("model_capability_unverified", status=422)
    model = profile["model"].lower()
    # These are requested budgets for the configured routes, never assertions
    # about an unverified model's streaming/continuation or advertised maximum.
    preferred = 32768 if re.search(r"gemini[-_. ]?(?:3[._]8|4)(?:[-_. ]|$)", model) else DEFAULT_OUTPUT_TOKENS
    output = max(preferred, profile["max_output_tokens"])
    seconds = 600 if output >= 32768 else DEFAULT_EXECUTION_SECONDS
    return replace(draft, budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=seconds)),
        execution_timeout_seconds=seconds, business_deadline_at=business_deadline_at,
        route_id=profile["route_id"], route_revision=profile["route_revision"], protocol=profile["protocol"],
        max_output_tokens=output, parameters=with_output_tokens(profile["protocol"], profile["output_parameters"], output))
