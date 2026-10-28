"""AL13 private diary/derived-question acceptance on the live Companion owner."""

import asyncio
import json
import time
from datetime import datetime
from uuid import uuid4

from native_autonomous_fixtures import ACTOR, require

QUESTION = "AL13_WITHDRAWN_QUESTION 原文中的未读部分是否改变结论？"
REFLECTION = "AL13_REFLECTION 我对已读原件的理解仍限于实际读取区间。"
PRIVATE_PROSE = "AL13_PRIVATE_PROSE 我回顾了确实收到的数字阅读材料；未读部分仍待核验。"


def extend_provider(provider):
    original = provider.recorded_response

    async def respond(request):
        from aiohttp import web

        body = await request.json()
        messages = body.get("messages", [])
        system = "\n".join(
            str(x.get("content", "")) for x in messages if x["role"] == "system"
        )
        if not system.startswith("Write a sourced diary"):
            return await original(request)
        require(
            request.headers.get("Authorization") == "Bearer " + provider.token,
            "real Gateway model credential on diary generation",
        )
        captured = json.loads(messages[1]["content"])
        entries = captured["material"]["entries"]
        require(entries, "diary model receives actual selected material")
        refs = entries[0]["episode_refs"]
        audience = captured["material"]["context"]["audience"]["kind"]
        public = audience == "public"
        scoped = audience in {"group", "private"}
        prose = (
            "AL13_PUBLIC_PROSE 公开原件的已读片段。"
            if public
            else "AL13_SCOPE_PROSE 只向已订阅的当前受众分享已获准片段。"
            if scoped
            else PRIVATE_PROSE
        )
        value = dict(
            content=prose,
            reflections=[]
            if public or scoped
            else [
                dict(
                    kind="reflection",
                    text=REFLECTION,
                    episode_refs=refs,
                    importance="normal",
                    retention="medium",
                )
            ],
            questions=[] if public or scoped else [QUESTION],
        )
        message = dict(role="assistant", content=json.dumps(value, ensure_ascii=False))
        provider.calls.append(
            dict(
                label="al13-public-diary"
                if public
                else "al13-scope-diary"
                if scoped
                else "al13-private-diary",
                messages=messages,
                model=body["model"],
                stream=bool(body.get("stream")),
                reply=message,
                started=time.time(),
                finished=time.time(),
            )
        )
        usage = dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        if body.get("stream"):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for frame in (
                dict(choices=[dict(index=0, delta=message, finish_reason=None)]),
                dict(
                    choices=[dict(index=0, delta={}, finish_reason="stop")], usage=usage
                ),
            ):
                await response.write(
                    ("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode()
                )
            await response.write(b"data: [DONE]\n\n")
            return response
        return web.json_response(
            dict(
                id="synthetic-al13:" + uuid4().hex,
                object="chat.completion",
                created=1,
                model=body["model"],
                choices=[dict(index=0, message=message, finish_reason="stop")],
                usage=usage,
            )
        )

    provider.recorded_response = respond
    return provider


async def private_diary(host, chronology):
    from tianshu_companion.life import zone

    stamp = datetime.fromisoformat(chronology["window"]["start"].replace("Z", "+00:00"))
    day = stamp.astimezone(zone(chronology["timezone"])).date().isoformat()

    async def prepare(app):
        core = app.state.core
        require(
            not core.store.list("proactive_subscriptions", states=["active"]),
            "no subscription scenario is genuinely unsubscribed",
        )
        actor = core.store.get("life_actors", ACTOR)
        actor["diary_prepared_day"] = day
        core.store.put("life_actors", actor)
        return await core.life.diary_sources.prepare(ACTOR, day)

    result = await host.exchange.invoke("companion", prepare)
    diary_id = result["id"]

    async def generate(app):
        core = app.state.core
        await core.life._diary_work(ACTOR)
        return core.store.get("life_diaries", diary_id)

    diary = None
    for _ in range(8):
        diary = await host.exchange.invoke("companion", generate)
        if diary["state"] == "draft":
            break
        require(
            diary["state"] not in {"failed", "unavailable", "material_incomplete"},
            "private diary must finish with source-checked text: "
            + str(diary["state"]),
        )
        await asyncio.sleep(0.05)
    require(
        diary and diary["state"] == "draft" and diary["published_revision"] is None,
        "unsubscribed completed diary remains private draft",
    )

    async def verify(app):
        from tianshu_companion.contracts import Fault

        core = app.state.core
        revision = await core.life.admin_read_revision_verified(
            diary["current_revision"]
        )
        require(
            revision["content"] == PRIVATE_PROSE,
            "original admin verified read returns private draft",
        )
        try:
            core.life.admin_read_revision(diary["current_revision"])
        except Fault:
            pass
        else:
            raise AssertionError("sync admin revision cannot bypass AL13 source proof")
        material = await core.actor_memory.query(
            ACTOR,
            dict(purpose="reflection", audience=dict(kind="actor_only")),
            "AL13_REFLECTION",
            limit=8,
            budget_bytes=16384,
        )
        require(
            any(
                row["kind"] == "reflection" and REFLECTION in row["text"]
                for row in material["items"]
            ),
            "actual original Memory recall contains sourced diary reflection",
        )
        concerns = [
            r for r in core.store.list("life_concerns") if r["title"] == QUESTION
        ]
        require(
            len(concerns) == 1 and concerns[0].get("autonomy_basis"),
            "question saved once with original basis",
        )
        require(
            not core.store.list("proactive_motives"),
            "no subscription produces zero share motives",
        )
        require(not core.store.list("turns"), "diary never manufactures a user turn")
        return dict(
            diary_id=diary_id,
            revision_id=revision["id"],
            question_id=concerns[0]["id"],
            reflection_recalled=True,
            public_pointer=None,
            no_share_motive=True,
            source_refs=diary["source_capture"]["proofs"],
        )

    return await host.exchange.invoke("companion", verify)


async def after_private_withdrawal(host, evidence, provider):
    async def check(app):
        from tianshu_companion.contracts import Fault
        from tianshu_companion.life_goals import AutonomousGoals

        core = app.state.core
        try:
            await core.life.admin_read_revision_verified(evidence["revision_id"])
        except Fault:
            pass
        else:
            raise AssertionError(
                "withdrawn original blocks verified admin private draft read"
            )
        planner = AutonomousGoals(core.runtime_execution)
        actor = core.store.get("life_actors", ACTOR)
        actor["autonomy_due_at"] = 0
        actor.pop("autonomy_operation", None)
        core.store.put("life_actors", actor)
        before = len(provider.calls)
        await planner.work(actor_id=ACTOR)
        recent = [
            r
            for r in provider.calls[before:]
            if r["label"].startswith("autonomous-") and "goal" in r["label"]
        ]
        require(
            recent, "source-withdrawal test actually reached original planner model"
        )
        require(
            all(
                QUESTION not in json.dumps(r["messages"], ensure_ascii=False)
                for r in recent
            ),
            "withdrawn derived concern never enters planning model messages",
        )
        return dict(
            admin_read_blocked=True,
            planner_called=True,
            revoked_question_not_in_model=True,
        )

    return await host.exchange.invoke("companion", check)


async def public_diary(host, helper, state, url, private_evidence):
    async def prepare(app):
        from tianshu_companion.diary_disclosure import DiaryDisclosure
        from tianshu_companion.life import zone

        core = app.state.core

        async def action(operation, value, expected=0):
            return await core.reading.action(
                "companion",
                dict(
                    schema_version=2,
                    request_id="al13-reading:" + uuid4().hex,
                    actor_id=ACTOR,
                    operation=operation,
                    expected_version=expected,
                    value=value,
                ),
                return_content=True,
            )

        acquired = await action(
            "content.acquire",
            dict(source=dict(kind="url", url=url), purpose="read", query=None),
        )
        reading_id = "al13-public-reading"
        await action(
            "reading.open",
            dict(
                id=reading_id,
                scope=None,
                query=None,
                content_ref=acquired["content_ref"],
                mode="solo",
                participants=[],
            ),
        )
        await action(
            "reading.read",
            dict(
                id=reading_id,
                query=None,
                range=dict(unit="characters", start=0, end=64),
            ),
            expected=1,
        )
        pending = [
            r
            for r in core.store.list("outbox", states=["actor_memory_pending"])
            if r.get("actor_id") == ACTOR
        ]
        for item in pending:
            await core.actor_memory.flush(item)
            require(
                core.store.get("outbox", item["id"])["state"]
                == "actor_memory_delivered",
                "actual public original reading episode delivered",
            )
        actor = core.store.get("life_actors", ACTOR)
        world = core.life._get("worlds", actor["world_id"])
        today = (
            datetime.fromtimestamp(core.clock(), zone(world["timezone"]))
            .date()
            .isoformat()
        )
        capture = await core.life.diary_sources.compose(
            ACTOR, today, dict(purpose="diary", audience=dict(kind="actor_only"))
        )
        await DiaryDisclosure(core.actor_memory).project(capture)
        result = await core.life.diary_sources.prepare(ACTOR, today, public=True)
        material = core.store.get("life_materials", result["id"])["material"]
        require(
            material["entries"]
            and PRIVATE_PROSE not in json.dumps(material, ensure_ascii=False),
            "public model material selected independently without private diary prose",
        )
        require(
            "AL13_SYNTHETIC_CHRONOLOGY" not in json.dumps(material, ensure_ascii=False),
            "public projection omits private chronology/motivation",
        )
        return result

    result = await host.exchange.invoke("companion", prepare)
    diary_id = result["id"]

    async def generate(app):
        core = app.state.core
        await core.life._diary_work(ACTOR)
        return core.store.get("life_diaries", diary_id)

    for _ in range(8):
        diary = await host.exchange.invoke("companion", generate)
        if diary["state"] == "draft":
            break
        require(
            diary["state"] not in {"failed", "unavailable", "material_incomplete"},
            "public diary source-checked generation: " + str(diary["state"]),
        )
        await asyncio.sleep(0.05)
    require(diary["state"] == "draft", "public draft generation settled")

    async def publish(app):
        core = app.state.core
        current = core.store.get("life_diaries", diary_id)
        await core.life.diary_sources.publish(
            diary_id, reviewer="synthetic-native-reviewer", expected=current["version"]
        )
        access = core.store.get("life_access", ACTOR)
        core.life.set_diary_access(
            ACTOR,
            readers=["al13-native-reader"],
            expected=access["version"] if access else None,
        )
        return core.store.get("life_diaries", diary_id)

    diary = await host.exchange.invoke("companion", publish)
    token = state["al13_core_token"]

    async def call(operation, payload):
        return await helper.json_call(
            host,
            "platform",
            "companion",
            "/internal/v1/life-read/" + operation,
            token,
            payload,
        )

    listed = await call("diaries", dict(schema_version=1, actor_id=ACTOR, limit=20))
    require(listed["status"] == 200, "original Life reader HTTP list")
    require(
        private_evidence["diary_id"]
        not in [r["diary_id"] for r in listed["body"]["items"]],
        "private draft never leaks through original Life reader list",
    )
    payload = dict(
        schema_version=1,
        actor_id=ACTOR,
        diary_id=diary_id,
        revision_id=diary["published_revision"],
        expected_diary_version=diary["version"],
    )
    actual = await call("revision", payload)
    require(
        actual["status"] == 200
        and actual["body"]["fictional"] is False
        and actual["body"]["provenance"]["kind"] == "digital",
        "real published diary projects source semantics",
    )
    require(
        PRIVATE_PROSE not in actual["body"]["content"],
        "public prose independent of private draft",
    )
    return dict(
        diary_id=diary_id,
        revision_request=payload,
        reader_http=True,
        private_draft_hidden=True,
        public_provenance=actual["body"]["provenance"],
    )


async def after_public_withdrawal(host, helper, state, evidence):
    reply = await helper.json_call(
        host,
        "platform",
        "companion",
        "/internal/v1/life-read/revision",
        state["al13_core_token"],
        evidence["revision_request"],
    )
    require(
        reply["status"] != 200,
        "withdrawn public original invalidates published reader projection",
    )
    return dict(reader_blocked=True, status=reply["status"])
