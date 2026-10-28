"""Bounded native user-memory read comparison, independent of zero-input checks.

The foreground is the original Companion -> Memory HTTP context query over an
actually admitted web message. This measures context retrieval, not chat first
text/completion latency. Product owners, clocks, slots and budgets are unchanged.
A declared external provider delay makes actual Life-loop overlap observable.
Only the fresh isolated fixture output/database is used; no production access.
"""

import asyncio
import copy
import hashlib
import json
import secrets
import statistics
import threading
import time
from pathlib import Path

import native_autonomous_check as native
from native_autonomous_fixtures import (
    ACTOR,
    MARKER,
    PREFERENCE,
    UNDERSTANDING,
    Sources,
    bootstrap,
    configure,
    provider_fixture,
    read,
    register_actor,
    require,
    write,
)

SAMPLES = 3
USER_MARKER = "AL_CORE_LOAD_USER 我不喜欢喝乌龙茶；这是隔离的原用户记忆，不是角色喜好。"
BACKGROUND_DELAY_SECONDS = 2.0


async def life_lane(host, enabled):
    async def change(app):
        core = app.state.core
        actor = core.life._get("actors", ACTOR)
        core.life.synchronize_role(
            ACTOR, enabled=enabled, personality_version=actor["personality_version"]
        )
        if not enabled:
            await core.life.drain()
        return dict(
            enabled=enabled, actor_version=core.life._get("actors", ACTOR)["version"]
        )

    return await host.exchange.invoke("companion", change)


async def backlog(host):
    def inspect(app):
        core = app.state.core
        rows = core.store.list("outbox")
        return dict(
            actor_pending=sum(
                row["state"]
                in {
                    "actor_memory_pending",
                    "actor_memory_unknown",
                    "actor_memory_submitting",
                }
                for row in rows
            ),
            user_pending=sum(
                row["state"] in {"pending", "unknown", "blocked_scope"} for row in rows
            ),
            active_life_jobs=len(core.life._jobs),
            user_turns=len(core.store.list("turns")),
        )

    return await host.exchange.invoke("companion", inspect)


async def admit_user(host, helper, state, schedule, timeout):
    from fixtures import bearer
    from source_fixtures import SOURCE_DOCS, register
    from services.platform.contracts import utc
    from deploy.single_container.applications import app_platform

    await helper.role_grant(host, state["bindings"], 0, True, "al-core-load:user-grant")
    data = copy.deepcopy(SOURCE_DOCS["self_private/request"]["input"])
    require(
        data["message_key"]["channel"]["namespace"] == "web",
        "ordinary original web input",
    )
    data["parts"] = [dict(kind="text", text=USER_MARKER)]
    data["sent_at"] = utc(time.time())

    async def dispatch(app):
        platform = app_platform(app)
        ingest = await platform.store.run_local(
            register, platform, time.time(), "self_private", [ACTOR], data
        )
        return await platform.sources.dispatch(bearer("CONNECTOR"), ingest)

    result = await host.exchange.invoke("platform", dispatch)
    outcome = result["outcomes"][0]
    require(
        outcome["admission"] and outcome["receipt"],
        "real web admission and original Companion receipt",
    )
    admission = outcome["admission"]

    async def delivered():
        def inspect(app):
            return any(
                row["state"] == "delivered" and row.get("receipt")
                for row in app.state.core.store.list("outbox")
                if row.get("conversation_id") == admission["scope"]["conversation_id"]
            )

        return await host.exchange.invoke("companion", inspect)

    await schedule.wait_for(
        delivered, "same-scope actual user material accepted by Memory", timeout
    )
    return admission


