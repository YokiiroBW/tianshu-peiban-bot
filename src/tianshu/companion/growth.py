"""Incremental subjective reflections and revocable expression habits."""
from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from tianshu.models.types import ModelRequest
from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ContextContribution, RequestBudget, ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature, source_refs
from tianshu.life.validation import revision, text
from tianshu.life.writing import generated_text
from .types import AffectUpdate, ReflectionProposal


class GrowthService:
    def __init__(self, companion, jobs, models, assembler, *, on_projection_changed):
        self.companion, self.jobs, self.models, self.assembler = companion, jobs, models, assembler
        self.on_projection_changed = on_projection_changed

    async def changed(self, context, watermark, *, connection=None):
        if connection is None:
            await self.companion.identity.check(context)
            async with self.companion.database.transaction() as transaction:
                return await self.changed(context, watermark, connection=transaction)
        await self.companion.validate_persistence(connection, context)
        scope_key = signature(sorted(context.scope_ids))
        row = await (await connection.execute(
            "INSERT INTO companion.organize_state(actor_id,scope_key,watermark,context) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(actor_id,scope_key) DO UPDATE SET watermark=excluded.watermark,context=excluded.context,"
            "revision=companion.organize_state.revision+1 WHERE companion.organize_state.watermark<>excluded.watermark RETURNING revision",
            (context.actor_id, scope_key, watermark, document(context)))).fetchone()
        if row:
            await self.jobs.enqueue(connection, JobSpec("companion.organize", f"organize:{context.actor_id}:{scope_key}:{row['revision']}",
                {"actor_id": context.actor_id, "scope_key": scope_key, "expected_revision": row["revision"]},
                scheduled_at=datetime.now(UTC)+timedelta(seconds=30), subject_id=context.actor_id))

    async def organize(self, actor_id, scope_key, expected_revision):
        async with self.companion.database.transaction(read_only=True) as connection:
            state = await (await connection.execute("SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s", (actor_id, scope_key))).fetchone()
        if not state or state["revision"] != expected_revision or state["reviewed_watermark"] == state["watermark"]:
            return
        context = context_from_record(state["context"])
        await self.companion.identity.check(context)
        page = await self.companion.memory.episodes_since(context, state["cursor"], 32)
        if not page.items:
            await self._reviewed(actor_id, scope_key, expected_revision, page.watermark, False)
            return
        actor = await self.companion.actor(context)
        persona = await self.companion.persona(context, actor["persona_id"], actor["persona_revision"]) if actor["persona_id"] else None
        refs = tuple({(ref.source_id, ref.revision): ref for episode in page.items for ref in episode.source_refs}.values())
        material = {"episodes": wire(page.items), "instruction":
            "Reflect on actual new experiences. Return JSON {notes:[{kind:'reflection'|'interest'|'style',title,content,episode_ids,conditions,counterexamples,affect:null|{appraisal,valence,arousal,stress,social_need,confidence,goal_relevance,attribution,control}}]}. "
            "No change is a valid result. Separate interpretation from observed fact. Cite only supplied episode IDs. "
            "Interests are the actor's experience, not copied user preferences. Habits are contextual and revisable; never rewrite authored identity or infer permissions. "
            "Do not treat prior reflections/diaries as independent evidence for the described event."}
        budget = RequestBudget(datetime.now(UTC)+timedelta(minutes=3))
        pack = await self.assembler.assemble(context, "reflect", "Review new experiences without forcing a change.", budget,
            persona_text=persona.prompt_text if persona else "", reserved_output_tokens=3000,
            contributions=(ContextContribution("new_experiences", json.dumps(material, ensure_ascii=False), refs, context.scope_ids, 80, True, str(expected_revision)),))
        result = await self.models.generate(ModelRequest(f"organize:{actor_id}:{scope_key}:{expected_revision}", str(uuid4()), "reflect", ServiceClass.ELASTIC,
            context, budget, tuple(pack.messages), system=pack.system, max_output_tokens=3000,
            estimated_input_tokens=pack.token_usage.estimated_tokens, source_refs=tuple(pack.source_refs)))
        if result.receipt.status != "succeeded":
            raise AppError(result.receipt.error_code or "reflection_generation_failed")
        await self.companion.identity.check(context, tuple(pack.source_refs))
        try:
            notes = json.loads(generated_text(result))["notes"]
        except (ValueError, KeyError, TypeError):
            raise AppError("reflection_output_invalid") from None
        if not isinstance(notes, list) or len(notes)>16:
            raise AppError("reflection_output_invalid")
        episodes = {episode.episode_id: episode for episode in page.items}
        relationship = await self.companion.memory.relationship(context, context.person_id) if context.person_id else None
        changed = False
        for note in notes:
            if not isinstance(note, dict) or note.get("kind") not in {"reflection", "interest", "style"}:
                raise AppError("reflection_output_invalid")
            supports = note.get("episode_ids", [])
            if not supports or any(item not in episodes for item in supports):
                raise AppError("reflection_support_invalid")
            supports = tuple(sorted(set(supports)))
            original_refs = tuple({(ref.source_id, ref.revision): ref for item in supports for ref in episodes[item].source_refs}.values())
            await self.companion.identity.check(context, original_refs)
            proposal_id = str(uuid5(NAMESPACE_URL, f"reflection:{actor_id}:{note['kind']}:{':'.join(supports)}"))
            content = text(note.get("content"), "主观记录", 10000)
            title = text(note.get("title"), "记录标题", 500)
            conditions, counterexamples = note.get("conditions", {}), note.get("counterexamples", [])
            if not isinstance(conditions, dict) or not isinstance(counterexamples, list) or any(not isinstance(item, str) for item in counterexamples):
                raise AppError("reflection_conditions_invalid")
            proposal = ReflectionProposal(proposal_id, actor_id, supports, content, note["kind"], datetime.now(UTC),
                original_refs, context.scope_ids, conditions=conditions, counterexamples=tuple(counterexamples))
            await self.companion.memory.record_reflection(context, proposal)
            # This is a current expression projection, never a second fact ledger.
            if note["kind"] in {"style", "interest"} and not (note["kind"] == "style" and relationship and relationship["frozen"]):
                async with self.companion.database.transaction() as connection:
                    await self.companion.validate_persistence(connection, context, original_refs)
                    inserted = await (await connection.execute(
                        "INSERT INTO companion.style_rules(id,actor_id,person_id,kind,title,content,conditions,counterexamples,episode_ids,source_refs,scope_ids,state) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active') ON CONFLICT(id) DO UPDATE SET "
                        "title=excluded.title,content=excluded.content,conditions=excluded.conditions,counterexamples=excluded.counterexamples,"
                        "source_refs=excluded.source_refs,scope_ids=excluded.scope_ids,revision=companion.style_rules.revision+1,updated_at=now() "
                        "WHERE companion.style_rules.source_refs<>excluded.source_refs RETURNING id",
                        (proposal_id, actor_id, context.person_id, note["kind"], title, content, document(conditions), document(counterexamples),
                         list(supports), document(original_refs), list(context.scope_ids)))).fetchone()
                    changed = changed or bool(inserted)
            appraisal = note.get("affect")
            if appraisal is not None:
                if not isinstance(appraisal, dict):
                    raise AppError("invalid_appraisal")
                update = AffectUpdate(proposal_id, max(episodes[item].revision for item in supports),
                    text(appraisal.get("appraisal"), "事件评价", 2000),
                    *(float(appraisal.get(key, 0)) for key in ("valence", "arousal", "stress", "social_need", "confidence")),
                    21600, max(episodes[item].occurred_at for item in supports), original_refs,
                    goal_relevance=float(appraisal.get("goal_relevance", 0)), attribution=appraisal.get("attribution", "unknown"), control=float(appraisal.get("control", .5)))
                await self.companion.apply_affect(context, update)
        await self._reviewed(actor_id, scope_key, expected_revision, page.watermark, changed)
        if changed:
            await self.on_projection_changed(context, f"growth:{scope_key}:{page.watermark}")
        if page.next_cursor:
            await self.changed(context, f"continuation:{state['watermark']}:{page.next_cursor}")

    async def _reviewed(self, actor_id, scope_key, expected_revision, cursor, changed):
        async with self.companion.database.transaction() as connection:
            await connection.execute("UPDATE companion.organize_state SET cursor=%s,reviewed_watermark=watermark,last_reviewed_at=now(),last_changed_at=CASE WHEN %s THEN now() ELSE last_changed_at END WHERE actor_id=%s AND scope_key=%s AND revision=%s", (cursor, changed, actor_id, scope_key, expected_revision))

    async def view(self, context, *, cursor=None, limit=40):
        await self.companion.identity.check(context)
        limit = min(100, max(1, int(limit)))
        async with self.companion.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.style_rules WHERE actor_id=%s AND (person_id IS NULL OR person_id=%s) AND scope_ids<@%s::text[] "
                "AND (%s::text IS NULL OR id>%s) ORDER BY id LIMIT %s",
                (context.actor_id, context.person_id, list(context.scope_ids), cursor, cursor, limit+1))).fetchall()
        permitted = await self.companion.identity.check_many(context, tuple(source_refs(row["source_refs"]) for row in rows[:limit]))
        items = [{**row, "occurred_at": row["created_at"], "evidence": row["source_refs"], "can_modify": True} for row, valid in zip(rows[:limit], permitted, strict=True) if valid]
        projection = await self.companion.memory.projection(context, context.person_id)
        notes = [{"id": item.proposal_id, "revision": item.revision, "kind": "reflection", "title": item.content[:60],
                  "content": item.content, "state": "active", "occurred_at": item.occurred_at,
                  "evidence": item.source_refs, "source_refs": item.source_refs, "episode_ids": item.episode_ids,
                  "conditions": item.conditions, "counterexamples": item.counterexamples, "can_modify": False}
                 for item in projection["reflections"] if item.kind == "reflection"]
        return {"items": items, "next_cursor": rows[limit-1]["id"] if len(rows)>limit else None,
                "reflections": notes, "missing": [] if all(permitted) else ["source_unavailable"],
                "evidence": await self.companion.memory.style_view(context, context.person_id)}

    async def material(self, context):
        view = await self.view(context, limit=40)
        rules = [row for row in view["items"] if row["state"] == "active"]
        if not rules:
            return ()
        refs = tuple({(ref.source_id, ref.revision): ref for row in rules for ref in source_refs(row["source_refs"])}.values())
        return (ContextContribution("learned_habits", json.dumps(wire(rules), ensure_ascii=False), refs, context.scope_ids, 55),)

    async def set_state(self, context, body, state):
        if state not in {"active", "revoked"}:
            raise AppError("invalid_growth_state")
        await self.companion.identity.check(context)
        async with self.companion.database.transaction(read_only=True) as connection:
            original = await (await connection.execute("SELECT * FROM companion.style_rules WHERE id=%s AND actor_id=%s AND scope_ids<@%s::text[]", (body.get("id"), context.actor_id, list(context.scope_ids)))).fetchone()
        if not original:
            raise AppError("growth_item_not_found", status=404)
        await self.companion.identity.check(context, source_refs(original["source_refs"]))
        async def apply(connection):
            row = await (await connection.execute("SELECT * FROM companion.style_rules WHERE id=%s FOR UPDATE", (original["id"],))).fetchone()
            revision(row, body.get("expected_revision"))
            await connection.execute("UPDATE companion.style_rules SET state=%s,reason=%s,revision=revision+1,updated_at=now() WHERE id=%s", (state, "explicit_user_change", row["id"]))
            return CommandResult(body["client_request_id"], {"id": row["id"], "state": state}, row["revision"]+1)
        return await self.companion.commands.execute(context, "companion.growth."+state, body["client_request_id"], signature(body), apply)
