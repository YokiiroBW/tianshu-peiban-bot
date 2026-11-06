"""Large source closures retain every reference, group, range, and lock."""

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef


_spec = spec_from_file_location(
    "rb_proof_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


async def _seed_sources(core, context, count):
    async with core.database.transaction() as conn:
        scopes, epochs = await core.identity.intake_scope(context, connection=conn)
        await conn.execute(
            "INSERT INTO sources.originals(source_id,source_key,owner_id,person_id,actor_id,head_revision) "
            "SELECT 'batch-source-'||n,'batch-key-'||n,%s,%s,%s,1 FROM generate_series(0,%s-1) n",
            (context.principal_id, context.person_id, context.actor_id, count),
        )
        await conn.execute(
            "INSERT INTO sources.versions(source_id,revision,intake_epoch,scope_ids,scope_epochs,audience,"
            "origin_kind,evidence_unit,content,locator,coverage,occurred_at) "
            "SELECT 'batch-source-'||n,1,1,%s::text[],%s::jsonb,%s::text[],'human',"
            "'batch-event-'||n,'value-'||n,'{}'::jsonb,'{}'::jsonb,now() "
            "FROM generate_series(0,%s-1) n",
            (list(scopes), Jsonb(epochs), list(scopes), count),
        )
    return tuple(SourceRef(f"batch-source-{index}", 1, {}) for index in range(count))


async def test_large_groups_preserve_results_ranges_and_global_locks(core, ordinary_users):
    alice, _ = ordinary_users
    references = await _seed_sources(core, alice, 2101)
    groups = (references, (), *((ref,) for ref in references[:1100]))
    assert await core.sources.check_many(alice, groups) == (True,) * len(groups)
    async with core.database.transaction(read_only=True) as conn:
        assert await core.sources.check_many_on(conn, alice, (references, ())) == (True, True)

    started = asyncio.Event()

    async def retract_last():
        started.set()
        await core.sources.invalidate(alice, {
            "source_id": references[-1].source_id, "expected_revision": 1,
            "client_request_id": "withdraw-last-source",
        })

    async with core.database.transaction() as conn:
        assert await core.sources.validate_persistence(conn, alice, tuple(reversed(references)))
        pending = asyncio.create_task(retract_last())
        await started.wait()
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
    await pending
    assert await core.sources.check_many(
        alice, (references, (references[0],), (references[-1],), ())
    ) == (False, True, False, True)

    await core.sources.forget_fragment(alice, {
        "source_id": references[0].source_id, "expected_revision": 1,
        "start": 0, "end": 1, "client_request_id": "forget-one-fragment",
    })
    safe = SourceRef(references[0].source_id, 1, {"start": 1, "end": 2})
    forgotten = SourceRef(references[0].source_id, 1, {"start": 0, "end": 1})
    large_safe_group = (*references[1:-1], safe)
    assert await core.sources.check_many(
        alice, (large_safe_group, (forgotten, *large_safe_group))
    ) == (True, False)
    async with core.database.transaction() as conn:
        assert await core.sources.validate_persistence(conn, alice, large_safe_group)
        assert not await core.sources.validate_persistence(conn, alice, (forgotten, *large_safe_group))


async def test_shared_dependency_budget_is_not_reset_by_splitting(core, ordinary_users):
    alice, _ = ordinary_users
    references = await _seed_sources(core, alice, 8195)
    roots = references[:3]
    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
            "support_revision,support_locator,support_key) "
            "SELECT 'batch-source-'||(n%3),1,'0','batch-source-'||(n+3),1,'{}'::jsonb,"
            "'support-'||n FROM generate_series(0,8191) n"
        )
    # The full public call has one node budget; splitting must not renew it.
    with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
        await core.sources.check_many(alice, (roots,))
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await core.sources.validate_persistence(conn, alice, roots)
    # A proof without locking can still evaluate one complete smaller graph.
    async with core.database.transaction(read_only=True) as conn:
        assert await core.sources.check_many_on(conn, alice, ((roots[0],),)) == (True,)

    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
            "support_revision,support_locator,support_key) "
            "SELECT 'batch-source-0',1,'0','batch-source-'||(n+3),1,'{}'::jsonb,"
            "'support-'||n FROM generate_series(0,8191) n ON CONFLICT DO NOTHING"
        )
    # A single root still exceeding the dependency limit is reported, never truncated or accepted.
    with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
        await core.sources.check_many(alice, ((roots[0],),))