async def measure(host, helper, recorder, admission, name, provider, delays, timeout):
    recorder.stage("al-core-load:" + name)
    result = dict(mode=name, samples=[], backlog_before=await backlog(host))
    calls_before = len(provider.calls)
    for index in range(SAMPLES):
        correlation = secrets.token_hex(16)
        start, started_at = time.monotonic(), time.time()
        sample = dict(
            index=index, correlation_id=correlation, started_at=started_at, ok=False
        )
        try:
            async with asyncio.timeout(timeout):
                value = await helper.query(host, admission, correlation)
            evidence = value["evidence"]
            same = [
                row
                for row in evidence
                if row.get("kind") == "episode"
                and row.get("text") == USER_MARKER
                and row.get("owner_scope") == admission["scope"]
            ]
            require(
                bool(same), "actual original user source remains in its exact scope"
            )
            encoded = json.dumps(evidence, ensure_ascii=False)
            require(
                all(
                    marker not in encoded
                    for marker in (MARKER, PREFERENCE, UNDERSTANDING)
                ),
                "actor experiences and preferences never become user-memory evidence",
            )
            sample.update(
                ok=True,
                exact_scope=True,
                user_episode_count=len(same),
                evidence_count=len(evidence),
            )
        except Exception as error:
            sample["error"] = dict(
                type=type(error).__name__,
                code=getattr(error, "code", None),
                assertion=str(error) if isinstance(error, AssertionError) else None,
            )
        finally:
            sample.update(
                finished_at=time.time(),
                elapsed_ms=round((time.monotonic() - start) * 1000, 3),
            )
            sample["native_http"] = [
                row
                for row in recorder.snapshot()
                if row.get("correlation_id") == correlation
            ]
            sample["memory_http_seen"] = any(
                row["owner"] == "memory"
                and row["path"] == "/internal/v2/memory/context/query"
                and row.get("status") == 200
                for row in sample["native_http"]
            )
            sample["background_overlap"] = any(
                row["started_at"] <= sample["finished_at"]
                and row.get("finished_at", sample["finished_at"]) >= started_at
                for row in delays
            )
            result["samples"].append(sample)
    times = [row["elapsed_ms"] for row in result["samples"] if row["ok"]]
    result.update(
        successes=len(times),
        errors=len(result["samples"]) - len(times),
        minimum_ms=min(times) if times else None,
        median_ms=statistics.median(times) if times else None,
        maximum_ms=max(times) if times else None,
        provider_calls=len(provider.calls) - calls_before,
        backlog_after=await backlog(host),
    )
    return result


