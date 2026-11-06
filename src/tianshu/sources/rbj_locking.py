"""Call-local, bounded discovery for Sources' existing ordered lock protocol."""

from psycopg.errors import ProgramLimitExceeded
from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError


async def _read(conn, statement, arguments, budget, field):
    try:
        async with conn.transaction():
            row = await (await conn.execute(statement, (*arguments, Jsonb(budget.remaining)))).fetchone()
    except ProgramLimitExceeded:
        raise AppError("source_dependency_budget_exceeded", status=503) from None
    result = row["result"] if row else None
    values = result.get(field) if isinstance(result, dict) else None
    item_type = dict if field == "rows" else str
    if not isinstance(values, list) or any(not isinstance(value, item_type) for value in values):
        raise AppError("source_proof_incomplete", status=503)
    budget.update(result.get("remaining"))
    return values if field == "rows" else set(values)


def _check_input(refs, extra, aliases, budget):
    count = len(refs) + len(extra) + len(aliases)
    if count > min(budget.remaining["inputs"], budget.remaining["work"]):
        raise AppError("source_dependency_budget_exceeded", status=503)
    values = (*refs, *extra, *aliases)
    if any(not isinstance(item, str) for item in values):
        raise AppError("invalid_source_reference")
    size = sum(len(item.encode("utf-8")) for item in values)
    if size > budget.remaining["bytes"]:
        raise AppError("source_dependency_budget_exceeded", status=503)


async def selected_on(conn, refs, extra, aliases, budget):
    from .proofs import normalize_groups

    refs = normalize_groups((refs,))[0]
    _check_input(tuple(ref.source_id for ref in refs), extra, aliases, budget)
    # Locators affect authorization but not which original rows must be locked.
    roots = [{"source_id": ref.source_id, "revision": ref.revision} for ref in refs]
    return await _read(
        conn, "SELECT sources._rbj_lock_select(%s::jsonb,%s::text[],%s::text[],%s::jsonb) AS result",
        (Jsonb(roots), list(extra), list(aliases)), budget, "ids",
    )


async def targets_on(conn, targets, aliases, budget):
    _check_input((), tuple(targets), aliases, budget)
    return await _read(
        conn, "SELECT sources._rbj_lock_targets(%s::text[],%s::text[],%s::jsonb) AS result",
        (sorted(targets), list(aliases)), budget, "ids",
    )


async def alias_keys_on(conn, targets, aliases, budget):
    _check_input((), tuple(targets), aliases, budget)
    return await _read(
        conn, "SELECT sources._rbj_lock_keys(%s::text[],%s::text[],%s::jsonb) AS result",
        (sorted(targets), list(aliases)), budget, "keys",
    )


async def archive_bound_on(conn, aliases, budget):
    """Read locked alias metadata without an unbounded pre-lock expansion."""
    _check_input((), (), aliases, budget)
    return await _read(
        conn, "SELECT sources._rbj_archive_bound(%s::text[],%s::jsonb) AS result",
        (list(aliases),), budget, "rows",
    )


async def evidence_manifests_on(conn, evidence_ids, budget):
    _check_input((), evidence_ids, (), budget)
    return await _read(
        conn, "SELECT sources._rbj_evidence_manifests(%s::text[],%s::jsonb) AS result",
        (list(evidence_ids),), budget, "rows",
    )


async def support_rows_on(conn, source_id, revision, budget):
    _check_input((), (source_id,), (), budget)
    return await _read(
        conn, "SELECT sources._rbj_support_rows(%s,%s,%s::jsonb) AS result",
        (source_id, revision), budget, "rows",
    )
