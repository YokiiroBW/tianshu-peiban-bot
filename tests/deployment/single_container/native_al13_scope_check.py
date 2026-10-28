"""Installed six-owner AL13 original-group sharing and ordinary web-memory acceptance.

The external OneBot peer and model are explicit loopback fixtures. Product owners,
clients, source authority, observation archive, actor/user Memory and Proactive
remain real. This separate entry permits genuine synthetic user turns; it never
changes the zero-turn assertions of native_al13_check.
"""

import asyncio
import copy
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import native_autonomous_check as native
import native_observer_fixtures as observer
from native_autonomous_fixtures import ACTOR, read, register_actor, require, write
from native_al13_diaries import extend_provider

ACTOR_PREFERENCE = (
    "AL13_ACTOR_TEA_PREFERENCE 我喜欢了解乌龙茶焙火与香气；群讨论只是启发，不是亲自品尝的经历。"
)

WEB_MARKER = "AL13_USER_DISLIKES_TEA 我不喜欢喝茶。这是隔离用户记忆回流验证。"


async def enable_group_reply(host, enrollment):
    from deploy.single_container.applications import app_platform
    from services.platform import group_policy

    async def apply(app):
        owner = app_platform(app).bot_observation
        row = owner.get(enrollment["id"])
        updated = await owner.policy(
            dict(
                id=row["id"],
                expected_revision=row["revision"],
                group_policy=dict(version=2, default=group_policy.rule("reply", ACTOR), groups={}),
                private_policy=dict(observe=False, mode="observe_only", list=[], actor_id=None),
                client_id="al13-group-policy:" + uuid4().hex,
            )
        )
        require(
            updated["state"] == "ready" and updated["pending"] is None,
            "actual reply/observe group policy applied",
        )
        return group_policy.revision(updated, "group:" + observer.GROUP)

    return await host.exchange.invoke("platform", apply)


async def group_admission(host, peer, schedule, timeout):
    await peer.peer.send(
        json.dumps(
            dict(
                time=int(time.time()),
                self_id=10001,
                post_type="message",
                message_type="group",
                sub_type="normal",
                group_id=int(observer.GROUP),
                message_id=991002,
                user_id=int(observer.AUTHOR),
                anonymous=None,
                message=[
                    dict(type="at", data=dict(qq="10001")),
                    dict(
                        type="text",
                        data=dict(text=observer.PRIVATE + " AL13_GROUP_REQUEST 请在原群回应。"),
                    ),
                ],
                raw_message="[CQ:at,qq=10001] " + observer.PRIVATE,
                font=0,
                sender=dict(
                    user_id=int(observer.AUTHOR), nickname="Synthetic observer author", card=""
                ),
            ),
            ensure_ascii=False,
        )
    )

    async def current():
        def inspect(app):
            core = app.state.core
            turns = [
                row
                for row in core.store.list("turns")
                if row["scope"]["actor_id"] == ACTOR and row["scope"]["audience"] == "group"
            ]
            if not turns:
                return None
            turn = turns[-1]
            return dict(
                scope=turn["scope"],
                origin=turn["origin"],
                channel=turn["bundle"]["collection_key"]["channel"],
                turn_id=turn["id"],
            )

        return await host.exchange.invoke("companion", inspect)

    return await schedule.wait_for(
        current, "genuine mentioned group input establishes original scope", timeout
    )


