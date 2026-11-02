"""Attach actual Broker dispatch evidence to the enclosing canonical attempt."""

import asyncio
from contextlib import asynccontextmanager
from .execution_context import CURRENT_EXECUTION
from .external import ProviderError


class TrackedBroker:
    def __init__(self, broker):
        self.broker = broker

    def __getattr__(self, name):
        return getattr(self.broker, name)

    async def _dispatch(self, request, receipt=None):
        current = CURRENT_EXECUTION.get()
        if not current:
            return
        if receipt is None:
            try:
                receipt = await self.broker.status(request.attempt_id)
            except Exception:
                # A broken control connection cannot prove that generation was unsubmitted.
                await current["owner"].submitted(current["attempt"], dispatch="unknown")
                current["submitted"] = True
                return
        if receipt and receipt.dispatch != "not_started":
            await current["owner"].submitted(
                current["attempt"], receipt.provider_handle, receipt.dispatch
            )
            current["submitted"] = True

    async def generate(self, request):
        current = CURRENT_EXECUTION.get()
        if current:
            await current["owner"].owners.check_attempt(current["attempt"])
        try:
            result = await self.broker.generate(request)
        except BaseException:
            await asyncio.shield(self._dispatch(request))
            raise
        await self._dispatch(request, result.receipt)
        return result

    @asynccontextmanager
    async def resource(self, request):
        async with self.broker.resource(request) as permit:
            tracked = TrackedPermit(permit, CURRENT_EXECUTION.get())
            try:
                yield tracked
            except ProviderError as error:
                if error.effect == "none":
                    await permit.rejected(error.code)
                else:
                    await permit.unknown(error.code)
                raise


class TrackedPermit:
    def __init__(self, permit, current):
        self.permit, self.current = permit, current

    async def submitted(self, provider_handle=None):
        if self.current:
            from .execution_context import CURRENT_ACTION
            from .contracts import Fault

            action = CURRENT_ACTION.get()
            if action is not None:
                owner = self.current["owner"]
                await owner.owners.check_attempt(self.current["attempt"])
                handler = await owner.handler(action)
                available = await owner.catalog.availability(action.context, handler)
                if not available.can_execute:
                    raise Fault(available.reason or "capability_unavailable", 503)
        await self.permit.submitted(provider_handle=provider_handle)
        if self.current:
            try:
                await self.current["owner"].owners.check_attempt(self.current["attempt"])
            except Exception:
                await self.permit.rejected("action_execution_owner_changed")
                raise
            await self.current["owner"].submitted(self.current["attempt"], provider_handle)
            self.current["submitted"] = True

    async def finish(self, usage=None, result=None):
        return await self.permit.finish(usage=usage, result=result)

    async def observe_provider(self, state, *, provider_handle):
        if self.current:
            await self.current["owner"].owners.check_attempt(self.current["attempt"])
        observed = await self.permit.observe_provider(state, provider_handle=provider_handle)
        if self.current:
            await self.current["owner"].owners.check_attempt(self.current["attempt"])
        return observed

    async def rejected(self, error_code, usage=None):
        return await self.permit.rejected(error_code, usage=usage)

    async def unknown(self, error_code):
        return await self.permit.unknown(error_code)


async def mark_submitted(provider_handle=None):
    current = CURRENT_EXECUTION.get()
    if current:
        await current["owner"].owners.check_attempt(current["attempt"])
        await current["owner"].submitted(current["attempt"], provider_handle)
        current["submitted"] = True
