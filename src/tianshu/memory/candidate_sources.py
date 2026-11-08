"""Batch complete Memory proofs through the existing Sources authority."""

from dataclasses import dataclass
import json

from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.runtime.cancellation import transaction_on
from . import codec


SOURCE_GROUPS = 1024
SOURCE_KEYS = 2048
_BATCH_LIMITS = {"source_batch_too_large", "source_dependency_budget_exceeded"}


def _all(values):
    values = tuple(values)
    return False if False in values else None if None in values else True


def _any(values):
    values = tuple(values)
    return True if True in values else None if None in values else False


@dataclass(frozen=True)
class _Proof:
    supports: tuple[tuple[int, ...], ...]
    required: tuple[int, ...]

    def evaluate(self, checked):
        return _all((_any(_all(checked[index] for index in group) for group in self.supports),
                     _all(checked[index] for index in self.required)))


class SourceProofGroups:
    def __init__(self):
        self.values = []
        self.positions = {}

    def add(self, refs):
        # Ranges are part of identity. Splitting a large AND group does not
        # discard any root or bypass the authority's per-root closure limits.
        unique = {json.dumps(wire(ref), sort_keys=True, separators=(",", ":")): ref
                  for ref in codec.refs(refs)}
        refs = tuple(unique[key] for key in sorted(unique))
        result = []
        for start in range(0, max(len(refs), 1), SOURCE_KEYS):
            group = refs[start:start + SOURCE_KEYS]
            key = json.dumps(wire(group), sort_keys=True, separators=(",", ":"))
            if key not in self.positions:
                self.positions[key] = len(self.values)
                self.values.append(group)
            result.append(self.positions[key])
        return tuple(result)

    def proof(self, supports, *required):
        return _Proof(tuple(self.add(group) for group in supports),
                      tuple(index for refs in required if refs for index in self.add(refs)))


async def _check_on(sources, conn, context, groups):
    try:
        # A dependency-limit error aborts its SQL statement. Roll it back before
        # splitting so the surrounding cursor/transaction remains usable.
        async with transaction_on(conn):
            return tuple(await sources.check_many_on(conn, context, groups))
    except AppError as exc:
        if exc.code not in _BATCH_LIMITS:
            raise
        if len(groups) > 1:
            middle = len(groups) // 2
            left = await _check_on(sources, conn, context, groups[:middle])
            right = await _check_on(sources, conn, context, groups[middle:])
            return left + right
        group = groups[0]
        if len(group) > 1:
            middle = len(group) // 2
            values = await _check_on(sources, conn, context, (group[:middle], group[middle:]))
            return (_all(values),)
        # One root still exceeds Sources' complete dependency/depth allowance.
        # It is unknown, never an authorized or definitively unreadable proof.
        return (None,)


async def check_group_batches_on(sources, conn, context, groups):
    """Check deduplicated chunks under both Sources group and root limits."""
    checked, position = [], 0
    while position < len(groups):
        batch, keys = [], set()
        while position < len(groups) and len(batch) < SOURCE_GROUPS:
            group = groups[position]
            added = {(ref.source_id, ref.revision) for ref in group}
            if batch and len(keys | added) > SOURCE_KEYS:
                break
            batch.append(group)
            keys.update(added)
            position += 1
        checked.extend(await _check_on(sources, conn, context, tuple(batch)))
    return tuple(checked)


async def check_ref_groups_on(sources, conn, context, refs_groups):
    """Check each complete AND group, sharing only exact, call-local proofs."""
    groups = SourceProofGroups()
    positions = tuple(groups.add(refs) for refs in refs_groups)
    checked = await check_group_batches_on(sources, conn, context, groups.values)
    return tuple(_all(checked[index] for index in indexes) for indexes in positions)


async def check_rows_on(sources, conn, context, rows):
    """Return true/false/unknown per row; never return a partially proved row."""
    groups, rules = SourceProofGroups(), []
    for row in rows:
        own = groups.proof(row["_supports"], row["_derivations"], row["_explicit"])
        related = row.get("_relation_proofs")
        links = None if related is None else tuple(groups.proof(
            link["supports"], link["derivations"]) for link in related)
        rules.append((own, links))
    checked = await check_group_batches_on(sources, conn, context, groups.values)
    return tuple(_all((own.evaluate(checked), True if links is None else
                      _any(link.evaluate(checked) for link in links))) for own, links in rules)
