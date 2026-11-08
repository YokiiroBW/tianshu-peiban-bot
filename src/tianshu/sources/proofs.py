"""Source proofs retain one authority snapshot and one budget per call."""

import json

from psycopg.errors import ProgramLimitExceeded
from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import transaction_on


def normalize_groups(refs_groups):
    groups, inputs = [], 0
    for group in refs_groups:
        inputs += 1
        if inputs > 32768:
            raise AppError("source_dependency_budget_exceeded", status=503)
        refs = []
        for ref in group:
            inputs += 1
            if inputs > 32768:
                raise AppError("source_dependency_budget_exceeded", status=503)
            if (not isinstance(getattr(ref, "source_id", None), str)
                or type(getattr(ref, "revision", None)) is not int or ref.revision < 1):
                raise AppError("invalid_source_reference")
            refs.append(ref)
        groups.append(tuple(refs))
    return tuple(groups)


def unique_refs(refs):
    """Drop only exact wire-identical references, retaining the first position."""
    seen, unique = set(), []
    for ref in refs:
        key = json.dumps(wire(ref), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return tuple(unique)


def _checked_groups(value, expected):
    if (not isinstance(value, (tuple, list)) or len(value) != expected
        or any(type(item) is not bool for item in value)):
        raise AppError("source_proof_incomplete", status=503)
    return tuple(value)


async def _execute_scoped(connection, requests, budget):
    payload = [{"viewer": list(context.scope_ids), "groups": wire(groups),
                "stop_if_denied": stop} for context, groups, stop in requests]
    try:
        # Roll back a failed statement to a savepoint before exposing its typed
        # budget error; callers may still need their outer transaction for cleanup.
        async with transaction_on(connection):
            row = await (await connection.execute(
                "SELECT sources._rbj_check_contexts(%s::jsonb,%s::jsonb) AS proof",
                (Jsonb(payload), Jsonb(budget.remaining) if budget is not None else None),
            )).fetchone()
    except ProgramLimitExceeded:
        raise AppError("source_dependency_budget_exceeded", status=503) from None
    proof = row["proof"] if row else None
    values = proof.get("allowed") if isinstance(proof, dict) else None
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= len(requests):
        raise AppError("source_proof_incomplete", status=503)
    result = tuple(_checked_groups(value, len(requests[i][1])) for i, value in enumerate(values))
    stopped = next((i for i, groups in enumerate(result)
                    if requests[i][2] and not all(groups)), None)
    if ((stopped is not None and stopped != len(result) - 1)
        or (len(result) < len(requests) and stopped is None)):
        raise AppError("source_proof_incomplete", status=503)
    if budget is not None:
        budget.update(proof.get("remaining"))
    return result


async def check_many_on(identity, connection, context, refs_groups, *, budget=None):
    """Prove all groups in one statement, without resetting graph budgets."""
    await identity.check_context(context, connection=connection)
    groups = normalize_groups(refs_groups)
    if not groups:
        return ()
    if budget is not None:
        return (await _execute_scoped(connection, ((context, groups, False),), budget))[0]
    try:
        async with transaction_on(connection):
            row = await (await connection.execute(
                "SELECT sources.check_groups(%s::jsonb,%s::text[]) AS allowed",
                (Jsonb(wire(groups)), list(context.scope_ids)),
            )).fetchone()
    except ProgramLimitExceeded:
        raise AppError("source_dependency_budget_exceeded", status=503) from None
    return _checked_groups(row["allowed"] if row else None, len(groups))


async def check_scoped_on(identity, connection, requests, *, budget=None):
    """Check independently authorized contexts with a shared call budget."""
    requests = tuple((context, normalize_groups(groups), stop)
                     for context, groups, stop in requests)
    if not requests:
        return ()
    for context, _, _ in requests:
        await identity.check_context(context, connection=connection)
    return await _execute_scoped(connection, requests, budget)