async def web_memory(host, helper, state, schedule, timeout):
    from fixtures import bearer
    from source_fixtures import SOURCE_DOCS, register
    from services.platform.contracts import utc
    from deploy.single_container.applications import app_platform

    data = copy.deepcopy(SOURCE_DOCS["self_private/request"]["input"])
    require(data["message_key"]["channel"]["namespace"] == "web", "synthetic web input")
    data["parts"] = [dict(kind="text", text=WEB_MARKER)]
    data["sent_at"] = utc(time.time())

    async def dispatch(app):
        platform = app_platform(app)
        ingest = await platform.store.run_local(
            register, platform, time.time(), "self_private", [ACTOR], data
        )
        return await platform.sources.dispatch(bearer("CONNECTOR"), ingest)

    accepted = await host.exchange.invoke("platform", dispatch)
    outcome = accepted["outcomes"][0]
    require(
        outcome["admission"] and outcome["receipt"], "actual web admission and Companion receipt"
    )
    admission = outcome["admission"]

    async def learned():
        def inspect(app):
            return any(
                row["state"] == "delivered" and row.get("receipt")
                for row in app.state.core.store.list("outbox")
                if row.get("conversation_id") == admission["scope"]["conversation_id"]
            )

        return await host.exchange.invoke("companion", inspect)

    await schedule.wait_for(
        learned, "ordinary web message reaches original user Memory material receipt", timeout
    )
    result = await helper.query(host, admission, secrets.token_hex(16))
    require(
        any(
            row["kind"] == "episode"
            and row["text"] == WEB_MARKER
            and row["owner_scope"] == admission["scope"]
            for row in result["evidence"]
        ),
        "original user recall contains same-scope actual dislike statement",
    )
    return dict(
        scope=admission["scope"],
        actual_material_receipt=True,
        original_user_memory_recalled=True,
        marker_sha256=hashlib.sha256(WEB_MARKER.encode()).hexdigest(),
    )


async def scoped_diary(host, admission, schedule, timeout, provider):
    async def prepare(app):
        from tianshu_companion.diary_sharing import DiarySharing
        from tianshu_companion.life import zone

        core = app.state.core
        scope = admission["scope"]
        now = datetime.now(timezone.utc)
        quiet = (f"{(now.hour - 1) % 24:02}:00", f"{(now.hour + 1) % 24:02}:00")
        sub = core.proactive.register_subscription(
            **scope,
            channel=admission["channel"],
            consent=dict(
                registered_by="admin:synthetic-al13",
                basis="explicit_admin_registration",
                evidence_ref="al13-explicit-scope-test",
            ),
            timezone_name="UTC",
            quiet=quiet,
            cooldown_seconds=3600,
            daily_limit=2,
            unanswered_limit=2,
            expiry_seconds=86400,
        )
        sub = core.store.get("proactive_subscriptions", sub["id"])
        actor = core.store.get("life_actors", ACTOR)
        world = core.store.get("life_worlds", actor["world_id"])
        day = datetime.fromtimestamp(core.clock(), zone(world["timezone"])).date().isoformat()
        await DiarySharing(core.life.diary_sources).prepare(
            dict(conversation_id=ACTOR, day=day), sub
        )
        motives = [
            row
            for row in core.store.list("proactive_motives")
            if row["subscription_id"] == sub["id"]
        ]
        require(
            len(motives) == 1 and motives[0]["state"] == "paused",
            "neutral same-owner motive is paused before generation",
        )
        require(
            observer.PRIVATE not in motives[0]["summary"],
            "neutral context acquisition contains no private original",
        )
        diary = core.store.get("life_diaries", motives[0]["diary_id"])
        require(diary["state"] == "queued", "original scope has actual permitted diary materials")
        audience = diary["source_capture"]["context"]["audience"]
        require(
            audience["kind"] == "group" and audience["assertion_ref"],
            "original sender minted genuine group assertion",
        )
        entries = diary["source_capture"]["material"]
        require(
            any(
                observer.PRIVATE in row["summary"] and row["kind"] == "observation"
                for row in entries
            ),
            "actual original group observation selected",
        )
        return dict(
            diary_id=diary["id"],
            motive_id=motives[0]["id"],
            subscription_id=sub["id"],
            day=day,
            scope=scope,
            material_refs=[ref for entry in entries for ref in entry["episode_refs"]],
        )

    evidence = await host.exchange.invoke("companion", prepare)

    async def completed():
        async def advance(app):
            core = app.state.core
            await core.life._diary_work(ACTOR)
            diary = core.store.get("life_diaries", evidence["diary_id"])
            motive = core.store.get("proactive_motives", evidence["motive_id"])
            if motive["state"] != "active":
                require(
                    diary["state"] not in {"failed", "unavailable"},
                    "scoped diary generation: " + str(diary),
                )
                return None
            require(
                diary["published_revision"] is None, "scope-only prose never gets public pointer"
            )
            require(
                "AL13_SCOPE_PROSE" in motive["summary"],
                "original motive receives separately generated scope prose",
            )
            core.proactive.tick(force=True)
            candidates = [
                row
                for row in core.store.list("proactive_candidates")
                if row["subject_id"] == motive["id"] and row["subject_version"] == motive["version"]
            ]
            require(
                candidates and all(row["state"] == "deferred" for row in candidates),
                "existing quiet gate keeps current sourced candidate waiting",
            )
            await core.runtime_execution.validate_sources(candidates[0])
            require(
                not [
                    row
                    for row in core.store.list("proactive_attempts")
                    if row["candidate_id"] == candidates[0]["id"]
                ],
                "no send attempted through quiet window",
            )
            return dict(
                candidate_id=candidates[0]["id"],
                diary_version=diary["version"],
                revision_id=diary["current_revision"],
                motive_version=motive["version"],
            )

        return await host.exchange.invoke("companion", advance)

    evidence.update(
        await schedule.wait_for(
            completed, "original scope diary reaches quiet-gated candidate", timeout
        )
    )
    scope_calls = [row for row in provider.calls if row["label"] == "al13-scope-diary"]
    require(
        scope_calls
        and observer.PRIVATE in json.dumps(scope_calls[-1]["messages"], ensure_ascii=False),
        "actual Gateway scoped input contains permitted group original",
    )
    return evidence


