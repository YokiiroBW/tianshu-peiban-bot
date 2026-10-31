"""Keep unavailable channel comparisons explicit until they can be completed."""

from .request_control import Deferred


def defer_channel(report, account, error, stage):
    rows = report.setdefault("deferred", [])
    key = account["account_id"]
    previous = next((row for row in rows if row["account_id"] == key), None)
    value = {
        "provider": account["provider"],
        "account_id": key,
        "code": error.code,
        "retry_at": error.until,
        "stage": stage,
    }
    if previous:
        if value["retry_at"] >= previous["retry_at"]:
            previous.update(value)
    else:
        rows.append(value)
    report["complete"] = False


def deferred_error(report):
    rows = report.get("deferred", [])
    if not rows:
        return None
    first = min(rows, key=lambda row: row["retry_at"])
    return Deferred(first["retry_at"], first["code"])
