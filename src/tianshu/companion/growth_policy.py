"""Use the shared recovery lineage for actual Growth model attempts."""

from tianshu.conversation.codec import signature
from tianshu.models.ledger import submission_digest
from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import validate_model_recovery_facts


OWNER = 'companion.growth'
MAX_ATTEMPTS = 4


def field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


async def database_now_on(connection):
    """Use the scheduler's database clock when setting or comparing due times."""
    row = await (await connection.execute('SELECT clock_timestamp() AS db_now')).fetchone()
    return row['db_now']


async def reserve_initial(policy, connection, context, batch, request):
    if policy is None:
        raise AppError('growth_recovery_policy_unavailable', status=503)
    version = signature({'actor': context.actor_id, 'scope': sorted(context.scope_ids), 'inputs': batch['inputs']})
    digest = signature({'context': request.context, 'messages': request.messages, 'system': request.system,
                        'source_refs': request.source_refs, 'inputs': batch['inputs']})
    lineage = await policy.register_on(connection, domain='growth', subject_id=context.actor_id, owner=OWNER,
        material_id=batch['material_key'], material_digest=digest, dependency=batch['recovery_dependency'],
        current_version=version, max_attempts=MAX_ATTEMPTS, recovery_kind='pure_computation')
    lineage_id = field(lineage, 'lineage_id')
    if not lineage_id:
        raise AppError('growth_recovery_lineage_invalid', status=503)
    reservation = await policy.reserve_attempt_on(connection, lineage_id, owner=OWNER,
        expected_version=version, attempt_id=request.attempt_id, request_digest=signature(request),
        route=batch['recovery_route'], original_state='none')
    return {'lineage_id': lineage_id, 'recovery_version': version,
            'probe_token': field(reservation, 'probe_token'),
            'recovery_observe_only': field(reservation, 'status') != 'reserved'}


async def fail(policy, connection, batch, attempt_id, code, *, unknown=False):
    if policy is None or not batch.get('lineage_id'):
        raise AppError('growth_recovery_lineage_missing', status=503)
    from tianshu.runtime.recovery_policy import classify_failure
    category = classify_failure(code, external_outcome_unknown=unknown)
    return await policy.fail_attempt_on(connection, attempt_id, code=code, category=category,
                                        probe_token=batch.get('probe_token'))


def bind_receipt(request, receipt):
    """Bind a Models receipt to the frozen domain request, not a local budget guess."""
    if (request is None or not isinstance(receipt, ModelReceipt)
            or receipt.attempt_id != request.attempt_id or receipt.intent_id != request.intent_id
            or receipt.data_generation != request.data_generation):
        raise AppError('growth_recovery_receipt_mismatch', status=409)
    if receipt.request_digest is not None and (
            not isinstance(receipt.request_digest, str) or not receipt.request_digest
            or receipt.request_digest != submission_digest(request)):
        raise AppError('growth_recovery_receipt_mismatch', status=409)


async def lock_receipt_on(connection, receipt, generation):
    """Validate the fresh physical outcome before taking Runtime lineage locks."""
    facts = await lock_recovery_facts_on(connection, receipt.attempt_id)
    validate_model_recovery_facts(facts, receipt=receipt, expected_status=receipt.status,
                                  data_generation=generation)


async def budget_on(policy, connection, batch):
    if policy is None or not batch.get('lineage_id'):
        raise AppError('growth_recovery_lineage_missing', status=503)
    return await policy.computation_budget_on(connection, batch['lineage_id'], owner=OWNER,
                                              expected_version=batch['recovery_version'])


async def note_receipt_on(policy, connection, batch, attempt_id, receipt):
    if policy is None or not batch.get('lineage_id'):
        raise AppError('growth_recovery_lineage_missing', status=503)
    return await policy.note_not_sent_on(connection, attempt_id, owner=OWNER,
                                        expected_version=batch['recovery_version'], model_receipt=receipt)


async def succeed(policy, connection, batch, attempt_id):
    if policy is None or not batch.get('lineage_id'):
        raise AppError('growth_recovery_lineage_missing', status=503)
    # resolve_unknown_on already recorded this exact physical attempt as
    # succeeded when Models supplied the definitive original receipt.
    if batch.get('recovery_succeeded_attempt_id') == attempt_id:
        return
    return await policy.succeed_attempt_on(connection, attempt_id, probe_token=batch.get('probe_token'))
