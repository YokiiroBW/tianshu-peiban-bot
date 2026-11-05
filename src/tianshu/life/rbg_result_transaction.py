"""Commit a Life result and its consumer evidence on the same connection."""
from contextlib import asynccontextmanager


@asynccontextmanager
async def result_transaction(database, on_complete):
    async with database.transaction() as connection:
        if on_complete is not None and hasattr(on_complete, "begin"):
            await on_complete.begin(connection)
        outcome = {"disposition": "done", "reason": None, "proof": {}}
        yield connection, outcome
        if on_complete is not None:
            await on_complete(connection, outcome["disposition"], reason=outcome["reason"], proof=outcome["proof"])