async def run(options):
    schedule = native.load(
        options.host_source
        / "tests/deployment/single_container/native_schedule_check.py",
        "al_core_load_schedule",
    )
    helper, smoke = schedule.load_helper(options)
    smoke.installed_wheels()
    sources = Sources()
    provider = provider_fixture(schedule, secrets.token_urlsafe(32), sources)
    started_background = asyncio.Event()
    delays = []
    original_serve = provider.serve

    async def delayed_external_provider(request):
        body = await request.json()
        system = "\n".join(
            str(row.get("content", ""))
            for row in body.get("messages", [])
            if row.get("role") == "system"
        )
        background = any(
            label in system
            for label in (
                "Choose one concrete next personal goal",
                "Continue this actor's independent activity",
            )
        )
        if not background:
            return await original_serve(request)
        # External fixture only. The real Life loop, Gateway admission and
        # foreground reserve remain responsible for concurrent progress.
        row = dict(
            started_at=time.time(), declared_delay_seconds=BACKGROUND_DELAY_SECONDS
        )
        delays.append(row)
        started_background.set()
        try:
            await asyncio.sleep(BACKGROUND_DELAY_SECONDS)
            return await original_serve(request)
        finally:
            row["finished_at"] = time.time()

    provider.serve = delayed_external_provider
    recorder, host, state = helper.Recorder(), None, None
    report = dict(
        ok=False,
        metric="foreground_user_memory_context_ms",
        samples_per_mode=SAMPLES,
        modes=[],
        limitations=[
            "Fixed order no-background / bounded-Life-load / paused; small descriptive samples, not a statistical performance guarantee.",
            "Measures real user-memory context retrieval and native source callbacks, not chat first-token/full-answer latency or model quality.",
            "Provider, web input and original article are explicit synthetic fixtures; no real QQ, paid model, external account or production data.",
            "PG lock waits and full asynchronous user-learning latency are not measured; HTTP headers_ms is not a database lock-wait metric.",
        ],
    )
    try:
        await sources.open()
        upstream = await provider.open()
        host_path, tokens, _, _ = smoke.prepare(options, upstream)
        tokens["upstream"] = provider.token
        config = read(host_path)
        config["modules"]["gateway"]["env"]["SYNTHETIC_UPSTREAM"] = provider.token
        write(host_path, config)
        state = configure(
            options, schedule, helper, smoke, host_path, tokens, upstream, sources
        )
        config = read(host_path)
        companion_path = Path(config["modules"]["companion"]["settings"])
        companion = read(companion_path)
        companion["automatic_memory_candidates"] = True
        write(companion_path, companion)
        schedule.seed_platform(host_path, upstream)
        await bootstrap(host_path, sources)
        report["installed_modules"] = native.installed_modules()
        from deploy.single_container.host import BusinessHost, load_settings

        host = BusinessHost(load_settings(host_path))
        recorder.attach(host)
        await host.open()
        report["owners"] = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in host.modules
        }
        require(
            len(set(report["owners"].values())) == 6, "six real installed native owners"
        )
        await register_actor(host)
        await life_lane(host, False)
        admission = await admit_user(host, helper, state, schedule, options.timeout)
        report["scope"] = admission["scope"]
        # One identical unmeasured warm-up ensures first-load cost is explicit.
        warmup = await helper.query(host, admission, secrets.token_hex(16))
        require(
            any(row.get("text") == USER_MARKER for row in warmup["evidence"]),
            "warm-up uses actual user evidence",
        )
        report["modes"].append(
            await measure(
                host,
                helper,
                recorder,
                admission,
                "no_background",
                provider,
                delays,
                options.timeout,
            )
        )
        started_background.clear()
        await life_lane(host, True)
        await native.make_routine_available(host)
        await asyncio.wait_for(started_background.wait(), options.timeout)
        report["modes"].append(
            await measure(
                host,
                helper,
                recorder,
                admission,
                "bounded_autonomy",
                provider,
                delays,
                options.timeout,
            )
        )
        await life_lane(host, False)
        report["modes"].append(
            await measure(
                host,
                helper,
                recorder,
                admission,
                "paused",
                provider,
                delays,
                options.timeout,
            )
        )
        require(
            all(
                row["ok"] and row["memory_http_seen"]
                for mode in report["modes"]
                for row in mode["samples"]
            ),
            "all foreground requests finish with real same-scope user Memory HTTP evidence",
        )
        require(
            any(row["background_overlap"] for row in report["modes"][1]["samples"]),
            "active mode actually overlaps a real scheduled Life provider request",
        )
        require(
            not any(row["background_overlap"] for row in report["modes"][2]["samples"]),
            "paused mode has drained existing Life work",
        )
        report["descriptive_median_ratios"] = {
            mode["mode"]: mode["median_ms"] / report["modes"][0]["median_ms"]
            for mode in report["modes"][1:]
        }
        report["ok"] = True
    except BaseException as error:
        report["failure"] = dict(
            type=type(error).__name__,
            code=getattr(error, "code", None),
            assertion=str(error) if isinstance(error, AssertionError) else None,
            stage=recorder.phase,
        )
        raise
    finally:
        provider.release.set()
        if host:
            report["closed"] = await host.close(timeout=30)
            report["owner_threads_remaining"] = [
                thread.name
                for thread in threading.enumerate()
                if thread.name.startswith("tianshu-")
            ]
            report["ok"] = (
                report["ok"]
                and report["closed"]
                and not report["owner_threads_remaining"]
            )
        await provider.close()
        await sources.close()
        if options.output.exists():
            report["source_sha256"] = hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest()
            report["background_provider_intervals"] = delays
            report["storage_bindings"] = schedule.storage_bindings(options.output)
            write(options.output / "native-al-core-load-validation.json", report)
            write(
                options.output / "native-al-core-load-requests.json",
                recorder.snapshot(),
            )
            write(options.output / "native-al-core-load-provider.json", provider.calls)
    require(report["ok"], "bounded native load comparison did not complete and close")


if __name__ == "__main__":
    asyncio.run(run(native.arguments(__doc__)))
