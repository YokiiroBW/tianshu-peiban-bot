"""One retention policy for native observers; delivery buffers never receive a TTL."""

from copy import deepcopy
from pathlib import PurePosixPath


def days(value=30):
    if type(value) is not int or value < 1:
        raise ValueError("retention_days_must_be_positive_integer")
    return value


def max_events(value=10000):
    """Validate only for the retired, offline integrity-ledger tooling."""
    if type(value) is not int or value < 1:
        raise ValueError("log_max_events_must_be_positive_integer")
    return value


def apply_retention(guard, loki, retention_days=30):
    """Project the policy onto captured native configs without rebuilding rules.

    Keep identities, paths, TLS, buffering and every unrelated native setting.
    Loki timestamps are collection timestamps; original event timestamps remain
    in the lossless JSON body. Its compactor performs asynchronous expiration.
    """
    value = days(retention_days)
    guard, loki = deepcopy(guard), deepcopy(loki)
    guard["retention_days"] = value
    old_ledger = guard.pop("ledger", None)
    if old_ledger and "guard" not in guard.get("storage_roots", {}):
        guard.setdefault("storage_roots", {})["guard"] = str(PurePosixPath(old_ledger).parent)
    guard.pop("log_max_events", None)
    guard.pop("ledger_max_events", None)
    limits = loki.setdefault("limits_config", {})
    limits["retention_period"] = f"{24 * value}h"
    limits["max_query_length"] = f"{24 * (value + 1)}h"
    # Do not shorten the existing 30-day retry window when choosing a shorter
    # retention. A pending Vector buffer is never a time-based deletion target.
    limits["reject_old_samples_max_age"] = f"{max(720, 24 * value)}h"
    limits["deletion_mode"] = "filter-and-delete"
    return guard, loki
