"""Bounded acquisition of the original missing part, without a second input event."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.capabilities.contracts import digest
from tianshu.capabilities.serialization import receipt_from
from tianshu.conversation.codec import context_from_record, document, input_parts, source_refs
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import Action, OriginKind, ServiceClass

from .contracts import ChannelError
from .ingress import event_withdrawn

MAX_ATTEMPTS = 3  # Includes the first acquisition performed during ingress.
RETRY_DELAYS = (5, 30, 120)
MAPPING_ERRORS = {"provider_media_mapping_required"}
PERMANENT_ERRORS = {"provider_media_path", "provider_media_origin_not_allowed", "channel_media_budget",
                    "telegram_file_path", "source_media_mismatch"}


def failure_category(code):
    from tianshu.runtime.recovery_policy import FailureCategory, classify_failure
    if code == "supplement_worker_interrupted":
        return FailureCategory.INFRASTRUCTURE
    return classify_failure(code, external_outcome_unknown=False)


def failure_state(code, attempt):
    from tianshu.runtime.recovery_policy import FailureCategory
    if attempt >= MAX_ATTEMPTS - 1:
        return "unavailable"
    if code in MAPPING_ERRORS:
        return "waiting_config"
    if code in PERMANENT_ERRORS:
        return "unavailable"
    category = failure_category(code)
    return "waiting_retry" if category in {FailureCategory.TRANSPORT, FailureCategory.INFRASTRUCTURE} else "unavailable"


def job(identifier, attempt, when, actor_id=None, generation=None):
    return JobSpec("channels.media_supplement", f"media-supplement:{identifier}:{attempt}",
                   {"supplement_id": identifier}, ServiceClass.INTERACTIVE, when, actor_id,
                   execution_lane=ExecutionLane.CONVERSATION, data_generation=generation)


async def activate_supplements(service, event_key, context, input_id, original_refs, *, deployment_generation=None):
    async with service.runtime.database.transaction() as conn:
        if deployment_generation is not None:
            await service.runtime.jobs.assert_generation_on(conn, deployment_generation)
        if await event_withdrawn(conn, event_key):
            return
        await conn.execute("UPDATE channels.events SET actor_id=%s WHERE id=%s AND state<>'withdrawn'",
                           (context.actor_id, event_key))
        rows = await (await conn.execute(
            "SELECT * FROM channels.media_supplements WHERE event_key=%s AND state='waiting_ingest' FOR UPDATE",
            (event_key,))).fetchall()
        for row in rows:
            if row["deployment_generation"] is not None:
                await service.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
            index = row["part_index"]
            if index >= len(original_refs):
                raise ChannelError("supplement_original_missing", 409)
            ref = original_refs[index]
            if not await service.sources.validate_persistence(conn, context, (ref,)):
                raise ChannelError("source_revoked", 410)
            await service.repository.bind_event_sources(conn, event_key, (ref,))
            code = row["error_code"]
            state = failure_state(code, 0)
            due = datetime.now(UTC) + timedelta(seconds=RETRY_DELAYS[0]) if state == "waiting_retry" else None
            lineage_id = lineage_version = None
            if service.recovery_policy is not None:
                event = await (await conn.execute("SELECT connection_id,producer,event_id,revision FROM channels.events WHERE id=%s",
                                                  (event_key,))).fetchone()
                lineage_version = digest([event["producer"], event["event_id"], event["revision"], index, wire(ref)])
                owner = "channels.media:" + row["id"]
                lineage = await service.recovery_policy.register_on(conn, domain="channels.media",
                    subject_id=context.actor_id, owner=owner, material_id=row["id"],
                    material_digest=lineage_version, dependency="channel:"+event["connection_id"],
                    current_version=lineage_version, max_attempts=MAX_ATTEMPTS)
                lineage_id = lineage["lineage_id"]
                original_attempt = "media:" + row["id"] + ":initial"
                reservation = await service.recovery_policy.reserve_attempt_on(conn, lineage_id, owner=owner,
                    expected_version=lineage_version, attempt_id=original_attempt, request_digest=lineage_version,
                    route=event["connection_id"], original_state="none")
                await service.recovery_policy.fail_attempt_on(conn, original_attempt, code=code,
                    category=failure_category(code), retry_after=due,
                    probe_token=reservation.probe_token)
            await conn.execute(
                "UPDATE channels.media_supplements SET input_id=%s,context=%s,original_ref=%s,state=%s,next_due=%s,lineage_id=%s,lineage_version=%s,observed_at=now() WHERE id=%s",
                (input_id, document(context), document(ref), state, due, lineage_id, lineage_version, row["id"]))
            from .observability import record_status
            observed = await (await conn.execute("SELECT observed_at FROM channels.media_supplements WHERE id=%s", (row["id"],))).fetchone()
            await record_status(service, conn, identifier=row["id"], actor_id=context.actor_id,
                                attempt=0, state=state, occurred_at=observed["observed_at"])
            if due:
                await service.runtime.jobs.enqueue(conn, job(row["id"], 1, due, context.actor_id, row["deployment_generation"]))


async def retry(service, supplement_id):
    async with service.runtime.database.transaction() as conn:
        row = await (await conn.execute(
            "SELECT * FROM channels.media_supplements WHERE id=%s FOR UPDATE", (supplement_id,))).fetchone()
        if not row or row["state"] != "waiting_retry" or row["next_due"] > datetime.now(UTC):
            return
        if await event_withdrawn(conn, row["event_key"]):
            await conn.execute("UPDATE channels.media_supplements SET state='withdrawn',payload=NULL WHERE id=%s", (supplement_id,))
            return
        if row["deployment_generation"] is not None:
            await service.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
        attempt = row["attempts"] + 1
        active_attempt_id = probe_token = None
        if service.recovery_policy is not None and row["lineage_id"]:
            owner = "channels.media:" + supplement_id
            claims = await service.recovery_policy.claim_due_on(conn, owner=owner, limit=1, lease_seconds=300)
            if not claims:
                due = datetime.now(UTC) + timedelta(seconds=30)
                await conn.execute("UPDATE channels.media_supplements SET next_due=%s WHERE id=%s", (due, supplement_id))
                await service.runtime.jobs.enqueue(conn, JobSpec("channels.media_supplement",
                    f"media-supplement-claim:{supplement_id}:{uuid4()}", {"supplement_id": supplement_id},
                    ServiceClass.INTERACTIVE, due, execution_lane=ExecutionLane.CONVERSATION,
                    data_generation=row["deployment_generation"]))
                return
            event = await (await conn.execute("SELECT connection_id FROM channels.events WHERE id=%s", (row["event_key"],))).fetchone()
            active_attempt_id = f"media:{supplement_id}:retry:{attempt}"
            reservation = await service.recovery_policy.reserve_attempt_on(conn, row["lineage_id"], owner=owner,
                expected_version=row["lineage_version"], attempt_id=active_attempt_id,
                request_digest=row["lineage_version"], route=event["connection_id"], original_state="failed",
                claim_token=claims[0]["claim_token"])
            probe_token = reservation.probe_token
        lease = datetime.now(UTC) + timedelta(minutes=5)
        started = await (await conn.execute("UPDATE channels.media_supplements SET state='fetching',attempts=%s,lease_until=%s,active_attempt_id=%s,probe_token=%s,attempt_started_at=now() WHERE id=%s RETURNING attempt_started_at",
                           (attempt, lease, active_attempt_id, probe_token, supplement_id))).fetchone()
        row = {**row, "active_attempt_id": active_attempt_id, "probe_token": probe_token}
        from .observability import record_status
        await record_status(service, conn, identifier=supplement_id, actor_id=(row["context"] or {}).get("actor_id"),
                            attempt=attempt, state="fetching", occurred_at=started["attempt_started_at"])
        await service.runtime.jobs.enqueue(conn, JobSpec("channels.media_supplement_expire",
            f"media-supplement-expire:{supplement_id}:{attempt}", {"supplement_id": supplement_id, "attempt": attempt},
            ServiceClass.INTERACTIVE, lease, execution_lane=ExecutionLane.CONVERSATION,
            data_generation=row["deployment_generation"]))
    context = context_from_record(row["context"])
    original_ref = source_refs((row["original_ref"],))[0]
    try:
        await service.sources.check(context, (original_ref,))
        async with service.runtime.database.transaction(read_only=True) as conn:
            event = await (await conn.execute("SELECT * FROM channels.events WHERE id=%s", (row["event_key"],))).fetchone()
        if not event or event["state"] == "withdrawn":
            await withdrawn(service, supplement_id, attempt)
            return
        connection = await service.repository.connection(event["connection_id"], context)
        original = input_parts((service.repository.unseal(row["payload"], supplement_id + ":media"),))[0]

        async def call_api(name, params):
            if name == "__token__":
                return await service.repository.token(connection)
            key = digest([supplement_id, attempt, name, params])
            old = await service.actions.find("channels.read", key, context)
            if old:
                actual = receipt_from(old["receipt"])
            else:
                read_context = replace(context, origin_kind=OriginKind.OBSERVATION)
                action = Action(str(uuid5(NAMESPACE_URL, key)), key, "channels.read", context.actor_id,
                    read_context, {"connection_id": connection["id"], "api_name": name, "params": params},
                    OriginKind.OBSERVATION, (original_ref,))
                actual = await service.actions.invoke(action, background=False)
            if not actual or actual.execution != "succeeded":
                raise ChannelError(actual.error_code if actual else "channel_media_lookup_unavailable", 503)
            return actual.result["data"]

        part, supplement_ref = await service.media.receive(context, connection, original, call_api,
            event_id=event["event_id"], part_index=row["part_index"], revision=event["revision"],
            source_support=(original_ref,), supplement_revision=1, producer=event["producer"],
            deployment_generation=row["deployment_generation"])
        if supplement_ref is None or part.media is None:
            await failed(service, row, attempt, part.metadata.get("error_code", "channel_media_unavailable"))
            return
        async with service.runtime.database.transaction() as conn:
            if row["deployment_generation"] is not None:
                await service.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
            if await event_withdrawn(conn, row["event_key"]):
                await conn.execute("UPDATE channels.media_supplements SET state='withdrawn',payload=NULL WHERE id=%s", (supplement_id,))
                return
            if not await service.sources.validate_persistence(conn, context, (original_ref, supplement_ref)):
                raise ChannelError("source_revoked", 410)
            current = await (await conn.execute("SELECT state,attempts FROM channels.media_supplements WHERE id=%s FOR UPDATE",
                                                (supplement_id,))).fetchone()
            if not current or current["state"] != "fetching" or current["attempts"] != attempt:
                return
            await service.conversation.supplement_input(conn, context, input_id=row["input_id"],
                part_index=row["part_index"], original_ref=original_ref, supplement_ref=supplement_ref, media=part.media,
                deployment_generation=row["deployment_generation"])
            if service.recovery_policy is not None and row.get("active_attempt_id"):
                await service.recovery_policy.succeed_attempt_on(conn, row["active_attempt_id"], probe_token=row.get("probe_token"))
            await service.repository.bind_event_sources(conn, row["event_key"], (supplement_ref,))
            updated = await (await conn.execute("UPDATE channels.media_supplements SET state='complete',payload=NULL,lease_until=NULL,error_code=NULL,observed_at=now() WHERE id=%s RETURNING observed_at", (supplement_id,))).fetchone()
            from .observability import record_status
            await record_status(service, conn, identifier=supplement_id, actor_id=context.actor_id,
                                attempt=attempt, state="complete", occurred_at=updated["observed_at"])
    except Exception as error:
        code = getattr(error, "code", "channel_media_unavailable")
        if code in {"source_revoked", "source_unavailable", "source_suppressed", "source_support_unavailable"}:
            async with service.runtime.database.transaction(read_only=True) as conn:
                was_withdrawn = await event_withdrawn(conn, row["event_key"])
            if was_withdrawn:
                await withdrawn(service, supplement_id, attempt)
                return
        await failed(service, row, attempt, code)


async def withdrawn(service, identifier, attempt):
    async with service.runtime.database.transaction() as conn:
        row = await (await conn.execute("SELECT deployment_generation FROM channels.media_supplements "
                                       "WHERE id=%s AND state='fetching' AND attempts=%s", (identifier, attempt))).fetchone()
        if not row:
            return
        if row["deployment_generation"] is not None:
            await service.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
        await conn.execute("UPDATE channels.media_supplements SET state='withdrawn',payload=NULL,lease_until=NULL "
                           "WHERE id=%s AND state='fetching' AND attempts=%s", (identifier, attempt))


async def failed(service, row, attempt, code):
    state = failure_state(code, attempt)
    due = datetime.now(UTC) + timedelta(seconds=RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)-1)]) if state == "waiting_retry" else None
    async with service.runtime.database.transaction() as conn:
        if row["deployment_generation"] is not None:
            await service.runtime.jobs.assert_generation_on(conn, row["deployment_generation"])
        current = await (await conn.execute("SELECT state,attempts,active_attempt_id,probe_token FROM channels.media_supplements WHERE id=%s FOR UPDATE", (row["id"],))).fetchone()
        if not current or current["state"] != "fetching" or current["attempts"] != attempt:
            return
        if service.recovery_policy is not None and current.get("active_attempt_id"):
            await service.recovery_policy.fail_attempt_on(conn, current["active_attempt_id"], code=code,
                category=failure_category(code), retry_after=due,
                probe_token=current.get("probe_token"))
        updated = await (await conn.execute(
            "UPDATE channels.media_supplements SET state=%s,next_due=%s,lease_until=NULL,error_code=%s,active_attempt_id=NULL,probe_token=NULL,observed_at=now() "
            "WHERE id=%s AND state='fetching' AND attempts=%s RETURNING id,observed_at", (state, due, code, row["id"], attempt))).fetchone()
        if updated:
            from .observability import record_status
            await record_status(service, conn, identifier=row["id"], actor_id=(row["context"] or {}).get("actor_id"),
                                attempt=attempt, state=state, occurred_at=updated["observed_at"])
        if updated and due:
            await service.runtime.jobs.enqueue(conn, job(row["id"], attempt+1, due, (row["context"] or {}).get("actor_id"),
                                                         row["deployment_generation"]))


async def expire(service, supplement_id, attempt):
    async with service.runtime.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT * FROM channels.media_supplements WHERE id=%s AND state='fetching' "
            "AND attempts=%s AND lease_until<=now()", (supplement_id, attempt))).fetchone()
    if row:
        await failed(service, row, attempt, "supplement_worker_interrupted")


async def wake_for_configuration(service, connection, connection_id, revision):
    rows = await (await connection.execute(
        "UPDATE channels.media_supplements s SET state='waiting_retry',next_due=now(),config_revision=%s,error_code=NULL "
        "FROM channels.events e WHERE s.event_key=e.id AND e.connection_id=%s AND e.state<>'withdrawn' "
        "AND s.state='waiting_config' AND s.config_revision<%s RETURNING s.id,s.attempts,s.context,s.lineage_id,s.lineage_version,s.deployment_generation",
        (revision, connection_id, revision))).fetchall()
    for row in rows:
        if service.recovery_policy is not None and row["lineage_id"]:
            new_version = digest([row["lineage_version"], "configuration", revision])
            await service.recovery_policy.update_version_on(connection, row["lineage_id"],
                owner="channels.media:"+row["id"], expected_version=row["lineage_version"],
                current_version=new_version, paused=False, cancelled=False)
            await connection.execute("UPDATE channels.media_supplements SET lineage_version=%s WHERE id=%s",
                                     (new_version, row["id"]))
        await service.runtime.jobs.enqueue(connection, job(row["id"], row["attempts"]+1,
            datetime.now(UTC), (row["context"] or {}).get("actor_id"), row["deployment_generation"]))


async def recover(service):
    async with service.runtime.database.transaction() as conn:
        rows = await (await conn.execute("SELECT * FROM channels.media_supplements WHERE state IN ('waiting_retry','fetching')")).fetchall()
        for row in rows:
            if row["state"] == "waiting_retry":
                await service.runtime.jobs.enqueue(conn, JobSpec("channels.media_supplement",
                    f"media-supplement-recover:{row['id']}:{uuid4()}", {"supplement_id": row["id"]},
                    ServiceClass.INTERACTIVE, row["next_due"], execution_lane=ExecutionLane.CONVERSATION,
                    data_generation=row["deployment_generation"]))
            else:
                await service.runtime.jobs.enqueue(conn, JobSpec("channels.media_supplement_expire",
                    f"media-supplement-expire-recover:{row['id']}:{uuid4()}", {"supplement_id": row["id"], "attempt": row["attempts"]},
                    ServiceClass.INTERACTIVE, row["lease_until"], execution_lane=ExecutionLane.CONVERSATION,
                    data_generation=row["deployment_generation"]))
