"""The source proof's JIT setting is local and leaves authorization unchanged."""

from datetime import UTC, datetime
from importlib.resources import files
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tianshu.conversation.types import InputPart
from tianshu.types import OriginKind, SourceRef


_spec = spec_from_file_location(
    "rbf_proof_jit_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


class _RestoreProbe(Exception):
    pass


async def test_proof_jit_is_function_local_and_preserves_authority(core, ordinary_users):
    alice, bob = ordinary_users
    original = (await core.sources.record_event(
        alice, producer="web", event_id="proof-jit", revision=1,
        occurred_at=datetime.now(UTC), parts=(InputPart("text", text="abcdef"),),
        origin_kind=OriginKind.HUMAN,
    ))[0]
    await core.sources.forget_fragment(alice, {
        "source_id": original.source_id, "expected_revision": 1,
        "start": 0, "end": 1, "client_request_id": "proof-jit-fragment",
    })
    safe = SourceRef(original.source_id, 1, {"start": 1, "end": 3})
    forgotten = SourceRef(original.source_id, 1, {"start": 0, "end": 1})
    missing = SourceRef("proof-jit-missing", 1, {})
    groups = ((safe,), (forgotten,), (safe, missing), ())
    expected = (True, False, False, True)
    migration = files("tianshu.sources").joinpath("rbf_source_proof_jit.sql").read_text(
        encoding="utf-8"
    )

    async with core.database.transaction() as conn:
        await conn.execute("SET LOCAL jit = on")
        # Domain fixtures apply current migrations; reconstruct the deployed
        # pre-migration function setting to exercise the actual upgrade.
        await conn.execute("ALTER FUNCTION sources.check_groups(jsonb, text[]) RESET jit")
        before = await (await conn.execute(
            "SELECT oid,prosrc,proowner,proacl,prosecdef,provolatile,proparallel,proisstrict,proconfig "
            "FROM pg_proc WHERE oid='sources.check_groups(jsonb,text[])'::regprocedure"
        )).fetchone()
        assert not any(value.startswith("jit=") for value in (before["proconfig"] or ()))
        assert await core.sources.check_many_on(conn, alice, groups) == expected
        assert await core.sources.check_many_on(conn, bob, ((safe,),)) == (False,)

        await conn.execute(migration, prepare=False)
        after = await (await conn.execute(
            "SELECT oid,prosrc,proowner,proacl,prosecdef,provolatile,proparallel,proisstrict,proconfig "
            "FROM pg_proc WHERE oid='sources.check_groups(jsonb,text[])'::regprocedure"
        )).fetchone()
        assert {key: after[key] for key in before if key != "proconfig"} == {
            key: value for key, value in before.items() if key != "proconfig"
        }
        assert set(after["proconfig"]) == set(before["proconfig"] or ()) | {"jit=off"}
        assert await core.sources.check_many_on(conn, alice, groups) == expected
        assert await core.sources.check_many_on(conn, bob, ((safe,),)) == (False,)
        assert (await (await conn.execute("SHOW jit")).fetchone())["jit"] == "on"

        # Observe the setting inside a nested call, then roll back the temporary
        # probe so the actual authority function remains installed.
        with pytest.raises(_RestoreProbe):
            async with conn.transaction():
                await conn.execute("""
                    CREATE OR REPLACE FUNCTION sources.valid_reference(
                        ref jsonb, nodes jsonb, viewer text[], require_access boolean,
                        visited text[] DEFAULT '{}'
                    ) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
                    BEGIN
                        RETURN current_setting('jit') = 'off';
                    END $$
                """)
                assert await core.sources.check_many_on(conn, alice, ((safe,),)) == (True,)
                assert (await (await conn.execute("SHOW jit")).fetchone())["jit"] == "on"
                raise _RestoreProbe()
        assert await core.sources.check_many_on(conn, alice, groups) == expected
