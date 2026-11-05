"""Serialize a plan's admission and business retirement by physical attempt."""


def plan_owner(context):
    from .ledger import digest

    scene = {key: sorted(getattr(context,key)) if key in {"scope_ids","audience"} else getattr(context,key)
        for key in ("principal_id","actor_id","person_id","conversation_id","conversation_kind",
                    "scope_ids","audience","delivery_allowed")}
    return "life.plan:" + digest(scene)


async def lock_admission_on(connection, attempt_id):
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("models-admission:" + attempt_id,))
