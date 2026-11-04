"""The role's current focus is separate from independent background work."""
from datetime import datetime

from .eligibility import background_work


async def conflict(connection, actor_id, activity_id, now):
    state = await (await connection.execute(
        "SELECT activity_id FROM life.actor_state WHERE actor_id=%s FOR UPDATE", (actor_id,))).fetchone()
    if not state or not state["activity_id"] or state["activity_id"] == activity_id:
        return None
    row = await (await connection.execute("SELECT * FROM life.activities WHERE id=%s AND actor_id=%s",
        (state["activity_id"], actor_id))).fetchone()
    if not row or row["state"] != "running" or background_work(row["checkpoint_data"]):
        return None
    end = row["next_due_at"] or (row["checkpoint_data"].get("execution_window") or {}).get("ends_at")
    if isinstance(end, str):
        end = datetime.fromisoformat(end)
    if row["protected_until"] and (end is None or row["protected_until"] > end):
        end = row["protected_until"]
    return {"activity_id": row["id"], "revision": row["revision"], "until": end} if end and end > now else None
