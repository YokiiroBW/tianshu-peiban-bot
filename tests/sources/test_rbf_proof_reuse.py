"""Shared source proofs stay equivalent to the original recursive authority."""

from importlib.resources import files
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef


_spec = spec_from_file_location(
    "rbf_proof_reuse_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


def _ref(name, locator=None):
    return {"source_id": name, "revision": 1, "locator": locator or {}}


def _node(name, groups=(), *, forgotten=(), disclosure=False):
    return {
        "valid": True, "scopes": ["visible"], "length": 8,
        "forgotten": list(forgotten), "disclosure": disclosure,
        "groups": list(groups), "_proof_ref": _ref(name),
    }


async def _reuse(connection, groups, nodes):
    row = await (await connection.execute(
        "SELECT sources._rbj_evaluate(%s::jsonb,%s::text[],%s::jsonb,%s)->'allowed' AS allowed",
        (Jsonb(groups), ["visible"], Jsonb(nodes), 1000000),
    )).fetchone()
    return row["allowed"]


async def test_reuse_handles_shared_dag_ranges_cycles_and_deep_graphs(core):
    leaf, left, right, root = (_ref(name) for name in ("leaf", "left", "right", "root"))
    nodes = {
        "leaf:1": _node("leaf"),
        "left:1": _node("left", ([leaf],)),
        "right:1": _node("right", ([leaf],)),
        "root:1": _node("root", ([left, right],), forgotten=({"start": 0, "end": 1},)),
    }
    groups = [
        [_ref("root", {"start": 1, "end": 3})], [root],
        [_ref("root", {"start": 0, "end": 1})], [leaf], [],
    ]
    async with core.database.transaction() as conn:
        assert await _reuse(conn, groups, nodes) == [True, False, False, True, True]

        ranged = dict(nodes)
        ranged["left:1"] = _node("left", ([_ref("leaf", {"start": 1, "end": 2})],))
        assert await _reuse(conn, groups, ranged) == [True, False, False, True, True]

        cyclic = dict(nodes)
        cyclic["leaf:1"] = _node("leaf", ([root],))
        assert await _reuse(conn, groups, cyclic) == [False, False, False, False, True]

        def chain(size):
            nodes = {}
            for index in range(size):
                children = [_ref(f"deep-{index + 1}")] if index < size - 1 else []
                if index == 0:
                    children.append(_ref(f"deep-{size - 1}"))  # Shared leaf enables reuse.
                nodes[f"deep-{index}:1"] = _node(
                    f"deep-{index}", (children,) if children else ()
                )
            return nodes

        assert await _reuse(conn, [[_ref("deep-0")]], chain(65)) == [True]
        assert await _reuse(conn, [[_ref("deep-0")]], chain(66)) == [True]


async def _seed_sources(core, alice, bob):
    async with core.database.transaction() as conn:
        scopes, epochs = await core.identity.intake_scope(alice, connection=conn)
        other_scopes, other_epochs = await core.identity.intake_scope(bob, connection=conn)
        await conn.execute(
            "INSERT INTO sources.originals(source_id,source_key,owner_id,person_id,actor_id,head_revision) "
            "SELECT 'reuse-'||n,'reuse-key-'||n,%s,%s,%s,1 FROM generate_series(0,80) n",
            (alice.principal_id, alice.person_id, alice.actor_id),
        )
        await conn.execute(
            "INSERT INTO sources.versions(source_id,revision,intake_epoch,scope_ids,scope_epochs,audience,"
            "origin_kind,evidence_unit,content,locator,coverage,occurred_at) "
            "SELECT 'reuse-'||n,1,1,%s::text[],%s::jsonb,%s::text[],'human',"
            "'reuse-event-'||n,'abcdefgh','{}'::jsonb,'{}'::jsonb,now() FROM generate_series(0,80) n",
            (list(scopes), Jsonb(epochs), list(scopes)),
        )
        await conn.execute(
            "UPDATE sources.versions SET scope_ids=%s,scope_epochs=%s,audience=%s "
            "WHERE source_id='reuse-0' AND revision=1",
            (list(other_scopes), Jsonb(other_epochs), list(other_scopes)),
        )
        await conn.execute(
            "UPDATE sources.versions SET disclosure=true WHERE source_id='reuse-1' AND revision=1"
        )
        edges = (
            (1, "0", 0), (2, "0", 0), (5, "0", 1), (5, "0", 3),
            (6, "0", 2), (6, "1", 5), (7, "0", 5), (7, "0", 6),
            (8, "0", 5), (8, "0", 6),
        )
        for parent, group, child in edges:
            await conn.execute(
                "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
                "support_revision,support_locator,support_key) "
                "VALUES(%s,1,%s,%s,1,'{}'::jsonb,%s)",
                (f"reuse-{parent}", group, f"reuse-{child}", f"{parent}:{group}:{child}"),
            )
        await conn.execute(
            "INSERT INTO sources.forgotten_fragments VALUES('reuse-7',1,0,1)"
        )


async def _install_original(connection):
    source = files("tianshu.sources").joinpath("functions.sql").read_text(encoding="utf-8")
    body = source.split("CREATE FUNCTION sources.check_groups(", 1)[1]
    body = body.split("CREATE FUNCTION sources.any_support_usable(", 1)[0]
    await connection.execute("CREATE FUNCTION sources._rbf_original_groups(" + body, prepare=False)


async def test_incremental_upgrade_preserves_function_identity_permissions_and_jit(core):
    source = files("tianshu.sources").joinpath("functions.sql").read_text(encoding="utf-8")
    body = source.split("CREATE FUNCTION sources.check_groups(", 1)[1]
    body = body.split("CREATE FUNCTION sources.any_support_usable(", 1)[0]
    migration = files("tianshu.sources").joinpath("rbf_source_proof_reuse.sql").read_text(
        encoding="utf-8"
    )
    query = (
        "SELECT oid,prosrc,proowner,proacl,prosecdef,provolatile,proparallel,proisstrict,proconfig "
        "FROM pg_proc WHERE oid='sources.check_groups(jsonb,text[])'::regprocedure"
    )
    async with core.database.transaction() as conn:
        # Reconstruct the deployed function, including its separately installed JIT option.
        await conn.execute("CREATE OR REPLACE FUNCTION sources.check_groups(" + body, prepare=False)
        await conn.execute("ALTER FUNCTION sources.check_groups(jsonb, text[]) SET jit=off")
        before = await (await conn.execute(query)).fetchone()
        await conn.execute(migration, prepare=False)
        after = await (await conn.execute(query)).fetchone()
        assert before["prosrc"] != after["prosrc"]
        assert {key: value for key, value in before.items() if key != "prosrc"} == {
            key: value for key, value in after.items() if key != "prosrc"
        }
        assert "jit=off" in after["proconfig"]


async def _compare(connection, groups, scopes):
    arguments = (Jsonb(wire(groups)), list(scopes), Jsonb(wire(groups)), list(scopes))
    return await (await connection.execute(
        "SELECT sources.check_groups(%s::jsonb,%s::text[]) AS current, "
        "sources._rbf_original_groups(%s::jsonb,%s::text[]) AS original", arguments,
    )).fetchone()


async def test_shared_dag_preserves_authority_ranges_alternatives_and_freshness(core, ordinary_users):
    alice, bob = ordinary_users
    await _seed_sources(core, alice, bob)
    safe = SourceRef("reuse-7", 1, {"start": 1, "end": 3})
    forgotten = SourceRef("reuse-7", 1, {"start": 0, "end": 1})
    noninteger_text = SourceRef("reuse-7", 1, {"start": 1.0, "end": 3.0})
    missing = SourceRef("reuse-missing", 1, {})
    huge = SourceRef("reuse-3", 1, {"start": 10**30, "end": 10**30})
    groups = (
        (safe,), (SourceRef("reuse-6", 1, {}),), (SourceRef("reuse-2", 1, {}),),
        (SourceRef("reuse-1", 1, {}),), (forgotten,), (), (SourceRef("reuse-7", 1, {}),),
        (SourceRef("reuse-8", 1, {}),), (safe, SourceRef("reuse-3", 1, {})),
        (missing, huge), (noninteger_text,), (safe, safe),
    )
    expected = [True, True, False, True, False, True, False, True, True, False, False, True]
    async with core.database.transaction() as conn:
        await conn.execute("SET LOCAL jit = off")
        await _install_original(conn)
        compared = await _compare(conn, groups, alice.scope_ids)
        assert compared["current"] == compared["original"] == expected
        denied = await _compare(conn, ((safe,),), bob.scope_ids)
        assert denied["current"] == denied["original"] == [False]

        # A huge numeric locator is not examined after a preceding false ref.
        for function in ("sources.check_groups", "sources._rbf_original_groups"):
            with pytest.raises(psycopg.Error) as failure:
                async with conn.transaction():
                    await conn.execute(
                        f"SELECT {function}(%s::jsonb,%s::text[])",
                        (Jsonb(wire(((huge,), (safe,)))), list(alice.scope_ids)),
                    )
            assert failure.value.sqlstate == "22003"

        # An earlier successful statement is never cached across a withdrawal.
        await conn.execute("UPDATE sources.originals SET state='retracted' WHERE source_id='reuse-3'")
        compared = await _compare(conn, ((safe,), (SourceRef("reuse-8", 1, {}),)), alice.scope_ids)
        assert compared["current"] == compared["original"] == [False, False]


async def test_ranged_support_cycles_and_depth_retain_original_lazy_failures(core, ordinary_users):
    alice, bob = ordinary_users
    await _seed_sources(core, alice, bob)
    missing = SourceRef("reuse-missing", 1, {})
    parent = SourceRef("reuse-5", 1, {})
    async with core.database.transaction() as conn:
        await conn.execute("SET LOCAL jit = off")
        await _install_original(conn)
        await conn.execute(
            "UPDATE sources.supports SET support_locator=%s "
            "WHERE source_id='reuse-5' AND support_source_id='reuse-3'",
            (Jsonb({"start": 10**30, "end": 10**30}),),
        )
        compared = await _compare(conn, ((missing, parent),), alice.scope_ids)
        assert compared["current"] == compared["original"] == [False]
        for function in ("sources.check_groups", "sources._rbf_original_groups"):
            with pytest.raises(psycopg.Error) as failure:
                async with conn.transaction():
                    await conn.execute(
                        f"SELECT {function}(%s::jsonb,%s::text[])",
                        (Jsonb(wire(((parent,),))), list(alice.scope_ids)),
                    )
            assert failure.value.sqlstate == "22003"

        await conn.execute(
            "UPDATE sources.supports SET support_locator='{}'::jsonb "
            "WHERE source_id='reuse-5' AND support_source_id='reuse-3'"
        )
        await conn.execute(
            "INSERT INTO sources.supports VALUES('reuse-3',1,'0','reuse-5',1,'{}'::jsonb,'cycle')"
        )
        compared = await _compare(conn, ((parent,),), alice.scope_ids)
        assert compared["current"] == compared["original"] == [False]

        await conn.execute("DELETE FROM sources.supports WHERE support_key='cycle'")
        await conn.execute(
            "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
            "support_revision,support_locator,support_key) "
            "SELECT 'reuse-'||n,1,'0','reuse-'||(n+1),1,'{}'::jsonb,'depth-'||n "
            "FROM generate_series(10,75) n"
        )
        await conn.execute(
            "INSERT INTO sources.supports VALUES('reuse-10',1,'0','reuse-76',1,'{}'::jsonb,'depth-shared')"
        )
        deep = SourceRef("reuse-10", 1, {})
        compared = await _compare(conn, ((missing, deep),), alice.scope_ids)
        assert compared["current"] == compared["original"] == [False]
        # Only the historical recursive implementation has a depth cutoff.
        with pytest.raises(psycopg.Error) as failure:
            async with conn.transaction():
                await conn.execute(
                    "SELECT sources._rbf_original_groups(%s::jsonb,%s::text[])",
                    (Jsonb(wire(((deep,),))), list(alice.scope_ids)),
                )
        assert failure.value.sqlstate == "54000"
        assert await core.sources.check_many_on(conn, alice, ((deep,),)) == (True,)
