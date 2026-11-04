from unittest.mock import AsyncMock

from tianshu.operations.incidents import IncidentStore, default_settings
from tianshu.runtime.commands import CommandStore
from tianshu.types import OriginKind, TrustedContext


async def test_diagnostic_context_comes_from_saved_author_and_is_reauthorized(operations_db):
    author = TrustedContext("author", None, None, None, (), (), 1, OriginKind.HUMAN,
                            frozenset({"config.publish"}))
    class Identity:
        check_context = AsyncMock()
        refresh_scheduled_context = AsyncMock(return_value=author)
    identity = Identity()
    store = IncidentStore(operations_db, CommandStore(operations_db), identity)
    result = await store.save_settings(author, {"client_request_id": "settings-1", "expected_revision": 0,
                                                "value": default_settings()})
    assert "author_context" not in result.data
    refreshed = await store.diagnostic_context()
    assert refreshed.principal_id == "author" and refreshed.origin_kind == OriginKind.SYSTEM
    identity.refresh_scheduled_context.assert_awaited_once()
