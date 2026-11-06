"""One finite, call-local budget for source locks and authority proofs."""

from itertools import islice

from tianshu.runtime.errors import AppError


_LIMITS = {"nodes": 8192, "edges": 65536, "work": 1000000,
           "inputs": 32768, "bytes": 33554432}


class SourceBudget:
    def __init__(self):
        self.remaining = dict(_LIMITS)

    def charge(self, **amounts):
        remaining = self.remaining.copy()
        for name, amount in amounts.items():
            remaining[name] -= amount
        if any(value < 0 for value in remaining.values()):
            raise AppError("source_dependency_budget_exceeded", status=503)
        self.remaining = remaining

    def update(self, remaining):
        if (not isinstance(remaining, dict) or set(remaining) != set(_LIMITS)
            or any(type(value) is not int or not 0 <= value <= self.remaining[name]
                   for name, value in remaining.items())):
            raise AppError("source_proof_incomplete", status=503)
        self.remaining = dict(remaining)


def bounded_tuple(values, limit):
    """Materialize at most the available input budget plus one sentinel."""
    result = tuple(islice(values, max(limit, 0) + 1))
    if len(result) > limit:
        raise AppError("source_dependency_budget_exceeded", status=503)
    return result
