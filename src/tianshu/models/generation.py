"""Fence a Models handoff against the installation generation it was created in."""

from uuid import UUID

from tianshu.runtime.errors import AppError


def generation_id(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise AppError("model_data_generation_required", status=409) from None


async def current_generation_on(connection):
    row = await (await connection.execute(
        "SELECT id FROM runtime.installation_generation WHERE singleton FOR SHARE")).fetchone()
    if not row:
        raise AppError("installation_generation_missing", status=503)
    return generation_id(row["id"])


def require_generation(expected, current):
    try:
        frozen = generation_id(expected)
    except AppError:
        raise AppError("data_generation_expired", status=409) from None
    if frozen != current:
        raise AppError("data_generation_expired", status=409)
