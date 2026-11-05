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


async def prepare_completed(life,context,chapter_id):
    """Read all candidate goal/milestone dependencies before any write lock."""
    async with life.database.transaction(read_only=True) as connection:
        chapter=await (await connection.execute(
            "SELECT c.*,w.actor_id,w.source_refs AS work_source_refs,w.revision AS work_snapshot_revision "
            "FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE c.id=%s",(chapter_id,))).fetchone()
        if not chapter or chapter["actor_id"] != context.actor_id:
            raise AppError("chapter_not_found",status=404)
        goals=await (await connection.execute(
            "SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] AND state IN ('active','completed') "
            "AND criteria->>'kind'='chapter_saved' AND criteria->>'capability_id'='writing.request' AND criteria->>'work_id'=%s ORDER BY id",
            (context.actor_id,list(context.scope_ids),chapter["work_id"]))).fetchall()
        milestones=await (await connection.execute(
            "SELECT m.*,c.current_revision FROM life.goal_milestones m LEFT JOIN life.chapters c ON m.milestone_id='chapter:'||c.id "
            "WHERE m.goal_id=ANY(%s) AND m.satisfied ORDER BY m.goal_id,m.milestone_id",
            ([row["id"] for row in goals],))).fetchall()
    groups=[source_refs(row["intention_source_refs"]) for row in goals]
    groups.extend(source_refs(row["source_refs"]) for row in milestones)
    allowed=await life.identity.check_many(context,tuple(groups))
    valid_goals={row["id"]:valid for row,valid in zip(goals,allowed[:len(goals)],strict=True)}
    valid_milestones={(row["goal_id"],row["milestone_id"]):valid for row,valid in
        zip(milestones,allowed[len(goals):],strict=True)}
    refs=unique_refs(source_refs(chapter["source_refs"]),source_refs(chapter["work_source_refs"]),
        *(group for group,valid in zip(groups,allowed,strict=True) if valid))
    return {"chapter":chapter,"goals":goals,"milestones":milestones,"refs":refs,
        "valid_goals":valid_goals,"valid_milestones":valid_milestones}


async def completed(life,context,chapter,refs,connection,*,prepared=None):
    proof=source_refs(chapter.get("completion_source_refs",()))
    if not proof or not chapter.get("model_attempt_id") or chapter.get("current_revision",0)<1:
        raise AppError("chapter_completion_proof_required")
    current=await (await connection.execute("SELECT c.current_revision,w.actor_id FROM life.chapters c JOIN life.works w ON w.id=c.work_id WHERE c.id=%s",(chapter["id"],))).fetchone()
    if not current or current["actor_id"]!=context.actor_id or current["current_revision"]!=chapter["current_revision"]:
        raise AppError("chapter_completion_changed",status=409)
    goals=prepared["goals"] if prepared is not None else await (await connection.execute(
        "SELECT * FROM life.goals WHERE actor_id=%s AND scope_ids<@%s::text[] AND state IN ('active','completed') "
        "AND criteria->>'kind'='chapter_saved' AND criteria->>'capability_id'='writing.request' AND criteria->>'work_id'=%s",
        (context.actor_id,list(context.scope_ids),chapter["work_id"]))).fetchall()
    if prepared is None:
        # Direct domain callers still prepare the complete set before the first
        # goal lock, rather than extending the source set inside the loop.
        milestones=await (await connection.execute("SELECT m.*,c.current_revision FROM life.goal_milestones m LEFT JOIN life.chapters c ON m.milestone_id='chapter:'||c.id WHERE m.goal_id=ANY(%s) AND m.satisfied ORDER BY m.goal_id,m.milestone_id",([row["id"] for row in goals],))).fetchall()
        groups=[source_refs(row['intention_source_refs']) for row in goals]+[source_refs(row['source_refs']) for row in milestones]
        checks=await life.sources.check_many_on(connection,context,tuple(groups))
        valid_goals={row['id']:valid for row,valid in zip(goals,checks[:len(goals)],strict=True)}
        valid_milestones={(row['goal_id'],row['milestone_id']):valid for row,valid in zip(milestones,checks[len(goals):],strict=True)}
        prepared={"goals":goals,"milestones":milestones,"valid_goals":valid_goals,"valid_milestones":valid_milestones}
        await life.validate_persistence(connection,context,unique_refs(tuple(refs),proof,*(group for group,valid in zip(groups,checks,strict=True) if valid)))
    else:
        # The producer prepared this complete set before locking the chapter.
        await life.validate_persistence(connection,context,unique_refs(tuple(refs),proof))
    for snapshot in goals:
        intentions=source_refs(snapshot['intention_source_refs'])
        if not prepared["valid_goals"][snapshot["id"]]:
            # The saved chapter is real even if an unrelated old goal has lost
            # support. It must not roll back or invent completion for that goal.
            continue
        milestones=[row for row in prepared["milestones"] if row["goal_id"]==snapshot["id"]]
        valid,invalid=[],[]
        for item in milestones:
            if item['milestone_id']=='chapter:'+chapter['id']:
                continue
            if item["version"]==item["current_revision"] and prepared["valid_milestones"][(item["goal_id"],item["milestone_id"])]:
                valid.append(item)
            else:
                invalid.append(item['milestone_id'])
        goal=await (await connection.execute("SELECT * FROM life.goals WHERE id=%s FOR UPDATE",(snapshot["id"],))).fetchone()
        if not goal or goal["revision"]!=snapshot["revision"] or goal["state"] not in {'active','completed'}:
            raise AppError("writing_preparation_changed",status=409)
        current_milestones=await (await connection.execute("SELECT m.*,c.current_revision FROM life.goal_milestones m LEFT JOIN life.chapters c ON m.milestone_id='chapter:'||c.id WHERE m.goal_id=%s AND m.satisfied ORDER BY m.milestone_id",(snapshot["id"],))).fetchall()
        others=lambda rows:[item for item in rows if item["milestone_id"] != 'chapter:'+chapter['id']]
        if others(current_milestones) != others(milestones):
            raise AppError("writing_preparation_changed",status=409)
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
