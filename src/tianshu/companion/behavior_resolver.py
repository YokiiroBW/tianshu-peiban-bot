"""Read-only behavior eligibility for Broker and other lightweight roles."""

from types import SimpleNamespace

from .habits import HabitLedger


class BehaviorResolver:
    def __init__(self, database, identity, sources):
        # This reuses the exact owner query and eligibility rules.  It creates
        # no Growth worker, Life service, model client, or second ledger.
        self._ledger = HabitLedger(SimpleNamespace(database=database, identity=identity),
                                   source_reader=sources.check_many)

    async def resolve_behaviors(self, context, refs, *, purpose):
        return await self._ledger.resolve(context, refs, purpose=purpose)
