"""Read continuing work through Writing's public API and consume actual saved chapters."""
from tianshu.conversation.codec import document, source_refs
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs


async def material(writing,context):
    if writing is None:
        return []
    works=await writing.list(context,limit=8)
    items=[]
    for work in works.items:
        result=await writing.chapters(context,{"work_id":work["id"],"limit":20})
        items.append({"id":work["id"],"title":work["title"],"revision":work["revision"],
            "source_refs":work["source_refs"],"coverage":result["chapters"].coverage,
            "chapters":[{key:row.get(key) for key in ("id","revision","title","position","state","current_revision","generation_id")}
                for row in result["chapters"].items]})
    return items


async def completed(life,context,chapter,refs,connection):
    proof=source_refs(chapter.get("completion_source_refs",()))
    if not proof or not chapter.get("model_attempt_id") or chapter.get("current_revision",0)<1:
        raise AppError("chapter_completion_proof_required")
    await life.validate_persistence(connection,context,unique_refs(tuple(refs),proof))
    current=await (await connection.execute("SELECT c.current_revision,w.actor_id FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE c.id=%s",(chapter["id"],))).fetchone()
    if not current or current["actor_id"]!=context.actor_id or current["current_revision"]!=chapter["current_revision"]:
        raise AppError("chapter_completion_changed",status=409)
    goals=await (await connection.execute(
        "SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] AND state IN ('active','completed') "
        "AND criteria->>'kind'='chapter_saved' AND criteria->>'capability_id'='writing.request' AND criteria->>'work_id'=%s",
        (context.actor_id,list(context.scope_ids),chapter["work_id"]))).fetchall()
    for snapshot in goals:
        intentions=source_refs(snapshot['intention_source_refs'])
        if not await life.sources.validate_persistence(connection,context,intentions):
            # The saved chapter is real even if an unrelated old goal has lost
            # support. It must not roll back or invent completion for that goal.
            continue
        milestones=await (await connection.execute("SELECT m.*,c.current_revision FROM life.goal_milestones m LEFT JOIN life.chapters c ON m.milestone_id='chapter:'||c.id WHERE m.goal_id=%s AND m.satisfied",(snapshot["id"],))).fetchall()
        valid,invalid=[],[]
        for item in milestones:
            if item['milestone_id']=='chapter:'+chapter['id']:
                continue
            if item["version"]==item["current_revision"] and await life.sources.validate_persistence(connection,context,source_refs(item["source_refs"])):
                valid.append(item)
            else:
                invalid.append(item['milestone_id'])
        goal=await (await connection.execute("SELECT * FROM life.goals WHERE id=%s FOR UPDATE",(snapshot["id"],))).fetchone()
        if goal["revision"]!=snapshot["revision"] or goal["state"] not in {'active','completed'}:
            continue
        added=await (await connection.execute(
            "INSERT INTO life.goal_milestones(goal_id,milestone_id,producer,evidence_id,version,source_refs) VALUES (%s,%s,'chapter_saved',%s,%s,%s) "
            "ON CONFLICT(goal_id,milestone_id) DO UPDATE SET evidence_id=excluded.evidence_id,version=excluded.version,source_refs=excluded.source_refs,satisfied=true "
            "WHERE life.goal_milestones.version<excluded.version OR NOT life.goal_milestones.satisfied RETURNING milestone_id",
            (goal["id"],"chapter:"+chapter["id"],chapter["model_attempt_id"],chapter["current_revision"],document(unique_refs(tuple(refs),proof))))).fetchone()
        if not added and not invalid:
            continue
        for identifier in invalid:
            await connection.execute("UPDATE life.goal_milestones SET satisfied=false WHERE goal_id=%s AND milestone_id=%s",(goal["id"],identifier))
        progress=min(1.0,(len(valid)+1)/goal["criteria"]["target_count"])
        supports=unique_refs(intentions,unique_refs(tuple(refs),proof),*(source_refs(item["source_refs"]) for item in valid))
        row=await (await connection.execute(
            "UPDATE life.goals SET progress=%s,state=CASE WHEN %s>=1 THEN 'completed' ELSE 'active' END,source_refs=%s,"
            "reason='chapter_saved_with_original',revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
            (progress,progress,document(supports),goal["id"]))).fetchone()
        await life.directions.record_revision(connection,row,"chapter_saved_with_original")
    from datetime import UTC,datetime
    await life.memory.observe_episode(context,{"episode_id":"chapter:"+chapter["id"],"event_id":"chapter:"+chapter["id"],
        "revision":chapter["current_revision"],"origin_kind":"actor","actor_id":context.actor_id,"subject_id":context.actor_id,
        "occurred_at":datetime.now(UTC),"summary":"章节正文已实际保存："+chapter["title"],"source_refs":unique_refs(tuple(refs),proof),
        "actual_result":{"experience_kind":"writing","content_completed":True,"chapter_id":chapter["id"],"work_id":chapter["work_id"],
            "content_revision":chapter["current_revision"],"model_attempt_id":chapter["model_attempt_id"],"completion_source_refs":proof}},connection=connection)
    await life.signal(context,f"writing-result:{chapter['id']}:{chapter['current_revision']}",connection=connection)
