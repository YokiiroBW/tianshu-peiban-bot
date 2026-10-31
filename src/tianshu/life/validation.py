from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from tianshu.runtime.errors import AppError


def text(value, name, maximum=4000, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise AppError("invalid_input", f"请检查{name}")
    return value


def timestamp(value, *, optional=False):
    if optional and value is None:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError):
        raise AppError("invalid_time", "时间必须包含时区") from None


def timezone(value):
    try:
        ZoneInfo(value)
    except (ValueError, KeyError, TypeError):
        raise AppError("invalid_timezone") from None
    return value


def revision(row, expected):
    if row is None:
        if expected not in (None, 0):
            raise AppError("revision_conflict", status=409)
    elif type(expected) is not int or expected != row["revision"]:
        raise AppError("revision_conflict", status=409, details={"revision": row["revision"]})