async def actor_skill_recall(host, admission, user_evidence):
    """Use registry-selected real turn tools, never inject pins or fabricate an origin."""

    async def execute(app):
        from tianshu_companion.contracts import canonical
        from tianshu_companion.life import zone

        core = app.state.core
        material = await core.actor_memory.query(
            ACTOR,
            dict(purpose="reflection", audience=dict(kind="actor_only")),
            "乌龙茶 焙火",
            kinds=["episode"],
            limit=8,
            budget_bytes=16384,
        )
        episodes = [
            row
            for row in material["items"]
            if any(basis["fact"]["relation"] == "observer" for basis in row["basis"])
        ]
        require(episodes, "actual observer episode is available as interpretation basis")
        refs = episodes[0]["episode_refs"]

        async def validate():
            await core.actor_memory.check(material)

        preference = await core.actor_memory.append_validated_derived(
            ACTOR,
            "al13-native-scope-preference",
            "recorded-fixture-interpretation",
            dict(
                kind="preference",
                text=ACTOR_PREFERENCE,
                episode_refs=refs,
                importance="normal",
                retention="medium",
            ),
            {canonical(ref) for ref in refs},
            validate,
        )
        turn = core.store.get("turns", admission["turn_id"])
        require(
            turn is not None and turn["scope"] == admission["scope"],
            "real admitted group turn retained",
        )
        # This is the same public registry method used by ordinary Core preparation.
        # It records actual enabled handler ownership/revisions; no turn field is hand-written.
        tools = core.role_actions.tools(turn)
        require(
            any(tool["function"]["name"] == "actor_memory_recall" for tool in tools),
            "actual enabled ActorMemorySkill renders in real group turn",
        )
        core.skills.check_selected(core.store.get("turns", turn["id"]), "actor_memory_recall")
        actor = core.store.get("life_actors", ACTOR)
        world = core.store.get("life_worlds", actor["world_id"])
        day = datetime.fromtimestamp(core.clock(), zone(world["timezone"])).date().isoformat()

        read_attempts = []

        async def call(key, value):
            from tianshu_companion.contracts import Fault
            from tianshu_companion.preparation_retry import read_retry

            tool = dict(
                id=key,
                type="function",
                function=dict(name="actor_memory_recall", arguments=canonical(value)),
            )
            recovery = {}
            while True:
                try:
                    result, _ = await core.role_actions.execute(turn["id"], tool)
                    break
                except Fault as error:
                    delay = read_retry(
                        recovery, error, core.clock(), core._source_recovery_bound(turn)
                    )
                    read_attempts.append(
                        dict(
                            call_id=key,
                            code=error.code,
                            retryable_read=error.retryable_read,
                            unknown=error.unknown,
                            cause=error.cause,
                            delay=delay,
                        )
                    )
                    if delay is None:
                        raise
                    await asyncio.sleep(delay)
            require(
                result["state"] == "completed" and result["source"] == "actor_self_memory",
                "original ActorMemorySkill returns own evidence channel",
            )
            return result

        timeline = await call("al13-tool-day", dict(day=day, limit=16))
        require(
            timeline["mode"] == "timeline" and timeline["window"],
            "real ordinary tool executes new civil-day path",
        )
        require(
            any(observer.PRIVATE in row["text"] for row in timeline["items"]),
            "same-group observer is visible through actual day recall",
        )
        preferred = await call(
            "al13-tool-preference", dict(text="乌龙茶", kinds=["preference"], limit=8)
        )
        require(
            any(row["text"] == ACTOR_PREFERENCE for row in preferred["items"]),
            "sourced actor preference is recalled separately from user dislike",
        )
        require(
            WEB_MARKER not in canonical(preferred) and WEB_MARKER not in canonical(timeline),
            "user private dislike never becomes actor own episode/preference",
        )
        require(
            user_evidence["original_user_memory_recalled"]
            and user_evidence["scope"] != admission["scope"],
            "user evidence was retrieved from its actual independent private scope",
        )
        return dict(
            actor_preference_receipt=preference,
            actor_evidence=preferred,
            actor_timeline=timeline,
            user_evidence=user_evidence,
            real_turn_id=turn["id"],
            registry_selected=True,
            hand_written_turn=False,
            ordinary_tool_execution=True,
            final_model_wording_tested=False,
            bounded_read_recovery=read_attempts,
        )

    return await host.exchange.invoke("companion", execute)


