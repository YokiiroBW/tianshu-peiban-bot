"""Internal material races are distinct from caller version conflicts."""
import pytest

from tianshu.conversation.persistence import MaterialSnapshotChanged, lock_material_row, retry_material_snapshot
from tianshu.runtime.errors import AppError


@pytest.mark.asyncio
async def test_source_check_then_row_change_retries_only_after_transaction_rollback(database):
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE test_material(id text PRIMARY KEY,revision integer,source_refs jsonb)")
        await connection.execute("INSERT INTO test_material VALUES ('one',1,'[]')")
    checks = []

    class Sources:
        async def validate_persistence(self, _connection, _context, _refs):
            checks.append("source")
            if len(checks) == 1:
                async with database.transaction() as concurrent:
                    await concurrent.execute("UPDATE test_material SET revision=2 WHERE id='one'")
            return True

    async def read_and_lock():
        async with database.transaction() as connection:
            row = await lock_material_row(connection, Sources(), None,
                "SELECT * FROM test_material WHERE id=%s FOR UPDATE", ("one",))
            await connection.execute("UPDATE test_material SET revision=revision+1 WHERE id='one'")
            return row["revision"]

    assert await retry_material_snapshot(read_and_lock) == 2
    assert len(checks) == 2
    async with database.transaction(read_only=True) as connection:
        row = await (await connection.execute("SELECT revision FROM test_material WHERE id='one'")).fetchone()
    assert row["revision"] == 3


@pytest.mark.asyncio
async def test_public_conflict_and_unconfirmed_remote_rollback_are_never_retried():
    calls = []

    async def public_conflict():
        calls.append(1)
        raise AppError("revision_conflict", status=409)

    with pytest.raises(AppError) as failure:
        await retry_material_snapshot(public_conflict)
    assert failure.value.code == "revision_conflict" and len(calls) == 1
    calls.clear()

    async def uncertain():
        calls.append(1)
        error = MaterialSnapshotChanged(2)
        error.transaction_rollback_confirmed = True
        error.remote_rollback_confirmed = False
        raise error

    with pytest.raises(MaterialSnapshotChanged):
        await retry_material_snapshot(uncertain)
    assert len(calls) == 1
