from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.channels.observability import (backfill_ledger, backfill_media, media_event,
    record_ledger_on, record_status, recovery_event, withdraw_media_on)
from tianshu.operations.incidents import IncidentStore
from tianshu.operations.statistics import StatisticsProjection, _producer_scopes


async def test_channel_ledgers_reconcile_gaps_and_state_regressions(operations_db):
    projection = StatisticsProjection(operations_db)
    service = SimpleNamespace(statistics=projection, runtime=SimpleNamespace(
        database=operations_db, jobs=SimpleNamespace(enqueue=AsyncMock())))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with operations_db.transaction() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS channels")
        await conn.execute("CREATE TABLE channels.events(id text PRIMARY KEY,state text NOT NULL,created_at timestamptz NOT NULL)")
        await conn.execute("CREATE TABLE channels.calls(id text PRIMARY KEY,state text NOT NULL,created_at timestamptz NOT NULL)")
        for table, identifier, state in (("events", "b", "accepted"), ("calls", "outbound-1", "prepared")):
            row = await (await conn.execute("INSERT INTO channels." + table +
                " VALUES(%s,%s,%s) RETURNING id,state,created_at", (identifier, state, now))).fetchone()
            await record_ledger_on(service, conn, scope="ingress" if table == "events" else "outbound", row=row)
        for state in ("sending", "prepared", "sending", "unknown"):
            row = await (await conn.execute("UPDATE channels.calls SET state=%s WHERE id='outbound-1' "
                                            "RETURNING id,state,created_at", (state,))).fetchone()
            await record_ledger_on(service, conn, scope="outbound", row=row)

    assert (await backfill_ledger(service, scope="outbound"))["complete"]
    # A late committed or previously missed source before the cursor must
    # prevent a false completion and force a new sweep from the beginning.
    async with operations_db.transaction() as conn:
        await conn.execute("INSERT INTO channels.events VALUES('a','consumed',%s)", (now,))
    result = await backfill_ledger(service, scope="ingress", after_id="b")
    assert result == {"next_cursor": "", "complete": False}
    assert (await backfill_ledger(service, scope="ingress"))["complete"]
    original_record = projection.record_safely_on
    projection.record_safely_on = AsyncMock(return_value=False)
    async with operations_db.transaction() as conn:
        row = await (await conn.execute("UPDATE channels.events SET state='consumed' WHERE id='b' "
                                        "RETURNING id,state,created_at")).fetchone()
        await record_ledger_on(service, conn, scope="ingress", row=row)
        marker = await (await conn.execute("SELECT complete FROM operations.statistic_producers "
                                           "WHERE producer='channels.ingress'")).fetchone()
        assert marker["complete"] is False
    projection.record_safely_on = original_record
    assert (await backfill_ledger(service, scope="ingress"))["complete"]

    result = await projection.query({"start": (now - timedelta(minutes=1)).isoformat(),
                                     "end": (now + timedelta(minutes=1)).isoformat()})
    channels = result["outcomes"]["channel_transport"]
    assert channels["metrics"]["channel_ingress_accepted"]["total"] == 2
    assert channels["metrics"]["channel_ingress_processed"]["total"] == 2
    assert channels["metrics"]["channel_calls_unknown"]["total"] == 1
    assert channels["metrics"]["channel_calls_inflight"]["total"] == 0
    assert channels["delivery"] == "not_proven_by_call_confirmation"
    assert "messages" not in result["outcomes"]["business"]["families"]
    assert result["coverage"]["producer_scopes"]["channels.ingress"]["complete"]
    assert not result["coverage"]["producer_scopes"]["channels.media"]["complete"]


def test_live_producer_marker_does_not_claim_old_backfill_is_complete():
    row = {"complete": False, "detail": {"reason": "live_producer"}, "watermark": "last", "last_seen": None}
    scope = _producer_scopes({"life.planning": row})["life.planning"]
    assert scope["mode"] == "live"
    assert scope["backfill"]["state"] == "scanned_unverified"
    assert scope["complete"] is False


