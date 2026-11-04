"""Batch source proofs while preserving every logical group's AND semantics."""

from psycopg.errors import ProgramLimitExceeded
from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire


_REFERENCES_PER_BATCH = 2048
_GROUPS_PER_BATCH = 1024


def normalize_groups(refs_groups):
    groups = tuple(tuple(group) for group in refs_groups)
    for group in groups:
        for ref in group:
            if (not isinstance(getattr(ref, "source_id", None), str)
                or type(getattr(ref, "revision", None)) is not int or ref.revision < 1):
                raise AppError("invalid_source_reference")
    return groups


def _batches(groups, reference_limit):
    batches, group_positions = [], []
    current, positions, count = [], [], 0

    def flush():
        nonlocal current, positions, count
        if current:
            batches.append(current)
            group_positions.append(positions)
            current, positions, count = [], [], 0

    for index, group in enumerate(groups):
        parts = (
            (group[offset:offset + reference_limit]
             for offset in range(0, len(group), reference_limit))
            if group else ((),)
        )
        for part in parts:
            if current and (len(current) >= _GROUPS_PER_BATCH or count + len(part) > reference_limit):
                flush()
            current.append(part)
            positions.append(index)
            count += len(part)
    flush()
    return batches, group_positions


async def check_many_on(identity, connection, context, refs_groups):
    """Prove all groups in one statement; split oversized shared dependency graphs."""
    await identity.check_context(context, connection=connection)
    groups = normalize_groups(refs_groups)
    if not groups:
        return ()

    reference_limit = _REFERENCES_PER_BATCH
    while True:
        batches, positions = _batches(groups, reference_limit)
        try:
            # A nested transaction is a savepoint: PG aborts a statement on its
            # dependency budget exception, so roll it back before splitting.
            # Every successful batch is evaluated in one SQL statement and thus
            # uses one source-state snapshot even on read-only connections.
            async with connection.transaction():
                rows = await (await connection.execute(
                    "SELECT q.ordinal,sources.check_groups(q.ref_groups,%s::text[]) AS allowed "
                    "FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS q(ref_groups,ordinal) "
                    "ORDER BY q.ordinal",
                    (list(context.scope_ids), Jsonb(wire(batches))),
                )).fetchall()
        except ProgramLimitExceeded:
            largest = max(sum(len(part) for part in batch) for batch in batches)
            largest_roots = max(
                len({(ref.source_id, ref.revision) for part in batch for ref in part})
                for batch in batches
            )
            if largest_roots <= 1:
                raise AppError("source_dependency_budget_exceeded", status=503) from None
            reference_limit = max(1, min(reference_limit // 2, largest // 2))
            continue

        if (len(rows) != len(positions) or any(
            row["allowed"] is None or len(row["allowed"]) != len(owners)
            or any(type(value) is not bool for value in row["allowed"])
            for row, owners in zip(rows, positions)
        )):
            raise AppError("source_proof_incomplete", status=503)
        results = [True] * len(groups)
        for row, owners in zip(rows, positions):
            for owner, allowed in zip(owners, row["allowed"]):
                results[owner] = results[owner] and allowed
        return tuple(results)
