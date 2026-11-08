"""Finish resource accounting while the caller is being cancelled."""

import asyncio
from contextlib import asynccontextmanager


async def finish_cleanup(awaitable):
    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


@asynccontextmanager
async def transaction_on(connection):
    """Settle native transaction entry/exit before an outer block can unwind.

    psycopg pushes its savepoint stack before awaiting BEGIN/SAVEPOINT. A
    cancellation during __aenter__ otherwise skips __aexit__, leaving the outer
    transaction to pop somebody else's savepoint. No body runs concurrently
    with entry or cleanup, and caller cancellation still propagates.
    """
    transaction = connection.transaction()
    entering = asyncio.ensure_future(transaction.__aenter__())
    try:
        await asyncio.shield(entering)
    except BaseException as error:
        try:
            await finish_cleanup(entering)
        except BaseException:
            pass
        try:
            await finish_cleanup(transaction.__aexit__(type(error),error,error.__traceback__))
        except BaseException:
            # An entry failure remains the authority; the owner still rolls
            # back/closes its outer transaction before releasing the connection.
            pass
        raise
    try:
        yield connection
    except BaseException as error:
        if not await finish_cleanup(transaction.__aexit__(type(error),error,error.__traceback__)):
            raise
    else:
        exiting = asyncio.ensure_future(transaction.__aexit__(None,None,None))
        try:
            await asyncio.shield(exiting)
        except asyncio.CancelledError:
            await finish_cleanup(exiting)
            raise
