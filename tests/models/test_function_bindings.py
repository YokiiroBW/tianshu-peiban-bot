"""A function chooses one model across scheduling classes, with actor overrides."""

from importlib.resources import files
from types import SimpleNamespace

import pytest

from tianshu.models.catalog import ModelCatalog
from tianshu.models.management import _apply_configuration
from tianshu.models.configuration_delete import delete_configuration
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.config import Settings, Secrets
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import foundation_migrations, execution_migrations, migrate
from tianshu.runtime.jobs import JobQueue
from tianshu.types import OriginKind, ServiceClass, TrustedContext


async def seed(connection):
    await connection.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) VALUES('q',2,0,2)")
    await connection.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) VALUES('p','P','https://fixture.invalid','KEY','openai.chat')")
    await connection.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group) VALUES('oc','p','oc','q'),('gemini','p','gemini','q')")


async def test_migration_archives_choices_and_keeps_author_selection_for_all_priorities(empty_database):
    jobs = JobQueue(empty_database.config.dsn, database=empty_database)
    before_migrations = tuple(m for m in foundation_migrations() + execution_migrations(jobs)
                              if m.name != 'models_function_bindings')
    await migrate(empty_database, before_migrations)
    async with empty_database.transaction() as conn:
        await seed(conn)
        await conn.execute("INSERT INTO models.bindings(function,service_class,actor_id,route_id,revision) VALUES"
                           "('memory.extract','interactive','','oc',2),('memory.extract','elastic','','gemini',4),"
                           "('memory.extract','due','','gemini',1),('memory.extract','elastic','role','gemini',3),"
                           "('life.plan','elastic','','gemini',1)")
        original = await (await conn.execute('SELECT * FROM models.bindings ORDER BY function,actor_id,service_class')).fetchall()
        await conn.execute(files('tianshu.models').joinpath('function_bindings.sql').read_text())
        archived = await (await conn.execute('SELECT function,service_class,actor_id,route_id,revision,fallback_routes FROM models.binding_class_history ORDER BY function,actor_id,service_class')).fetchall()
        assert archived == original
        assert await (await conn.execute('SELECT 1 FROM models.bindings WHERE revision<=0')).fetchone() is None
        catalog = ModelCatalog(empty_database)
        for priority in ServiceClass:
            for actor, expected in ((None, 'oc'), ('role', 'gemini')):
                request = SimpleNamespace(function='memory.extract', service_class=priority,
                                          context=SimpleNamespace(actor_id=actor))
                assert (await catalog.bound_binding(conn, request))['route_id'] == expected
        rows = await (await conn.execute("SELECT route_id,revision FROM models.bindings WHERE function='memory.extract' AND actor_id='' ")).fetchall()
        assert len(rows) == 3 and all(row == {'route_id': 'oc', 'revision': 5} for row in rows)


async def test_one_save_updates_every_legacy_alias_and_rejects_stale_cas(database, tmp_path):
    services = SimpleNamespace(database=database, commands=CommandStore(database),
                               settings=Settings({}, Secrets({}), tmp_path))
    async with database.transaction() as conn:
        await seed(conn)
        body = dict(function='reflect', route_id='oc', fallback_routes=['gemini'],
                    client_request_id='first', service_class='elastic')
        first = await _apply_configuration(conn, 'bindings', body, 0, services)
        assert first.revision == 1
        rows = await (await conn.execute("SELECT * FROM models.bindings WHERE function='reflect'")).fetchall()
        assert len(rows) == 3 and all(r['route_id'] == 'oc' and r['revision'] == 1 for r in rows)
        second = await _apply_configuration(conn, 'bindings', {**body, 'route_id': 'gemini', 'fallback_routes': [], 'client_request_id': 'second'}, 1, services)
        assert second.revision == 2
        with pytest.raises(AppError, match='revision_conflict'):
            await _apply_configuration(conn, 'bindings', body, 1, services)
        for priority in ServiceClass:
            request = SimpleNamespace(function='reflect', service_class=priority,
                                      context=SimpleNamespace(actor_id='role'))
            assert (await ModelCatalog(database).bound_binding(conn, request))['route_id'] == 'gemini'
    context = TrustedContext('author', None, None, None, (), (), 1, OriginKind.HUMAN,
                             frozenset({'models.manage'}))
    await delete_configuration(services, context, 'bindings', dict(
        function='reflect', expected_revision=2, client_request_id='inherit', service_class='due'))
    async with database.transaction() as conn:
        assert await (await conn.execute("SELECT 1 FROM models.bindings WHERE function='reflect'")).fetchone() is None
