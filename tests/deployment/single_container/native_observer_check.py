"""Opt-in AL12 acceptance using six installed native owners and synthetic group WS input.

No real QQ or model service is contacted. Native SDK capture, Platform policy and
source authority, Companion archive callback, Memory raw archive and the actor
ledger remain real. Reuse native_autonomous_check arguments and R14 helpers;
--postgres-url selects a disposable Unix-socket PG database, otherwise SQLite.
"""

import asyncio
import hashlib
import json
import secrets
import sys
import threading
from pathlib import Path

import native_autonomous_check as native
import native_observer_fixtures as fixtures
from native_autonomous_fixtures import ACTOR, read, register_actor, require, write

sys.dont_write_bytecode = True


async def run(options):
    schedule = native.load(
        options.host_source
        / "tests/deployment/single_container/native_schedule_check.py",
        "observer_schedule_helper",
    )
    helper, smoke = schedule.load_helper(options)
    smoke.installed_wheels()
    provider = fixtures.provider_fixture(schedule, secrets.token_urlsafe(32))
    peer = fixtures.peer_fixture(schedule)
    recorder, host, state, source_databases = helper.Recorder(), None, None, None
    summary = {
        "ok": False,
        "checks": {},
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(
            Path(fixtures.__file__).read_bytes()
        ).hexdigest(),
        "limitations": [
            "External provider and OneBot reverse-WebSocket peer are synthetic loopback protocols, not a paid model or real QQ account.",
            "Installed modules and six real native owners retain their original clients, bytes, routes, clocks, archives and actor receipts; no source overlay or hand-made append_episode is used.",
            "The actual original observation enrollment/policy management domain is called with synthetic operator authority; browser/Cookie workflows are not claimed.",
            "Observe-only captures a single real synthetic group input without admitting a dialogue turn. The original raw archive and QQ author registration may persist; logical user memory material/segments/facts/records must remain unchanged.",
            "The first sourced goal only registers a question for later exploration, through a real activity checkpoint. It does not claim reading new material or completing external research.",
            "A second independent goal explicitly declines observation inspiration; a consumed hint must not inject the old public topic into it.",
            "Cross-day continuous use, abrupt kill recovery, policy A-to-B-to-A cycles, real account sends, external account actions and production deployment are outside this isolated tool.",
        ],
    }
    try:
        upstream = await provider.open()
        host_path, tokens, _, source_databases = smoke.prepare(options, upstream)
        tokens["upstream"] = provider.token
        config = read(host_path)
        config["modules"]["gateway"]["env"]["SYNTHETIC_UPSTREAM"] = provider.token
        write(host_path, config)
        state = fixtures.configure(
            options, schedule, helper, smoke, host_path, tokens, upstream
        )
        schedule.seed_platform(host_path, upstream)
        fixtures.initialize_actor(host_path)
        summary["installed_modules"] = native.installed_modules()
        from deploy.single_container.host import BusinessHost, load_settings

        host = BusinessHost(load_settings(host_path))
        recorder.attach(host)
        recorder.stage("observer-native-host-open")
        await host.open()
        owners = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in host.modules
        }
        require(
            len(owners) == 6
            and len(set(owners.values())) == 6
            and threading.get_ident() not in owners.values(),
            "six distinct actual native owners",
        )
        summary["owners"] = owners
        summary["checks"]["synthetic_role_registration"] = await register_actor(host)
        await native.make_routine_available(host)
        enrollment = await fixtures.enroll_observe_only(host, schedule, peer, state)
        summary["enrollment"] = enrollment
        baseline = await fixtures.user_memory_snapshot(host)
        summary["user_memory_before"] = baseline
        recorder.stage("observe-only-actual-sdk-input")
        await peer.observed_message()

        async def closed_loop():
            failures = [
                row for row in provider.calls if row["label"] == "provider-failure"
            ]
            require(
                not failures,
                "recorded observer provider failed: "
                + str(failures[-1]["failure"] if failures else None),
            )
            require(
                not any(row["action"] != "get_login_info" for row in peer.calls),
                "observe-only must never request a QQ send or other action",
            )
            current = await fixtures.snapshot(host)
            require(
                not current["turns"]
                and not current["reading"]
                and current["automatic_memory_candidates"] is False,
                "observer is independent of explicitly disabled user extraction and creates no user turn or fake reading",
            )
            require(
                all(
                    row["state"] == "material_pending" and row["attempts"] == 0
                    for row in current["user_learning"]
                ),
                "original passive archive handoff can be recorded but user extraction must not process it",
            )
            completed = [row for row in current["jobs"] if row["state"] == "completed"]
            first = [
                row
                for row in current["activities"]
                if row["title"] == fixtures.FIRST_GOAL and row["state"] == "paused"
            ]
            second = [
                row
                for row in current["activities"]
                if row["title"] == fixtures.SECOND_GOAL
            ]
            return current if completed and first and second else None

        recorder.stage("archive-source-observer-episode-derived-goal")
        current = await schedule.wait_for(
            closed_loop,
            "actual archive, observer reflection and consumed-hint successor goals",
            options.timeout,
        )
        summary["core_snapshot"] = current
        require(
            current["backend"] == ("postgresql" if options.postgres_url else "sqlite"),
            "actual requested Companion backend",
        )
        jobs = [row for row in current["jobs"] if row["state"] == "completed"]
        require(
            len(jobs) == 1
            and jobs[0]["processing_version"] == enrollment["policy_version"],
            "one original observer job preserves exact group generation",
        )
        job = jobs[0]
        require(
            job["decision"]["topic"] == fixtures.TOPIC
            and job["model_receipt"]["outcome"] == "succeeded",
            "semantic retain result has actual Gateway receipt",
        )
        delivered = [
            row for row in current["outbox"] if row["state"] == "actor_memory_delivered"
        ]
        require(
            len(delivered) == 1
            and delivered[0].get("perspective") == "observer"
            and delivered[0]["source"]["producer"] == "observation",
            "only real observer source is delivered to independent actor ledger",
        )
        original = job["originals"][0]
        request = original["request"]
        require(
            delivered[0]["source"] == original["source"]
            and original["source"]
            == {
                "producer": "observation",
                "object_id": request["source_ref"],
                "revision": str(request["scope_version"])
                + ":"
                + str(request["archive_epoch"]),
                "content_hash": request["source_digest"],
            },
            "source descriptor remains bound to actual accepted archive revision/digest",
        )
        require(
            request["event"]["text"] == fixtures.PRIVATE
            and request["event"]["mentioned"] is False
            and request["event"]["conversation_id"] == "group:" + fixtures.GROUP,
            "original actual unmentioned group observation is retained",
        )

        async def recall(app):
            return await app.state.core.actor_memory.query(
                ACTOR,
                {"purpose": "reflection", "audience": {"kind": "actor_only"}},
                "乌龙茶 焙火 清香",
                kinds=["episode", "reflection", "preference"],
                limit=8,
                budget_bytes=16384,
            )

        material = await host.exchange.invoke("companion", recall)
        require(
            {"episode", "reflection"} <= {row["kind"] for row in material["items"]},
            "actual independent Memory recalls observer episode and reflection",
        )
        require(
            not any(row["kind"] == "preference" for row in material["items"]),
            "a passing group suggestion does not become a permanent preference",
        )
        facts = [basis["fact"] for row in material["items"] for basis in row["basis"]]
        require(
            facts
            and all(
                fact["relation"] == "observer"
                and fact["provenance"] == "observation"
                and fact["outcome"] == "observed"
                and not fact["content_reads"]
                and fact["source_constraints"]["kind"] == "group"
                and fact["source"] == original["source"]
                for fact in facts
            ),
            "Memory owner proves observation, not participation or actual reading",
        )
        first = next(
            row for row in current["activities"] if row["title"] == fixtures.FIRST_GOAL
        )
        second = next(
            row for row in current["activities"] if row["title"] == fixtures.SECOND_GOAL
        )
        actor = next(row for row in current["actors"] if row["id"] == ACTOR)
        require(
            first.get("actor_only")
            and first.get("public_exploration_topic") == fixtures.TOPIC
            and first["autonomy_basis"]["items"],
            "first actual goal retains sourced private basis and abstract public topic",
        )
        require(
            not actor.get("autonomy_observation_hint")
            and "public_exploration_topic" not in second,
            "consumed observation hint cannot cover the independent successor goal",
        )
        inspired = [
            row for row in provider.calls if row["label"] == "observer-inspired-goal"
        ]
        independent = [
            row for row in provider.calls if row["label"] == "independent-goal"
        ]
        require(
            len(inspired) == 1
            and independent
            and fixtures.REFLECTION
            in json.dumps(inspired[0]["messages"], ensure_ascii=False),
            "actual first successor model input includes saved reflection exactly once",
        )
        require(
            first["state"] == "paused"
            and first["checkpoint"]["step"] == 0
            and independent[0]["started"] - inspired[0]["started"] >= 60,
            "unexecuted first goal stays paused until the genuine 60-second review timer",
        )

        from contextlib import closing

        def archived(app):
            core = app.state.core
            with closing(core.observations._db()) as db:
                return [
                    dict(row)
                    for row in db.execute("SELECT source_ref,archive_state FROM inbox")
                ]

        archive_rows = await host.exchange.invoke("companion", archived)
        require(
            archive_rows
            == [{"source_ref": request["source_ref"], "archive_state": "archived"}],
            "original Companion durable archive handoff is acknowledged",
        )
        require(
            len(current["user_learning"]) == 1
            and current["user_learning"][0]["source_ref"] == request["source_ref"]
            and current["user_learning"][0]["state"] == "material_pending"
            and current["user_learning"][0]["attempts"] == 0,
            "the unchanged original user archive callback retains only its unprocessed receipt marker",
        )
        policy = await fixtures.platform_policy(host, enrollment)
        require(
            policy["rule"]["mode"] == "observe"
            and policy["rule"]["actor_id"] == ACTOR
            and policy["generation"] == enrollment["policy_version"]
            and not any(policy["counts"].values()),
            "observe-only policy and generation remain unchanged without reply scopes",
        )
        after = await fixtures.user_memory_snapshot(host)
        require(
            after == baseline, "original user memory tables and rows remain unchanged"
        )
        summary["user_memory_after"] = after
        summary["actor_material"] = material
        summary["policy"] = policy
        summary["checks"]["observer_chain"] = {
            "source_ref": request["source_ref"],
            "policy_generation": enrollment["policy_version"],
            "observer_episode": delivered[0]["receipt"],
            "derived_receipt": job["derived_receipt"],
            "first_goal": first["id"],
            "independent_goal": second["id"],
            "raw_archive": archive_rows,
            "user_turns": 0,
            "user_memory_unchanged": True,
            "hint_consumed_once": True,
            "independent_goal_wait_seconds": independent[0]["started"]
            - inspired[0]["started"],
            "qq_sends": 0,
            "user_extraction_enabled": False,
            "original_archive_handoff_marker": current["user_learning"][0],
        }
        rows = recorder.snapshot()
        for owner, path in (
            ("companion", "/internal/v2/observations/ingest"),
            ("memory", "/internal/v2/memory/observations"),
            ("memory", "/internal/v1/actor-self-memory/observation-source"),
            ("memory", "/internal/v1/actor-self-memory/operate"),
            ("platform", "/internal/v2/observation-source/verify"),
            ("platform", "/internal/v2/observation-source/processing"),
            ("companion", "/internal/v1/memory/consolidation/window/check"),
        ):
            require(
                any(
                    row["owner"] == owner
                    and row["path"] == path
                    and row.get("status") == 200
                    for row in rows
                ),
                "original native observer route: " + owner + path,
            )
        require(
            not any(
                row["owner"] == "memory"
                and row["path"].startswith(
                    ("/internal/v1/memory/candidates/", "/internal/v2/memory/context/")
                )
                for row in rows
            ),
            "observer never invokes user-material extraction or context APIs",
        )
        require(
            all(row["thread"] == owners[row["owner"]] for row in rows),
            "all actual dispatches stay on native owner threads",
        )
        summary["installed_modules_final"] = native.installed_modules()
        summary["ok"] = True
    except BaseException as error:
        summary["failure"] = {
            "type": type(error).__name__,
            "code": getattr(error, "code", None),
            "stage": recorder.phase,
        }
        if isinstance(error, AssertionError):
            summary["failure"]["assertion"] = str(error)
        if host and host.started:
            try:
                summary["failure_snapshot"] = await fixtures.snapshot(host)
            except BaseException as snapshot_error:  # noqa: BLE001 - retain the original failure
                summary["failure"]["snapshot_error_type"] = type(
                    snapshot_error
                ).__name__
        raise
    finally:
        recorder.stage("observer-native-host-shutdown")
        provider.release.set()
        try:
            await peer.close()
            if host:
                summary["closed"] = await host.close(timeout=30)
                summary["owner_threads_remaining"] = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name.startswith("tianshu-")
                ]
                require(
                    summary["closed"] and not summary["owner_threads_remaining"],
                    "actual native owners drain and stop",
                )
        except BaseException as error:
            summary["ok"] = False
            summary["shutdown_error_type"] = type(error).__name__
            raise
        finally:
            await provider.close()
            if options.output.is_dir():
                summary["storage_bindings"] = schedule.storage_bindings(options.output)
                if options.postgres_url and state is not None:
                    bindings = summary["storage_bindings"]
                    active = (
                        bool(bindings)
                        and {row["service"] for row in bindings}
                        >= {
                            "platform",
                            "companion",
                            "memory",
                            "gateway",
                            "actor-memory",
                        }
                        and all(
                            row["backend"] == "postgresql"
                            and row["state"] == "active"
                            and row["namespace"].startswith(state["namespace"])
                            for row in bindings
                        )
                    )
                    summary["checks"]["all_pg_bindings_active"] = active
                    summary["ok"] = summary["ok"] and active
                if source_databases is not None:
                    unchanged = all(
                        hashlib.sha256(
                            (options.memory_fixture / relative).read_bytes()
                        ).hexdigest()
                        == digest
                        for relative, digest in source_databases.items()
                    )
                    summary["checks"]["source_fixture_databases_unchanged"] = unchanged
                    summary["ok"] = summary["ok"] and unchanged
                write(
                    options.output / "observer-native-requests.json",
                    recorder.snapshot(),
                )
                write(options.output / "observer-provider.json", provider.calls)
                write(options.output / "observer-onebot.json", peer.calls)
                write(options.output / "native-observer-validation.json", summary)
            print(
                json.dumps(
                    {
                        "ok": summary["ok"],
                        "checks": summary["checks"],
                        "failure": summary.get("failure"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
    require(summary["ok"], "native observer acceptance did not close")


if __name__ == "__main__":
    asyncio.run(run(native.arguments(__doc__, default_timeout=240)))
