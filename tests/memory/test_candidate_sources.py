"""Complete candidate authority rules, independent of ranking and provider calls."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tianshu.memory.candidate_sources import check_rows_on, check_ref_groups_on
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef


class Connection:
    def __init__(self):
        self.rollbacks = 0

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        except BaseException:
            self.rollbacks += 1
            raise


def proof(*supports, derivations=(), explicit=(), relations=None):
    return {"_supports": wire(supports), "_derivations": wire(derivations),
            "_explicit": wire(explicit), "_relation_proofs": relations}


class Sources:
    def __init__(self, denied=()):
        self.denied, self.calls = set(denied), []

    async def check_many_on(self, conn, context, groups):
        self.calls.append(groups)
        assert len(groups) <= 1024
        assert len({(ref.source_id, ref.revision) for group in groups for ref in group}) <= 2048
        return tuple(all(ref.source_id not in self.denied for ref in group) for group in groups)


@pytest.mark.asyncio
async def test_alternative_supports_never_discard_required_or_relation_sources():
    allowed, denied = SourceRef("allowed", 1), SourceRef("denied", 1)
    sources = Sources(("denied",))
    good_link = {"supports": wire(((denied,), (allowed,))), "derivations": []}
    bad_link = {"supports": wire(((allowed,),)), "derivations": wire((denied,))}
    rows = (
        proof((denied,), (allowed,)),
        proof((allowed, denied)),
        proof((denied,), (allowed,), derivations=(denied,)),
        proof((allowed,), explicit=(denied,)),
        proof((allowed,), relations=[bad_link]),
        proof((allowed,), relations=[bad_link, good_link]),
        proof((allowed,), relations=[]),
    )
    assert await check_rows_on(sources, Connection(), SimpleNamespace(), rows) == (
        True, False, False, False, False, True, False)


@pytest.mark.asyncio
async def test_large_and_groups_and_many_or_alternatives_use_legal_source_batches():
    refs = tuple(SourceRef("root_" + str(index), 1) for index in range(2100))
    sources = Sources((refs[-1].source_id,))
    rows = (proof(refs), proof(*((ref,) for ref in refs)))
    assert await check_rows_on(sources, Connection(), SimpleNamespace(), rows) == (False, True)
    assert len(sources.calls) > 1
    assert any(ref == refs[-1] for groups in sources.calls for group in groups for ref in group)


@pytest.mark.asyncio
async def test_dependency_limit_splits_complete_proofs_and_marks_only_unprovable_roots_unknown():
    class SmallClosure(Sources):
        async def check_many_on(self, conn, context, groups):
            roots = {ref.source_id for group in groups for ref in group}
            if "too_deep" in roots or len(roots) > 2:
                raise AppError("source_dependency_budget_exceeded", status=503)
            return await super().check_many_on(conn, context, groups)

    refs = tuple(SourceRef("root_" + str(index), 1) for index in range(4))
    deep = SourceRef("too_deep", 1)
    conn, sources = Connection(), SmallClosure()
    rows = (proof(refs), proof((deep,)), proof((deep,), (refs[0],)))
    assert await check_rows_on(sources, conn, SimpleNamespace(), rows) == (True, None, True)
    assert conn.rollbacks > 0


@pytest.mark.asyncio
async def test_source_ranges_and_context_errors_are_not_cached_or_downgraded():
    class ExactSource(Sources):
        async def check_many_on(self, conn, context, groups):
            if context.access_epoch != 1:
                raise AppError("access_epoch_changed", status=403)
            return tuple(all(ref.locator.get("end", 0) <= 4 for ref in group) for group in groups)

    sources = ExactSource()
    rows = (proof((SourceRef("same", 1, {"start": 0, "end": 4}),)),
            proof((SourceRef("same", 1, {"start": 0, "end": 8}),)))
    assert await check_rows_on(sources, Connection(), SimpleNamespace(access_epoch=1), rows) == (True, False)
    with pytest.raises(AppError) as raised:
        await check_rows_on(sources, Connection(), SimpleNamespace(access_epoch=2), rows)
    assert raised.value.code == "access_epoch_changed"


@pytest.mark.asyncio
async def test_external_cancellation_propagates_out_of_source_batch_savepoint():
    entered = asyncio.Event()
    class WaitingSources:
        async def check_many_on(self, conn, context, groups):
            entered.set()
            await asyncio.Event().wait()

    conn = Connection()
    task = asyncio.create_task(check_rows_on(WaitingSources(), conn, SimpleNamespace(),
        (proof((SourceRef("root", 1),)),)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert conn.rollbacks == 1


async def test_complete_reference_groups_preserve_unknown_denial_and_locator_ranges():
    class Authority(Sources):
        async def check_many_on(self,conn,context,groups):
            if any(ref.source_id=="unknown" for group in groups for ref in group):
                raise AppError("source_dependency_budget_exceeded",status=503)
            return tuple(all(ref.locator.get("end",0)<=4 for ref in group) for group in groups)
    small=SourceRef("same",1,{"end":4})
    wide=SourceRef("same",1,{"end":8})
    unknown=SourceRef("unknown",1)
    assert await check_ref_groups_on(Authority(),Connection(),SimpleNamespace(),
        ((small,),(small,),(wide,),(unknown,),(small,unknown))) == (True,True,False,None,None)
