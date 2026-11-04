"""A rebuilt installation is empty once; later starts retain its new data."""

from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import migrate, product_migrations
from tianshu.runtime.recovery_policy import RecoveryPolicy


async def test_repeated_start_keeps_new_generation_and_post_installation_data(empty_database):
    database = empty_database
    jobs = JobQueue(database.config.dsn, database=database)
    await migrate(database, product_migrations())
    async with database.transaction() as conn:
        generation = await jobs.current_generation_on(conn)
        await conn.execute(
            "INSERT INTO runtime.commands(principal_id,operation,client_request_id,body_hash,result) "
            "VALUES('fixture-owner','fixture.created_after_rebuild','new-command','fixture-digest','{}')"
        )
        lineage = await RecoveryPolicy().register_on(
            conn, domain="fixture", subject_id="new-subject", owner="fixture-owner",
            material_id="new-material", material_digest="new-digest", dependency="new-dependency",
            current_version="v1", max_attempts=3, recovery_kind="pure_computation",
        )
    await migrate(database, product_migrations())
    async with database.transaction(read_only=True) as conn:
        assert await jobs.current_generation_on(conn) == generation
        row = await (await conn.execute(
            "SELECT client_request_id FROM runtime.commands WHERE principal_id='fixture-owner'"
        )).fetchone()
        assert row['client_request_id'] == 'new-command'
        retained = await (await conn.execute(
            "SELECT recovery_kind,attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
            (lineage['lineage_id'],),
        )).fetchone()
        assert retained == {'recovery_kind': 'pure_computation', 'attempts': 0}
        assert await (await conn.execute(
            "SELECT to_regclass('memory.reflections') AS retired_learning_owner"
        )).fetchone() == {'retired_learning_owner': None}
