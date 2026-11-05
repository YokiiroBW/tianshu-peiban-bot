"""Reauthorize current Growth work without replacing its original request."""

import aiohttp

from tianshu.conversation.codec import context_from_record, document
from tianshu.conversation.generation import attempt_for
from tianshu.models.types import ModelResult
from tianshu.runtime.errors import AppError


AUTHORIZATION_BLOCKED = "reflection_recovery_authorization_required"
IDENTITY_BLOCKED = "reflection_identity_conflict"


def authorization_error(error):
    return isinstance(error, AppError) and (error.status in {401, 403} or error.code in {
        "access_changed", "delivery_access_changed", "actor_disabled", "source_unavailable",
    })


def recovery_permission(context):
    return "companion.manage" if context.permissions & {"*", "companion.manage"} else "companion.read"


def sequence(cursor):
    if cursor is None:
        return 0
    try:
        prefix, value = cursor.split(":", 1)
        if prefix != "e" or int(value) < 0:
            raise ValueError
        return int(value)
    except (ValueError, AttributeError):
        raise AppError("reflection_material_contract_invalid") from None


class GrowthRecovery:
    def __init__(self, growth):
        self.growth = growth
        self.companion = growth.companion

    @staticmethod
    def observes_original(batch, blocked_material):
        return bool(batch and (blocked_material or {}).get("recovery") == "observe_original_attempt")

    @staticmethod
    def same_scene(left, right):
        return ((left.principal_id, left.actor_id, left.person_id, left.conversation_id)
                == (right.principal_id, right.actor_id, right.person_id, right.conversation_id))

    @staticmethod
    def blocked(state):
        return {"state": "blocked", "error_code": AUTHORIZATION_BLOCKED,
                "recovery": "observe_original_attempt" if state["active_batch"] else "reauthorize_original_scope"}

    async def reader(self, original, *, connection=None):
        """Current bounded authorization for reading; never rekey an existing intent."""
        return await self.companion.identity.refresh_scheduled_context(
            original, permission=recovery_permission(original), connection=connection)

    async def block(self, state, error):
        async with self.companion.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, state['runtime_generation'])
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if (not current or current["revision"] != state["revision"]
                    or current["context"] != state["context"] or current["active_batch"] != state["active_batch"]):
                return {"state": "changed_since_recovery_read"}
            if current["last_error"] != AUTHORIZATION_BLOCKED:
                blocked = {"recovery": "reauthorize_original_scope",
                    "error_code": error.code, "previous_error": current["last_error"],
                    "previous_blocked_material": current["blocked_material"],
                    "reason": "原授权已失效；原材料和原请求已保留，需在原权限范围恢复后继续。"}
                await connection.execute(
                    "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s",
                    (AUTHORIZATION_BLOCKED, document(blocked), state["actor_id"], state["scope_key"]))
        return self.blocked(state)

    async def wait_dependency(self, state, batch, wait):
        """Persist a pre-admission dependency wait and its wake atomically."""
        async with self.companion.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, state['runtime_generation'])
            current = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                (state['actor_id'], state['scope_key']))).fetchone()
            if (not current or current['revision'] != state['revision'] or current['active_batch']
                    or current['runtime_generation'] != state['runtime_generation']):
                return {'state': 'changed_since_material_read'}
            waiting = {'recovery': 'dependency_wait', 'reason': wait.reason, 'dependency': wait.dependency,
                       'retry_at': wait.retry_at.isoformat(), 'material_key': batch['material_key'],
                       'inputs': batch['inputs']}
            revision = current['revision'] + 1
            await connection.execute(
                'UPDATE companion.organize_state SET revision=%s,last_error=%s,blocked_material=%s,updated_at=now() '
                'WHERE actor_id=%s AND scope_key=%s',
                (revision, wait.reason, document(waiting), state['actor_id'], state['scope_key']))
            await self.growth._enqueue(connection, state['actor_id'], state['scope_key'], revision,
                'dependency-wait', generation=state['runtime_generation'], scheduled_at=wait.retry_at)
        return {'state': 'waiting_dependency', 'reason': wait.reason, 'retry_at': wait.retry_at}

    async def reconcile_commit(self, actor_id, scope_key, material_key, inputs, generation):
        """Observe an uncertain local commit before scheduling its original result."""
        if not material_key:
            return None
        async with self.companion.database.transaction(read_only=True) as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            state = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s',
                (actor_id, scope_key))).fetchone()
            if state and state['runtime_generation'] == generation and not state['active_batch'] and inputs:
                rows = await (await connection.execute(
                    'SELECT episode_id,revision,reviewed_revision FROM companion.growth_inputs '
                    'WHERE actor_id=%s AND scope_key=%s AND episode_id=ANY(%s)',
                    (actor_id, scope_key, [item[0] for item in inputs]))).fetchall()
                by_id = {row['episode_id']: row for row in rows}
                if all(identifier in by_id and (by_id[identifier]['revision'] > version
                       or by_id[identifier]['reviewed_revision'] >= version) for identifier, version in inputs):
                    return {'state': 'changed_since_original_result_read'}
        if not state or state['runtime_generation'] != generation:
            return None
        batch = state['active_batch']
        if batch and batch['material_key'] != material_key:
            return {'state': 'changed_since_original_result_read'}
        if not batch:
            # The owning batch may already have committed or been superseded;
            # do not interpret that observation as permission to replay it.
            return None
        original = context_from_record(batch['context'])
        attempt_id = attempt_for(material_key)
        try:
            result = await self.growth.models.reusable_result(
                attempt_id, context=original, expected_data_generation=generation)
        except (AppError, TimeoutError, aiohttp.ClientError):
            return None
        if (not isinstance(result, ModelResult) or result.receipt.attempt_id != attempt_id
                or result.receipt.intent_id != material_key or result.receipt.status != 'succeeded'
                or result.receipt.data_generation != generation
                or result.receipt.cancellation_reason == 'explicit_cancel'
                or not any(part.kind == 'text' and part.text for part in result.parts)):
            return None
        async with self.companion.database.transaction() as connection:
            await self.growth.jobs.assert_generation_on(connection, generation)
            current = await (await connection.execute(
                'SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE',
                (actor_id, scope_key))).fetchone()
            active = current['active_batch'] if current and current['runtime_generation'] == generation else None
            if not active or active['material_key'] != material_key:
                return {'state': 'changed_since_original_result_read'}
            await self.growth._enqueue(connection, actor_id, scope_key, current['revision'],
                'commit-reconciled', generation=generation)
        return {'state': 'waiting_commit_recovery', 'attempt_id': attempt_id}

    async def resume(self, connection, current, caller):
        """Reauthorize original materials or observation without replacing an attempt."""
        await self.growth.jobs.assert_generation_on(connection, current['runtime_generation'])
        batch = current["active_batch"]
        previous = context_from_record(batch["context"] if batch else current["context"])
        if not self.same_scene(caller, previous):
            raise AppError("reflection_recovery_context_mismatch", status=403)
        refreshed = await self.reader(previous, connection=connection)
        blocked = current["blocked_material"] or {}
        previous_error = blocked.get("previous_error")
        restored = blocked.get("previous_blocked_material")
        if batch:
            # Keep batch.context, frozen inputs and attempt untouched.
            restored = {"recovery": "observe_original_attempt",
                "reason": "在原权限范围读取原请求结果；不会提交新请求或替换未知结果。"}
        next_revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET last_error=%s,blocked_material=%s,context=%s,revision=%s,updated_at=now() "
            "WHERE actor_id=%s AND scope_key=%s",
            (previous_error, document(restored), document(refreshed), next_revision,
             current["actor_id"], current["scope_key"]))
        await self.growth._enqueue(connection, current["actor_id"], current["scope_key"], next_revision,
                                   "authorization-resume", generation=current['runtime_generation'])
        return next_revision
