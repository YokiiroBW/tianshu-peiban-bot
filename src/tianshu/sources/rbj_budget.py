"""One finite, call-local budget for source locks and authority proofs."""

from itertools import islice
import json

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


_LIMITS = {"nodes": 8192, "edges": 65536, "work": 1000000,
           "inputs": 32768, "bytes": 33554432}


class SourceBudget:
    def __init__(self):
        self.remaining = dict(_LIMITS)
        self._locked_graph = None

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

    async def remember_locked_graph(self, connection, refs, pairs):
        """Reuse only the closed version graph, never its authorization facts."""
        if not refs:
            return
        transaction = await (await connection.execute(
            "SELECT pg_current_xact_id()::text AS transaction_id")).fetchone()
        self._locked_graph = (connection, transaction["transaction_id"],
            frozenset(_ref_key(ref) for ref in refs), pairs)

    def proof_graph(self, connection, requests):
        graph = self._locked_graph
        if (graph is None or graph[0] is not connection
                or any(_ref_key(ref) not in graph[2]
                       for _, groups, _ in requests for group in groups for ref in group)):
            return None
        return graph[1], graph[3]


def _ref_key(ref):
    return json.dumps(wire(ref), sort_keys=True, separators=(",", ":"))


def bounded_tuple(values, limit):
    """Materialize at most the available input budget plus one sentinel."""
    result = tuple(islice(values, max(limit, 0) + 1))
    if len(result) > limit:
        raise AppError("source_dependency_budget_exceeded", status=503)
    return result
