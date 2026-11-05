"""Affect writes reuse the Growth transaction and retain source authorization."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.companion.service import CompanionService
from tianshu.companion.types import AffectUpdate
from tianshu.runtime.errors import AppError
from tianshu.types import OriginKind, SourceRef, TrustedContext


def context_and_update():
    context = TrustedContext('principal', 'actor', 'person', 'chat', ('scope',), ('scope',), 1,
                             OriginKind.HUMAN, permissions=frozenset({'companion.manage'}))
    update = AffectUpdate('event', 1, '感受', 0.2, 0.2, 0.1, 0.1, 0.8, 300,
                          datetime.now(UTC), (SourceRef('original', 1),))
    return context, update


@pytest.mark.asyncio
async def test_existing_affect_transaction_reuses_connection_for_authorization():
    context, update = context_and_update()
    identity = SimpleNamespace(check=AsyncMock(side_effect=AssertionError('nested identity transaction')))
    sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=True))
    service = CompanionService(None, None, identity, None, actor_registry=None, actor_changed=None,
                               sources=sources, actor_activated=None)
    changed = SimpleNamespace(fetchone=AsyncMock(return_value={'event_revision': 1}))
    previous = SimpleNamespace(fetchone=AsyncMock(return_value=None))
    connection = SimpleNamespace(execute=AsyncMock(side_effect=[changed, previous, None]))

    result = await service.apply_affect(context, update, connection=connection)

    assert result == {'applied': True, 'event_id': 'event'}
    identity.check.assert_not_awaited()
    sources.validate_persistence.assert_awaited_once_with(connection, context, update.source_refs)


@pytest.mark.asyncio
async def test_existing_affect_transaction_still_rejects_revoked_source():
    context, update = context_and_update()
    identity = SimpleNamespace(check=AsyncMock(side_effect=AssertionError('nested identity transaction')))
    sources = SimpleNamespace(validate_persistence=AsyncMock(return_value=False))
    service = CompanionService(None, None, identity, None, actor_registry=None, actor_changed=None,
                               sources=sources, actor_activated=None)
    connection = SimpleNamespace(execute=AsyncMock())

    with pytest.raises(AppError) as failure:
        await service.apply_affect(context, update, connection=connection)

    assert failure.value.code == 'source_unavailable'
    sources.validate_persistence.assert_awaited_once_with(connection, context, update.source_refs)
    identity.check.assert_not_awaited()
    connection.execute.assert_not_awaited()
