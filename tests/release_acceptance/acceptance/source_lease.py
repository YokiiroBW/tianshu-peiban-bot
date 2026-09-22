"""A completed delivery after expiry is evidence; a later inspection clock is not."""

from datetime import datetime, timezone
import re
import uuid

from .transport import check

TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
CORRELATION = re.compile(r"[0-9a-f]{32}")


def instant(value):
    check(
        isinstance(value, str) and TIMESTAMP.fullmatch(value),
        "delivery_timestamp_invalid",
    )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (ValueError, OverflowError):
        check(False, "delivery_timestamp_invalid")


def completed_after_expiry(events, initial_expires_at, accepted_correlations):
    """Require one unique successful event per accepted request, and actual post-expiry work.

    Callers supply correlations captured when the public input was accepted. No current
    clock is consulted. This validates event facts, not the authenticity of their producer.
    """
    expiry = instant(initial_expires_at)
    expected = list(accepted_correlations)
    check(bool(events) and bool(expected), "delivery_evidence_missing")
    check(
        all(isinstance(c, str) and CORRELATION.fullmatch(c) for c in expected),
        "delivery_correlation_invalid",
    )
    check(len(set(expected)) == len(expected), "accepted_correlation_duplicate")
    seen, ids, timed = set(), set(), []
    for event in events:
        check(
            isinstance(event, dict)
            and event.get("service") == "companion"
            and event.get("event") == "turn.delivery.finished"
            and event.get("outcome") == "succeeded",
            "successful_delivery_required",
        )
        correlation = event.get("correlation_id")
        check(
            isinstance(correlation, str) and correlation in expected,
            "delivery_correlation_unmatched",
        )
        check(correlation not in seen, "delivery_correlation_duplicate")
        seen.add(correlation)
        identifier = event.get("event_id")
        try:
            valid_id = (
                isinstance(identifier, str) and str(uuid.UUID(identifier)) == identifier
            )
        except ValueError:
            valid_id = False
        check(
            valid_id and identifier not in ids, "delivery_identity_invalid_or_duplicate"
        )
        ids.add(identifier)
        timed.append((instant(event.get("timestamp")), event["timestamp"]))
    check(seen == set(expected), "delivery_correlation_missing")
    timed.sort()
    crossed = [item for item in timed if item[0] > expiry]
    check(bool(crossed), "initial_source_lease_not_crossed_by_delivery")
    return {
        "initial_expires_at": initial_expires_at,
        "completed_delivery_after_initial_expiry": True,
        "successful_delivery_count": len(timed),
        "deliveries_after_initial_expiry": len(crossed),
        "first_delivery_at": timed[0][1],
        "last_delivery_at": timed[-1][1],
        "first_delivery_after_initial_expiry": crossed[0][1],
        "last_delivery_after_initial_expiry": crossed[-1][1],
        "time_basis": "successful_matched_product_delivery_event_timestamp",
    }
