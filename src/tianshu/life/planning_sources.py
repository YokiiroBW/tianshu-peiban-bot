"""Prepare every existing source and derived direction before domain locks."""

from uuid import NAMESPACE_URL, uuid5

from tianshu.conversation.codec import signature, source_refs
from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs


async def prepare(life, context, decision, frozen, generation_key, daily, refs, contact, day):
    activities = ([decision["activity"]] if decision.get("activity") else []) + decision.get("arrangements", [])
    activity_ids = set(decision.get("cancel_activity_ids", ())) | {
        item["existing_activity_id"] for item in activities if item.get("existing_activity_id")}
    goal_ids = {item["id"] for item in decision.get("goal_changes", ()) if item.get("operation") != "create"}
    goal_ids.update(item["goal_id"] for item in activities if item.get("goal_id") and not item["goal_id"].startswith("new:"))
    async with life.database.transaction(read_only=True) as connection:
        goals = await (await connection.execute(
            "SELECT * FROM life.goals WHERE actor_id=%s AND id=ANY(%s) ORDER BY id",
            (context.actor_id, sorted(goal_ids)))).fetchall()
        targets = await (await connection.execute(
            "SELECT * FROM life.activities WHERE actor_id=%s AND id=ANY(%s) ORDER BY id",
            (context.actor_id, sorted(activity_ids)))).fetchall()
        plan = await (await connection.execute(
            "SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s",
            (context.actor_id, day))).fetchone()
    by_goal = {row["id"]:row for row in goals}
    bases = {item["id"]:item for item in frozen.get("basis", ())}
    events = []
    for change in decision.get("goal_changes", ()):
        operation = change["operation"]
        identifier = str(uuid5(NAMESPACE_URL, generation_key+":direction:"+change["key"])) if operation == "create" else change["id"]
        old = by_goal.get(identifier)
        if operation != "create" and old is None:
            continue
        version = old["revision"]+1 if old else 1
        supports = unique_refs(source_refs(old["intention_source_refs"]) if old else (),
            *(source_refs(bases[key].get("source_refs", ())) for key in change["basis_ids"]))
        parts = (InputPart("text",change["reason"],metadata={"derived_kind":"actor_intention",
            "goal_id":identifier,"operation":operation,"software_completion":False}),)
        events.append({"producer":"life:direction","event_id":f"{identifier}:{version}","revision":1,
            "parts":parts,"support_refs":supports})
    groups = [refs]
    groups.extend(source_refs(row.get("source_refs") or ()) for row in
        [*(daily.get("slots") or ()), *(daily.get("unfinished") or ()), *(daily.get("recent_results") or ()),
         *([daily["plan"]] if daily.get("plan") else []), *goals, *targets])
    groups.extend(source_refs(row.get("intention_source_refs") or ()) for row in goals)
    groups.extend(event["support_refs"] for event in events)
    if plan and set(plan["scope_ids"]) <= set(context.scope_ids):
        groups.append(source_refs(plan["source_refs"]))
    if contact is not None:
        groups.append(contact.candidate.source_refs)
    by_activity = {row["id"]:row for row in targets}
    return {"events":events,"refs":unique_refs(*groups),
        "goals":{identifier:signature(by_goal[identifier]) if identifier in by_goal else None for identifier in goal_ids},
        "activities":{identifier:signature(by_activity[identifier]) if identifier in by_activity else None for identifier in activity_ids},
        "day":day,"day_plan":signature(plan) if plan else None}


async def lock_on(life, connection, context, prepared, contact):
    if contact is not None:
        contexts = {signature(value):value for value in (context,contact.candidate.context)}
        for key in sorted(contexts):
            await life.identity.lock_context_on(connection,contexts[key])
    if prepared["events"]:
        await life.sources.prepare_event_writes(connection,context,prepared["events"],additional_refs=prepared["refs"])
    await life.validate_persistence(connection,context,prepared["refs"])
    if contact is not None:
        await life.validate_persistence(connection,contact.candidate.context,contact.candidate.source_refs)


async def check_rows_on(connection, context, prepared):
    # Reconsideration locks the day plan before signaling the watermark;
    # activity completion takes its activity before its goal and watermark.
    plan = await (await connection.execute(
        "SELECT * FROM life.day_plans WHERE actor_id=%s AND day=%s FOR UPDATE",
        (context.actor_id, prepared["day"]))).fetchone()
    if (plan is None) != (prepared["day_plan"] is None) or (plan is not None and signature(plan) != prepared["day_plan"]):
        raise AppError("planner_preparation_changed",status=409)
    for table, label in (("activities","activities"),("goals","goals")):
        for identifier, expected in sorted(prepared[label].items()):
            row = await (await connection.execute(
                f"SELECT * FROM life.{table} WHERE actor_id=%s AND id=%s FOR UPDATE",
                (context.actor_id,identifier))).fetchone()
            if (row is None) != (expected is None) or (row is not None and signature(row) != expected):
                raise AppError("planner_preparation_changed",status=409)
