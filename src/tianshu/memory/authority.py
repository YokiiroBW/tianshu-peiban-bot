"""Stable serialization of the authority attached to a fixed Memory request."""

from copy import deepcopy

from tianshu.runtime.serialization import wire


def context_record(context):
    value = deepcopy(wire(context))
    # Scope and audience order does not grant authority. Permission frozensets also
    # have a different iteration order after a process restart.
    for name in ("scope_ids", "audience", "permissions"):
        value[name] = sorted(value.get(name, ()))
    return value


def request_identity(request, *, canonical_capabilities=True):
    value = deepcopy(wire(request))
    value.pop("budget", None)
    for name in ("execution_timeout_seconds", "business_deadline_at"):
        if value.get(name) is None:
            value.pop(name, None)
    # Old wire requests omitted scope and used the conversation provider cache.
    # Explicit conversation preserves that identity; new intent scope is distinct.
    if value.get("provider_session_scope") == "conversation":
        value.pop("provider_session_scope")
    value["context"] = context_record(value["context"])
    if canonical_capabilities and "required_capabilities" in value:
        value["required_capabilities"] = sorted(value["required_capabilities"])
    return value


def request_digest_matches(request, digest):
    """Keep immutable legacy reservations whose only difference is set order."""
    from itertools import permutations
    from tianshu.runtime.commands import body_hash

    canonical = request_identity(request)
    if body_hash(canonical) == digest:
        return True
    legacy = request_identity(request, canonical_capabilities=False)
    if body_hash(legacy) == digest:
        return True
    # Historical writers serialized the frozenset after reconstruction. All
    # remaining fields stay byte-for-byte canonical; never ignore a route,
    # source, audience, generation, or actual capability change.
    capabilities = canonical.get("required_capabilities", ())
    if len(capabilities) <= 6:
        return any(body_hash({**canonical, "required_capabilities": list(order)}) == digest
                   for order in permutations(capabilities))
    return False
