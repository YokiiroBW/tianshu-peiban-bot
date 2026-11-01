"""The actor's intentions and receipt-backed milestones, never a second fact store."""
from uuid import NAMESPACE_URL, uuid5

from tianshu.conversation.codec import document, source_refs, signature
from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from tianshu.types import OriginKind


def criterion_schema():
    return {"type": "object", "required": ["kind", "capability_id", "target_count"],
        "allOf": [{"if": {"properties": {"kind": {"const": "chapter_saved"}}},
            "then": {"required": ["work_id"], "properties": {
                "capability_id": {"const": "writing.request"}, "work_id": {"type": "string", "minLength": 1}}}},
            {"if":{"properties":{"kind":{"const":"reading_complete"}}},"then":{"properties":{
                "capability_id":{"enum":["video.observe","content.read","life.reading"]}}}},
            {"if":{"properties":{"kind":{"enum":["completed_activity","created_artifact"]}}},
                "then":{"properties":{"capability_id":{"not":{"anyOf":[{"pattern":"^life\\."},{"const":"writing.request"}]}}}}}],
        "additionalProperties": False, "properties": {
            "kind": {"enum": ["completed_activity", "reading_complete", "created_artifact", "chapter_saved"]},
            "work_id": {"type":["string","null"]},
            "capability_id": {"type": "string", "minLength": 1},
            "target_count": {"type": "integer", "minimum": 1, "maximum": 50}}}


def milestone(criteria, capability_id, result, activity):
    """Only an observed final result can satisfy the declared kind of work."""
    if (not criteria or result.execution != "succeeded" or result.effect == "unknown"
            or criteria.get("capability_id") != capability_id
            or (capability_id.startswith("life.") and not (capability_id=='life.reading' and criteria.get('kind')=='reading_complete'))
            or capability_id=="writing.request"):
        return None
    data = result.result
    if data.get("continuation") is not None or data.get("next_position") is not None:
        return None
    if criteria["kind"] == "created_artifact":
        return signature(sorted(item.artifact_id for item in result.artifacts)) if result.artifacts else None
    consumption = data.get("consumption")
    if criteria["kind"] == "reading_complete":
        if capability_id=='content.read':
            if data.get('read_basis')!='actual_source_body' or not data.get('source') or not result.coverage.complete:
                return None
            return signature({'source':data['source'],'content_key':data.get('content_key')})
        if capability_id=='life.reading':
            reading=data.get('reading') or {}
            if (reading.get('state')!='completed' or reading.get('content') is None or not reading.get('source_ref')
                    or reading.get('next_position') is not None or reading.get('completion_basis')!='source_read_not_understanding'):
                return None
            return signature({'source':reading.get('source_ref'),'reading_id':reading.get('id')})
        if (not consumption or consumption.get("analysis_completed") is not True
                or consumption.get("requested_completed") is not True or consumption.get("next_position") is not None):
            return None
        # This is completion of the requested window. Sampling omissions stay
        # on the original receipt; they never become a claim of exhaustive reading.
        return signature({"source": consumption.get("source"), "requested": consumption.get("requested")})
    if criteria["kind"] == "completed_activity":
        return activity["id"]
    return None


