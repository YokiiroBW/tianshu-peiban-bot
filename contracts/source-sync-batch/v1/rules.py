"""Pure cross-batch consistency checks, after source-sync/v1 checks each batch."""

import hashlib
import json
from datetime import datetime


class Violation(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise Violation(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def batch_barrier(observation, now):
    """No authentication or storage side effects; callers validate all v1 relations."""
    batches = observation["observations"]
    _require(len(batches) >= 2, "batch_coverage")
    final_request = observation["final_access_request"]
    final_access = observation["final_access"]
    core_head = observation["final_core_head"]
    _require(final_request["admissions"] == [], "batch_coverage")
    _require(final_access["grants"] == [], "batch_coverage")
    _require(
        final_access["request_id"] == final_request["request_id"]
        and final_access["request_digest"]
        == hashlib.sha256(_canonical(final_request).encode("utf-8")).hexdigest(),
        "correlation",
    )
    seen = set()
    for index, batch in enumerate(batches):
        _require(batch["snapshot"]["head"] == batch["final_head"] == core_head, "moving_owner")
        _require(batch["access"]["head"] == final_access["head"], "moving_owner")
        _require(batch["access_request"]["viewer"] == final_request["viewer"], "viewer_actor")
        _require(batch["access"]["viewer_context"] == final_access["viewer_context"], "viewer_actor")
        _require(index == 0 or not batch["request"]["turn_ids"], "turn_coverage")
        selectors = batch["request"]["selectors"]
        _require(0 < len(selectors) <= 256, "batch_coverage")
        keys = {_canonical(selector) for selector in selectors}
        _require(len(keys) == len(selectors) and not seen.intersection(keys), "batch_coverage")
        seen.update(keys)
    viewer = final_request["viewer"]
    context = final_access["viewer_context"]
    if viewer is None:
        _require(context is None, "viewer_actor")
    else:
        _require(
            context is not None
            and context["issuer"] == "platform"
            and context["allowed_scope"] == viewer["scope"]
            and context["assertion_ref"] == viewer["origin"]["assertion_ref"]
            and context["audience_service"] == "memory"
            and context["authenticated_service"] == "companion"
            and not context["revoked"]
            and datetime.fromisoformat(context["expires_at"]) > datetime.fromisoformat(now),
            "viewer_actor",
        )
    return True
