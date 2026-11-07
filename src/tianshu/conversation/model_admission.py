"""Pause incompatible frozen requests and resume them through normal admission."""

import asyncio
from datetime import UTC, datetime, timedelta

from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget

from .codec import context_from_record
from .recovery import is_resource_wait

BLOCKED_ROUTE = "model_no_compatible_route"


class ModelAdmissionWaits:
    def __init__(self, database, models, generations, jobs, schedule):
        self.database, self.models, self.generations = database, models, generations
        self.jobs, self.schedule = jobs, schedule
        self.after, self.next_check = 0, 0.0

    async def block(self, row, attempt_id, error):
        if not isinstance(error, AppError) or error.code != BLOCKED_ROUTE:
            return False
        async with self.database.transaction() as connection:
            if row["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, row["deployment_generation"])
            changed = await (await connection.execute(
                "UPDATE conversation.turns t SET error_code=%s WHERE t.turn_id=%s "
                "AND t.model_attempt_id=%s AND t.state='waiting_external' "
                "AND EXISTS(SELECT 1 FROM conversation.context_selections s "
                "JOIN conversation.generation_requests g USING(attempt_id) "
                "WHERE s.attempt_id=t.model_attempt_id AND s.consumed_at IS NULL "
                "AND s.consumption_state='idle' AND s.model_receipt IS NULL "
                "AND NOT g.submission_confirmed) RETURNING t.turn_id",
                (BLOCKED_ROUTE, row["turn_id"], attempt_id))).fetchone()
        return changed is not None

    async def reconcile(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise AppError("invalid_limit")
        loop = asyncio.get_running_loop()
        if loop.time() < self.next_check:
            return
        self.next_check = loop.time() + 30
        end = loop.time() + 3
        budget = RequestBudget(datetime.now(UTC) + timedelta(seconds=1))
        async with self.database.transaction(budget, read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT t.* FROM conversation.turns t "
                "JOIN conversation.context_selections s ON s.attempt_id=t.model_attempt_id "
                "WHERE t.state='waiting_external' AND t.error_code=%s AND t.sequence>%s "
                "AND s.consumed_at IS NULL AND s.consumption_state='idle' "
                "ORDER BY t.sequence LIMIT %s", (BLOCKED_ROUTE, self.after, limit))).fetchall()
        for row in rows:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            self.after = row["sequence"]
            try:
                async with asyncio.timeout(min(1, remaining)):
                    await self._resume(row)
            except Exception as error:
                # This bounded read/eligibility pass never creates a model
                # attempt. Outages leave the original request blocked.
                if not isinstance(error, (AppError, TimeoutError)) and not is_resource_wait(error):
                    raise
        else:
            if len(rows) < limit:
                self.after = 0

    async def _resume(self, row):
        attempt_id = row["model_attempt_id"]
        context = context_from_record(row["context"])
        result = await self.models.result(attempt_id, context=context)
        submit = False
        if result is None:
            async with self.database.transaction(read_only=True) as connection:
                generation = await (await connection.execute(
                    "SELECT submission_confirmed,recovery_unconfirmed,state "
                    "FROM conversation.generation_requests WHERE attempt_id=%s", (attempt_id,))).fetchone()
            if (not generation or generation["submission_confirmed"] or generation["recovery_unconfirmed"]
                    or generation["state"] not in {"prepared", "claimed"}):
                return
            request = await self.generations.request(attempt_id, context)
            if request is None:
                return
            await self.models.generation_profile(request)
            submit = True
        async with self.database.transaction() as connection:
            if row["deployment_generation"] is not None:
                await self.jobs.assert_generation_on(connection, row["deployment_generation"])
            changed = await (await connection.execute(
                "UPDATE conversation.turns SET error_code=NULL WHERE turn_id=%s AND model_attempt_id=%s "
                "AND state='waiting_external' AND error_code=%s RETURNING turn_id",
                (row["turn_id"], attempt_id, BLOCKED_ROUTE))).fetchone()
            if changed:
                await self.schedule(row, attempt_id, submit=submit, connection=connection)
