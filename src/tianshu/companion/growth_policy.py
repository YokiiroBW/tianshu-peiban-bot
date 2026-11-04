"""Use the shared recovery lineage for actual Growth model attempts."""

from tianshu.conversation.codec import signature
from tianshu.runtime.errors import AppError


OWNER = 'companion.growth'
MAX_ATTEMPTS = 4


def field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


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


async def succeed(policy, connection, batch, attempt_id):
    if policy is None or not batch.get('lineage_id'):
        raise AppError('growth_recovery_lineage_missing', status=503)
    # resolve_unknown_on already recorded this exact physical attempt as
    # succeeded when Models supplied the definitive original receipt.
    if batch.get('recovery_succeeded_attempt_id') == attempt_id:
        return
    return await policy.succeed_attempt_on(connection, attempt_id, probe_token=batch.get('probe_token'))