async def withdraw_and_block(host, evidence):
    async def check(app):
        from tianshu_companion.contracts import Fault

        core = app.state.core
        version = await core.actor_memory.role_version(ACTOR)
        for ref in {
            json.dumps(ref, sort_keys=True): ref for ref in evidence["material_refs"]
        }.values():
            request = dict(
                schema_version=1,
                request_id="al13-scope-withdraw:" + uuid4().hex,
                actor_id=ACTOR,
                role_version=version,
                operation="withdraw_episode",
                idempotency_key="al13-withdraw:" + ref["episode_id"],
                reason="forgotten",
                **ref,
            )
            core.contracts.check("actor-self-memory#request", request)
            response = await core.memory.client.call(
                "/internal/v1/actor-self-memory/operate", request, uncertain_write=True
            )
            core.contracts.check("actor-self-memory#response", response)
        candidate = core.store.get("proactive_candidates", evidence["candidate_id"])
        before = core.store.list("proactive_attempts")
        for operation in (
            lambda: core.runtime_execution.validate_sources(candidate),
            lambda: core.life.admin_read_revision_verified(evidence["revision_id"]),
        ):
            try:
                await operation()
            except Fault:
                pass
            else:
                raise AssertionError("withdrawn original basis must block cached scoped prose")
        require(
            core.store.list("proactive_attempts") == before,
            "withdrawal does not mint a new attempt/key",
        )
        return dict(
            actual_memory_withdrawal=True,
            presend_source_blocked=True,
            cached_admin_read_blocked=True,
            no_new_attempt=True,
        )

    return await host.exchange.invoke("companion", check)


