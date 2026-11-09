"""A complete locked graph is not a new graph at the authorization boundary."""
import pytest

from test_rbj_graph_proofs import core,ordinary_users,_seed
from tianshu.runtime.errors import AppError
from tianshu.sources.rbj_budget import SourceBudget


async def dense(core,context):
    refs=await _seed(core,context,'locked-dense-',284)
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
            "support_revision,support_locator,support_key) SELECT 'locked-dense-'||p,1,'0',"
            "'locked-dense-'||c,1,'{}'::jsonb,p||'-'||c FROM generate_series(1,283) p "
            "CROSS JOIN generate_series(0,282) c WHERE c<p ORDER BY p,c LIMIT 22019")
    return refs


async def test_dense_persistence_reuses_locked_graph_but_charges_proof_execution(core,ordinary_users):
    alice,_=ordinary_users
    refs=await dense(core,alice)
    async with core.database.transaction() as conn:
        budget=SourceBudget()
        await core.identity.lock_context_on(conn,alice)
        await core.sources._lock_selected(conn,refs,budget=budget)
        before=budget.remaining.copy()
        assert before['edges']==65536-2*22019
        assert (await core.sources._check_with_budget(conn,alice,(refs,),budget))==(True,)
        assert budget.remaining['edges']==before['edges'] and budget.remaining['nodes']==before['nodes']
        assert budget.remaining['work']<before['work'] and budget.remaining['inputs']<before['inputs']
    async with core.database.transaction() as conn:
        assert await core.sources.validate_persistence(conn,alice,refs)


@pytest.mark.parametrize('change',['viewer','withdrawal','fragment'])
async def test_locked_graph_never_caches_permission_or_original_content_validity(core,ordinary_users,change):
    alice,bob=ordinary_users
    refs=await dense(core,alice)
    async with core.database.transaction() as conn:
        budget=SourceBudget()
        await core.identity.lock_context_on(conn,alice)
        await core.sources._lock_selected(conn,refs,budget=budget)
        viewer=alice
        if change=='viewer':viewer=bob
        elif change=='withdrawal':
            await conn.execute("UPDATE sources.originals SET state='retracted' WHERE source_id=%s",(refs[0].source_id,))
        else:
            await conn.execute("INSERT INTO sources.forgotten_fragments(source_id,revision,start_position,end_position) "
                "VALUES(%s,1,0,1)",(refs[0].source_id,))
        assert (await core.sources._check_with_budget(conn,viewer,(refs,),budget))==(False,)


async def test_locked_graph_does_not_cover_new_refs_or_reset_execution_work(core,ordinary_users):
    alice,_=ordinary_users
    refs=await dense(core,alice)
    outside=await _seed(core,alice,'outside-locked-',1)
    async with core.database.transaction() as conn:
        budget=SourceBudget()
        await core.identity.lock_context_on(conn,alice)
        await core.sources._lock_selected(conn,refs,budget=budget)
        budget.charge(nodes=budget.remaining['nodes'],edges=budget.remaining['edges'])
        assert (await core.sources._check_with_budget(conn,alice,(refs,),budget))==(True,)
        with pytest.raises(AppError,match='source_dependency_budget_exceeded'):
            await core.sources._check_with_budget(conn,alice,(outside,),budget)
        budget.charge(work=budget.remaining['work'])
        with pytest.raises(AppError,match='source_dependency_budget_exceeded'):
            await core.sources._check_with_budget(conn,alice,(refs,),budget)


async def test_locked_graph_cannot_survive_its_transaction(core,ordinary_users):
    alice,_=ordinary_users
    refs=await _seed(core,alice,'transaction-bound-',2)
    budget=SourceBudget()
    async with core.database.pool.connection() as conn:
        async with conn.transaction():
            await core.identity.lock_context_on(conn,alice)
            await core.sources._lock_selected(conn,refs,budget=budget)
        async with conn.transaction():
            with pytest.raises(AppError,match='source_proof_incomplete'):
                await core.sources._check_with_budget(conn,alice,(refs,),budget)


async def test_in_transaction_new_dependency_requires_new_ordered_lock_set(core, ordinary_users):
    from tianshu.runtime.recovery_policy import RecoveryWait

    alice, _ = ordinary_users
    refs = await _seed(core, alice, "locked-extension-", 2)
    async with core.database.transaction() as conn:
        budget = SourceBudget()
        await core.identity.lock_context_on(conn, alice)
        await core.sources._lock_selected(conn, refs[:1], budget=budget)
        await conn.execute("INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
            "support_revision,support_locator,support_key) VALUES(%s,1,'0',%s,1,'{}'::jsonb,'new')",
            (refs[0].source_id, refs[1].source_id))
        with pytest.raises(RecoveryWait) as error:
            await core.sources._check_with_budget(conn, alice, (refs[:1],), budget)
        assert error.value.reason == "source_lock_set_changed"
