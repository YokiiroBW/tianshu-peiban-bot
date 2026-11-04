"""Queued old transport events do not refill a new installation's history."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tianshu.channels.ingress import predates_generation


def test_transport_cutoff_is_the_outer_event_time_not_quoted_history():
    cutoff = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    old = SimpleNamespace(occurred_at=cutoff - timedelta(seconds=1))
    current_with_old_quote = SimpleNamespace(occurred_at=cutoff + timedelta(seconds=1),
        quoted_at=cutoff - timedelta(days=3))
    assert predates_generation(old, cutoff)
    assert not predates_generation(current_with_old_quote, cutoff)
    assert not predates_generation(None, cutoff)
