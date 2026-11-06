"""Use the same Action-head fence for Life's direct and consumer result paths."""
from tianshu.capabilities.actions import result_sources
from tianshu.conversation.codec import receipt, source_refs
from tianshu.sources.dependencies import unique_refs
from .rbi_activity_reconciliation import generation_on


class DirectResultFence:
    def __init__(self, life, actions, context, snapshot, data_generation=None):
        self.life, self.actions, self.context = life, actions, context
        self.snapshot, self.requested_generation = snapshot, data_generation
        self.generation = None
        self.result = receipt(snapshot["receipt"])
        self.source_refs = unique_refs(source_refs(snapshot["action"].get("source_refs", ())),
            result_sources(self.result))

    def include_source_refs(self, refs):
        self.source_refs = unique_refs(self.source_refs,refs)

    async def begin(self, connection):
        self.generation = await generation_on(self.life, connection, self.requested_generation)

    async def fence(self, connection):
        await self.actions.assert_result_head_on(connection, self.context,
            action_id=self.result.action_id, expected_receipt=self.result,
            result_revision=self.snapshot["result_revision"], data_generation=self.generation,
            source_refs=self.source_refs)

    async def __call__(self, _connection, _disposition="done", *, reason=None, proof=None):
        # Direct observation has no Conversation claim to complete.
        return None
