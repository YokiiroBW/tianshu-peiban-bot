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


async def test_upgrade_keeps_original_unknown_and_confirms_only_existing_admission(empty_database):
    database = empty_database
    jobs = JobQueue(database.config.dsn, database=database)
    migrations = product_migrations()
    await migrate(database, migrations[:1])
    async with database.transaction() as conn:
        generation = await jobs.current_generation_on(conn)
        await conn.execute(
            "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,function,service_class,"
            "route_id,route_revision,provider_id,model,protocol,quota_group,request_digest,"
            "context_fingerprint,status,dispatch,data_generation) "
            "VALUES('original','material-original','owner','life.plan','elastic','route',1,'provider',"
            "'model','openai.chat','quota','original-digest','context','unknown','unknown',%s)",
            (generation,),
        )
        for identifier in ('original', 'unconfirmed'):
            await conn.execute(
                "INSERT INTO conversation.generation_requests(material_key,attempt_id,context_key,"
                "request,source_refs,state) VALUES(%s,%s,'context','{\"frozen\":true}','[]','claimed')",
                ('material-' + identifier, identifier),
            )
            await conn.execute(
                "INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,"
                "base_revision,context) VALUES(%s,'actor',CURRENT_DATE,%s,1,'{}')",
                ('material-' + identifier, identifier),
            )
    await migrate(database, migrations)
    await migrate(database, migrations)
    async with database.transaction(read_only=True) as conn:
        assert await jobs.current_generation_on(conn) == generation
        original = await (await conn.execute(
            "SELECT status,dispatch,request_digest FROM models.attempts WHERE attempt_id='original'"
        )).fetchone()
        assert original == {'status': 'unknown', 'dispatch': 'unknown', 'request_digest': 'original-digest'}
        rows = await (await conn.execute(
            "SELECT request.attempt_id,request.request,request.submission_confirmed,"
            "plan.submission_confirmed AS plan_confirmed,plan.admission_started "
            "FROM conversation.generation_requests request JOIN life.plan_generations plan "
            "ON plan.generation_key=request.material_key ORDER BY request.attempt_id"
        )).fetchall()
        assert rows == [
            {'attempt_id': 'original', 'request': {'frozen': True}, 'submission_confirmed': True,
             'plan_confirmed': True, 'admission_started': None},
            {'attempt_id': 'unconfirmed', 'request': {'frozen': True}, 'submission_confirmed': False,
             'plan_confirmed': None, 'admission_started': None},
        ]
        applied = await (await conn.execute("SELECT name FROM runtime.schema_migrations")).fetchall()
        assert {row['name'] for row in applied} == {item.name for item in migrations}