async def test_business_attempts_recovery_and_billing_are_independent(operations_db):
    projection = StatisticsProjection(operations_db)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with operations_db.transaction() as conn:
        for event in (
            {"family": "models", "event_id": "original", "revision": 1, "occurred_at": now,
             "module": "models", "dimensions": {"state": "unknown"},
             "metrics": {"physical_attempts": 1, "input_tokens": None, "actual_cost": None}},
            {"family": "recovery", "event_id": "recovery", "revision": 1, "occurred_at": now,
             "module": "memory", "dimensions": {"state": "verified"},
             "metrics": {"recovery_eligible": 1, "recovery_evaluated": 1, "recovery_verified": 1}},
        ):
            await projection.record_on(conn, event)
    result = await projection.query({"start": (now - timedelta(minutes=1)).isoformat(),
                                     "end": (now + timedelta(minutes=1)).isoformat()})
    outcomes = result["outcomes"]
    assert outcomes["original_model_attempts"]["states"] == {"unknown": 1}
    assert outcomes["original_model_attempts"]["complete"] is False
    assert outcomes["recovery"]["metrics"]["recovery_verified"]["total"] == 1
    assert outcomes["billing"]["actual_cost"]["knowledge"] == "unknown"
    assert outcomes["business"]["complete"] is False


def test_media_retry_replaces_previous_failure_and_keeps_evidence_separate():
    facts = {"identifier": "media-1", "actor_id": "actor-1", "occurred_at": datetime.now(UTC)}
    failed = media_event(**facts, attempt=1, state="waiting_retry")
    running = media_event(**facts, attempt=2, state="fetching")
    complete = media_event(**facts, attempt=2, state="complete")
    assert failed["revision"] < running["revision"] < complete["revision"]
    assert failed["metrics"]["media_failed"] == 1
    assert running["metrics"]["media_failed"] == 0
    assert recovery_event(**facts, attempt=2, state="fetching")["metrics"]["recovery_evaluated"] == 0
    assert recovery_event(**facts, attempt=2, state="complete")["metrics"]["recovery_verified"] == 1
    withdrawn = media_event(**facts, attempt=2, state="withdrawn")
    assert withdrawn["revision"] > complete["revision"]
    assert withdrawn["actor_id"] is None and withdrawn["metrics"]["media_completed"] is None
    withdrawn_recovery = recovery_event(**facts, attempt=2, state="withdrawn")
    assert withdrawn_recovery["actor_id"] is None and withdrawn_recovery["metrics"]["recovery_verified"] is None


async def test_withdrawn_media_is_not_counted_as_known_success(operations_db):
    projection = StatisticsProjection(operations_db)
    service = SimpleNamespace(statistics=projection, runtime=SimpleNamespace(
        database=operations_db, jobs=SimpleNamespace(enqueue=AsyncMock())))
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with operations_db.transaction() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS channels")
        await conn.execute("CREATE TABLE channels.events(id text PRIMARY KEY,state text NOT NULL,actor_id text)")
        await conn.execute("CREATE TABLE channels.media_supplements(id text PRIMARY KEY,event_key text NOT NULL,"
                           "attempts integer NOT NULL,state text NOT NULL,observed_at timestamptz NOT NULL,"
                           "attempt_started_at timestamptz)")
        await conn.execute("INSERT INTO channels.events VALUES('event-1','consumed','actor-1')")
        await conn.execute("INSERT INTO channels.media_supplements VALUES('media-1','event-1',1,'complete',%s,%s)",
                           (now, now))
        await record_status(service, conn, identifier="media-1", actor_id="actor-1", attempt=1,
                            state="complete", occurred_at=now)
        await conn.execute("UPDATE channels.events SET state='withdrawn' WHERE id='event-1'")
        await withdraw_media_on(service, conn, ["event-1"])
    assert (await backfill_media(service))["complete"]
    result = await projection.query({"start": (now - timedelta(minutes=1)).isoformat(),
                                     "end": (now + timedelta(minutes=1)).isoformat()})
    assert result["breakdowns"]["family"]["media"]["media_completed"] == {
        "total": None, "samples": 0, "unknown": 1}
    assert "actor-1" not in result["breakdowns"].get("actor_id", {})


async def test_database_incidents_do_not_merge_distinct_pools_or_roles(operations_db):
    store = IncidentStore(operations_db, None, None)
    base = {"dependency": "postgresql", "error_family": "PoolTimeout", "version": "unknown",
            "failure_phase": "connection_acquire", "severity": "error"}
    one = await store.ingest({**base, "event_id": "one", "role": "interactive", "pool_name": "business"})
    two = await store.ingest({**base, "event_id": "two", "role": "interactive", "pool_name": "control"})
    three = await store.ingest({**base, "event_id": "three", "role": "background", "pool_name": "business"})
    assert len({one["incident_id"], two["incident_id"], three["incident_id"]}) == 3
    assert one["summary"]["pool_name"] == "business"
