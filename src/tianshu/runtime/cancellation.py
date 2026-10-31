"""Finish resource accounting while the caller is being cancelled."""

import asyncio


async def finish_cleanup(awaitable):
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()
