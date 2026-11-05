"""Authorize an original prestart attempt after an explicit, auditable reconciliation."""

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef, TrustedContext
from .codec import request_from_wire
from .generation import current_generation_on, generation_id, require_generation
from .ledger import digest, submission_digest


def _identifier(value, maximum):
    return (isinstance(value, str) and 1 <= len(value) <= maximum and bool(value.strip())
            and not any(ord(character) < 32 or ord(character) == 127 for character in value))


async def reconcile(broker, attempt_id, *, context, expected_version, expected_request_digest,
                    expected_data_generation, client_request_id, evidence_ref, decision):
    if not broker.ready:
        raise AppError("broker_unavailable", status=503)
    if not isinstance(context, TrustedContext) or not ({"models.manage", "*"} & context.permissions):
        raise AppError("forbidden", status=403)
    if (not _identifier(attempt_id, 256) or type(expected_version) is not int or expected_version < 0
            or not _identifier(expected_request_digest, 128) or not _identifier(client_request_id, 128)
            or not _identifier(evidence_ref, 512) or decision != "authorized_resume_original"):
        raise AppError("invalid_prestart_reconciliation")
    expected_generation = generation_id(expected_data_generation)
    if broker.validate_persistence is None:
        raise AppError("source_validation_not_bound", status=503)

    # Only internal code reads this frozen request; the reconciliation API
    # returns the original receipt and never returns the request body.
    async with broker.database.transaction(control=True, read_only=True) as connection:
        snapshot = await (await connection.execute(
            "SELECT a.source_refs,s.request,s.streaming FROM models.attempts a "
            "JOIN models.submissions s USING(attempt_id) WHERE a.attempt_id=%s", (attempt_id,))).fetchone()
    if not snapshot or snapshot["request"] is None:
        raise AppError("model_prestart_observation_required", status=503,
                       details={"attempt_id": attempt_id, "action": "observe_original_attempt"})
    original = request_from_wire(snapshot["request"])
    if original.attempt_id != attempt_id or submission_digest(original) != expected_request_digest:
        raise AppError("attempt_identity_conflict", status=409)
    saved_refs = tuple(SourceRef(**value) for value in snapshot["source_refs"] or ())
    refs = tuple({digest(ref): ref for ref in (*original.source_refs, *saved_refs)}.values())
    await broker.recheck_behaviors(original)

    replay = False
    async with broker.database.transaction(control=True) as connection:
        current = await current_generation_on(connection)
        require_generation(expected_generation, current)
        # Lock the current author and original scene/source authorities until
        # the Models CAS commits; neither context is replaced by the other.
        if not await broker.validate_persistence(connection, context, ()):
            raise AppError("forbidden", status=403)
        if not await broker.validate_persistence(connection, original.context, refs):
            raise AppError("source_unavailable", status=403)
        command_key = "models:prestart:" + context.principal_id + ":" + client_request_id
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (command_key,))
        prior = await (await connection.execute(
            "SELECT * FROM models.prestart_reconciliations WHERE operator_principal_id=%s AND client_request_id=%s",
            (context.principal_id, client_request_id))).fetchone()
        if prior:
            if (prior["attempt_id"] != attempt_id or prior["recovery_version"] != expected_version
                    or prior["request_digest"] != expected_request_digest
                    or generation_id(prior["data_generation"]) != expected_generation
                    or prior["evidence_ref"] != evidence_ref):
                raise AppError("client_request_id_conflict", status=409)
            replay = True
        else:
            driver = broker.drivers.get(attempt_id)
            if driver and not driver[0].done():
                raise AppError("model_prestart_check_active", status=409)
            attempt = await (await connection.execute(
                "SELECT * FROM models.attempts WHERE attempt_id=%s FOR UPDATE", (attempt_id,))).fetchone()
            saved = await (await connection.execute(
                "SELECT request,request_digest,recovery_state,recovery_version,recovery_wait "
                "FROM models.submissions WHERE attempt_id=%s FOR UPDATE", (attempt_id,))).fetchone()
            if not attempt or not saved:
                raise AppError("attempt_not_found", status=404)
            require_generation(attempt["data_generation"], expected_generation)
            if (saved["request_digest"] != expected_request_digest or saved["request"] != snapshot["request"]
                    or attempt["source_refs"] != snapshot["source_refs"]):
                raise AppError("attempt_identity_conflict", status=409)
            cancelled = await (await connection.execute(
                "SELECT 1 FROM models.cancellations WHERE attempt_id=%s", (attempt_id,))).fetchone()
            if (attempt["status"] != "queued" or attempt["dispatch"] != "not_started" or attempt["started_at"] is not None
                    or attempt["cancel_requested"] or cancelled
                    or saved["recovery_state"] not in {"checking", "observe_only"}
                    or saved["recovery_version"] != expected_version):
                raise AppError("model_prestart_reconciliation_conflict", status=409)
            await connection.execute(
                "INSERT INTO models.prestart_reconciliations(attempt_id,recovery_version,operator_principal_id,"
                "client_request_id,request_digest,data_generation,evidence_ref,prior_state,prior_wait,previous_broker_instance) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (attempt_id, expected_version, context.principal_id, client_request_id, expected_request_digest,
                 expected_generation, evidence_ref, saved["recovery_state"],
                 Jsonb(saved["recovery_wait"]) if saved["recovery_wait"] is not None else None,
                 attempt["broker_instance"]))
            # Leave the original queued attempt unclaimed. If the HTTP reply or
            # this process is lost after commit, the existing watcher can still
            # discover it, and the old broker can no longer start it.
            await connection.execute("UPDATE models.attempts SET broker_instance=NULL,revision=revision+1 "
                                     "WHERE attempt_id=%s", (attempt_id,))
            await connection.execute("UPDATE models.submissions SET recovery_state=NULL,recovery_due_at=NULL,"
                                     "recovery_wait=NULL,recovery_version=recovery_version+1 WHERE attempt_id=%s",
                                     (attempt_id,))
    if not replay and broker.ready:
        await broker._start_driver(original, streaming=snapshot["streaming"], resume=True)
    row = await broker.ledger.get(attempt_id)
    if row is None:
        raise AppError("attempt_not_found", status=404)
    return broker._receipt(row)
