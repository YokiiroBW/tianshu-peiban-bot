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


def request_identity(request):
    value = deepcopy(wire(request))
    value.pop("budget", None)
    value["context"] = context_record(value["context"])
    return value
