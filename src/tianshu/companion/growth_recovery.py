"""Reauthorize current Growth work without replacing its original request."""

from tianshu.conversation.codec import context_from_record, document
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