async def run(options):
    schedule = native.load(
        options.host_source / "tests/deployment/single_container/native_schedule_check.py",
        "al13_scope_schedule",
    )
    helper, smoke = schedule.load_helper(options)
    smoke.installed_wheels()
    provider = observer.provider_fixture(schedule, secrets.token_urlsafe(32))
    # Adapt only two fixture methods; no product owner/client is replaced.
    provider.recorded_response = provider.observer_response
    extend_provider(provider)
    provider.observer_response = provider.recorded_response
    peer = observer.peer_fixture(schedule)
    recorder, host, state = helper.Recorder(), None, None
    report = dict(
        ok=False,
        checks={},
        limitations=[
            "Synthetic external model and OneBot loopback peer, never real QQ or paid model.",
            "No source owner, Memory, Knowledge, Proactive or current-context client responses are mocked.",
            "The original quiet gate is tested at pending send; actual unknown-send recovery reuses unchanged existing native evidence, not rerun here.",
            "Real ActorMemorySkill execution and distinct user/actor evidence are checked; autonomous model tool choice and final natural-language preference wording are not evaluated.",
        ],
    )
    try:
        upstream = await provider.open()
        host_path, tokens, _, _ = smoke.prepare(options, upstream)
        tokens["upstream"] = provider.token
        config = read(host_path)
        config["modules"]["gateway"]["env"]["SYNTHETIC_UPSTREAM"] = provider.token
        write(host_path, config)
        state = observer.configure(options, schedule, helper, smoke, host_path, tokens, upstream)
        config = read(host_path)
        companion_path = config["modules"]["companion"]["settings"]
        companion = read(companion_path)
        companion["automatic_memory_candidates"] = True
        companion["life_writing"] = True
        write(companion_path, companion)
        schedule.seed_platform(host_path, upstream)
        observer.initialize_actor(host_path)
        report["installed_modules"] = native.installed_modules()
        from deploy.single_container.host import BusinessHost, load_settings

        host = BusinessHost(load_settings(host_path))
        recorder.attach(host)
        report["shutdown_phases"] = []
        original_invoke = host.invoke

        async def observed_invoke(name, operation, **kwargs):
            try:
                value = await original_invoke(name, operation, **kwargs)
            except BaseException as error:
                if operation in {
                    "stop_accepting",
                    "drain_ingress",
                    "quiesce",
                    "drain",
                    "close_public",
                }:
                    report["shutdown_phases"].append(
                        dict(owner=name, operation=operation, error_type=type(error).__name__)
                    )
                raise
            if operation in {"stop_accepting", "drain_ingress", "quiesce", "drain", "close_public"}:
                report["shutdown_phases"].append(
                    dict(owner=name, operation=operation, refused=value is False)
                )
            return value

        host.invoke = observed_invoke  # Observe host lifecycle only; never replace a product owner.
        await host.open()
        owners = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in host.modules
        }
        require(
            len(owners) == 6 and len(set(owners.values())) == 6, "six actual native owner threads"
        )
        report["owners"] = owners
        await register_actor(host)

        async def enable_skill(app):
            core = app.state.core
            return await core.skills.call(
                "platform",
                "manage",
                dict(
                    schema_version=1,
                    request_id="al13-enable-actor-memory",
                    actor_id=ACTOR,
                    expected_version=core.skills.actor(ACTOR)["version"],
                    operation="skill.enable",
                    value=dict(skill_id="actor-memory"),
                ),
            )

        report["checks"]["selected_skill_configuration"] = await host.exchange.invoke(
            "companion", enable_skill
        )
        await helper.role_grant(host, state["bindings"], 0, True, "al13-user-memory-role")
        await native.make_routine_available(host)
        enrollment = await observer.enroll_observe_only(host, schedule, peer, state)
        report["policy_version"] = await enable_group_reply(host, enrollment)
        recorder.stage("actual-group-observer-source")
        await peer.observed_message()

        async def observed():
            snapshot = await observer.snapshot(host)
            done = [row for row in snapshot["jobs"] if row["state"] == "completed"]
            return done[0] if done else None

        report["observer_job"] = await schedule.wait_for(
            observed, "real archived group source reaches actor Memory", options.timeout
        )
        recorder.stage("actual-original-group-admission")
        admission = await group_admission(host, peer, schedule, options.timeout)
        recorder.stage("ordinary-web-user-memory")
        report["checks"]["web_memory"] = await web_memory(
            host, helper, state, schedule, options.timeout
        )
        recorder.stage("original-group-turn-natural-settlement")

        async def settled_group():
            def inspect(app):
                from tianshu_companion.delivery import TERMINAL

                turn = app.state.core.store.get("turns", admission["turn_id"])
                return (
                    {key: turn.get(key) for key in ("id", "phase", "failure", "model_calls")}
                    if turn and turn["phase"] in TERMINAL
                    else None
                )

            return await host.exchange.invoke("companion", inspect)

        report["group_turn_settled"] = await schedule.wait_for(
            settled_group,
            "original group turn settles before additional read-only skill checks",
            options.timeout,
        )
        recorder.stage("original-actor-memory-skill-and-separate-user-evidence")
        report["checks"]["ordinary_actor_skill"] = await actor_skill_recall(
            host, admission, report["checks"]["web_memory"]
        )
        recorder.stage("scope-filtered-diary-and-neutral-motive")
        evidence = await scoped_diary(host, admission, schedule, options.timeout, provider)
        report["checks"]["scope_diary"] = evidence
        recorder.stage("source-withdrawal-before-send")
        report["checks"]["withdrawal"] = await withdraw_and_block(host, evidence)
        report["installed_modules_final"] = native.installed_modules()
        report["ok"] = True
    except BaseException as error:
        report["failure"] = dict(
            type=type(error).__name__,
            code=getattr(error, "code", None),
            cause=getattr(error, "cause", None),
            retryable_read=getattr(error, "retryable_read", None),
            stage=recorder.phase,
            assertion=str(error) if isinstance(error, AssertionError) else None,
        )
        raise
    finally:
        recorder.stage("scope-native-host-shutdown")
        provider.release.set()
        await peer.close()
        if host:
            report["closed"] = await host.close(timeout=30)
            report["owner_threads_remaining"] = [
                thread.name
                for thread in threading.enumerate()
                if thread.name.startswith("tianshu-")
            ]
            report["close_task_done"] = host.close_task.done() if host.close_task else None
            report["ok"] = (
                report["ok"] and report["closed"] and not report["owner_threads_remaining"]
            )
            if not report["closed"] or report["owner_threads_remaining"]:
                report["shutdown_failure"] = (
                    "Native owners did not close cleanly within original deadline"
                )
        await provider.close()
        if options.output.exists():
            report["storage_bindings"] = schedule.storage_bindings(options.output)
            if options.postgres_url and state:
                require(
                    all(
                        row["backend"] == "postgresql"
                        and row["state"] == "active"
                        and row["namespace"].startswith(state["namespace"])
                        for row in report["storage_bindings"]
                    ),
                    "separate PG namespace with active original stores",
                )
            report["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            write(options.output / "native-al13-scope-validation.json", report)
            write(options.output / "native-al13-scope-requests.json", recorder.snapshot())
            write(options.output / "native-al13-scope-provider.json", provider.calls)
        print(
            json.dumps(dict(ok=report["ok"], failure=report.get("failure")), ensure_ascii=False),
            flush=True,
        )
    require(report["ok"], "scope acceptance did not close")


if __name__ == "__main__":
    asyncio.run(run(native.arguments(__doc__)))