class Directions:
    def __init__(self, life):
        self.life = life

    async def record_revision(self, connection, row, reason, *, generation_key=None):
        await connection.execute(
            "INSERT INTO life.goal_revisions(goal_id,revision,reason,snapshot,source_refs,generation_key) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (row["id"], row["revision"], reason, document({key: row[key] for key in
                ("title", "motivation", "state", "priority", "progress", "criteria", "origin")}),
             document(row["source_refs"]), generation_key))

    async def apply(self, connection, context, changes, frozen_data, generation_key, now):
        aliases = {}
        bases = {item["id"]: item for item in frozen_data.get("basis", ())}
        originals = {item["id"]: item for item in frozen_data["goals"]}
        for change in changes:
            refs = unique_refs(*(source_refs(bases[key].get("source_refs", ())) for key in change["basis_ids"]))
            operation = change["operation"]
            if operation == "create":
                goal_id = str(uuid5(NAMESPACE_URL, generation_key + ":direction:" + change["key"]))
                aliases["new:" + change["key"]] = goal_id
                old = None
            else:
                goal_id = change["id"]
                old = await self.life.lock_row(connection, context,
                    "SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE",
                    (goal_id, context.actor_id), additional_refs=refs)
                if not old or old["revision"] != originals[goal_id]["revision"]:
                    raise AppError("planner_goal_changed", status=409)
            intentions = unique_refs(source_refs(old["intention_source_refs"]) if old else (),refs)
            await self.life.validate_persistence(connection, context, intentions)
            state = {"pause": "paused", "abandon": "cancelled"}.get(operation, "active")
            version = old["revision"] + 1 if old else 1
            actual_refs = await self.life.sources.record_event(context, producer="life:direction",
                event_id=f"{goal_id}:{version}", revision=1, occurred_at=now, origin_kind=OriginKind.ACTOR,
                parts=(InputPart("text", change["reason"], metadata={"derived_kind": "actor_intention",
                    "goal_id": goal_id, "operation": operation, "software_completion": False}),),
                support_refs=intentions, connection=connection)
            intentions = unique_refs(intentions,actual_refs)
            refs = unique_refs(source_refs(old["source_refs"]) if old else (),intentions)
            row = await (await connection.execute(
                "INSERT INTO life.goals(id,actor_id,revision,title,motivation,state,priority,criteria,origin,reason,scope_ids,source_refs,intention_source_refs) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'autonomous',%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,title=excluded.title,motivation=excluded.motivation,"
                "state=excluded.state,priority=excluded.priority,criteria=excluded.criteria,reason=excluded.reason,"
                "source_refs=excluded.source_refs,intention_source_refs=excluded.intention_source_refs,updated_at=now() RETURNING *",
                (goal_id, context.actor_id, version, change["title"], change["motivation"], state,
                 change["priority"], document(change["criteria"]), change["reason"], list(context.scope_ids), document(refs),document(intentions)))).fetchone()
            await self.record_revision(connection, row, change["reason"], generation_key=generation_key)
        return aliases

    async def observe_result(self, connection, context, activity, result, result_revision, capability_id, refs):
        if not activity["goal_id"]:
            return
        goal = await self.life.lock_row(connection, context,
            "SELECT * FROM life.goals WHERE id=%s AND actor_id=%s FOR UPDATE",
            (activity["goal_id"], context.actor_id), additional_refs=refs)
        if not goal or goal["state"] != "active":
            return
        key = milestone(goal["criteria"], capability_id, result, activity)
        if key is None:
            return
        added = await (await connection.execute(
            "INSERT INTO life.goal_progress_receipts(goal_id,action_id,receipt_id,activity_id,result_revision,satisfied,source_refs,milestone_key) "
            "VALUES (%s,%s,%s,%s,%s,true,%s,%s) ON CONFLICT DO NOTHING RETURNING action_id",
            (goal["id"], result.action_id, result.receipt_id, activity["id"], result_revision, document(refs), key))).fetchone()
        if not added:
            return
        count = await (await connection.execute(
            "SELECT count(*) AS value FROM life.goal_progress_receipts WHERE goal_id=%s AND satisfied", (goal["id"],))).fetchone()
        progress = min(1.0, count["value"] / goal["criteria"]["target_count"])
        row = await (await connection.execute(
            "UPDATE life.goals SET progress=%s,state=CASE WHEN %s>=1 THEN 'completed' ELSE state END,"
            "revision=revision+1,reason='declared_milestone_confirmed_by_receipt',source_refs=%s,updated_at=now() WHERE id=%s RETURNING *",
            (progress, progress,document(unique_refs(source_refs(goal["source_refs"]),refs)), goal["id"]))).fetchone()
        await self.record_revision(connection, row, "declared_milestone_confirmed_by_receipt")
