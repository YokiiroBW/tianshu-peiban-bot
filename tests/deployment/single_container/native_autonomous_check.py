"""Opt-in installed-wheel acceptance of autonomous goals and actual reading.

Run in the frozen single-container validation environment, without production
mounts. The six applications retain their real BusinessHost owners and ports.
Only an external provider, search directory and original article are loopback
fixtures. No user message, real model, QQ event or external account is used.

SQLite is the default. --postgres-url requires a disposable database reached
through an explicit Unix socket. Generated state and failure evidence are kept;
the tool never drops databases or schemas. Product source overlays are refused.
"""

import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import native_autonomous_fixtures as autonomy_fixtures
from native_autonomous_fixtures import (
    ACTOR,
    MARKER,
    PREFERENCE,
    UNDERSTANDING,
    Sources,
    bootstrap,
    configure,
    media_checks,
    provider_fixture,
    read,
    register_actor,
    require,
    write,
)

sys.dont_write_bytecode = True


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-source", type=Path, default=Path("/opt/tianshu/application")
    )
    parser.add_argument(
        "--platform-fixtures",
        type=Path,
        default=Path("/validation/platform/tests/backend"),
    )
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path("/verification/contracts/text-dialogue/v1"),
    )
    parser.add_argument(
        "--memory-contracts",
        type=Path,
        default=Path("/verification/memory-fixture/contracts/text-dialogue/v1"),
    )
    parser.add_argument(
        "--diagnostics-contracts",
        type=Path,
        default=Path("/verification/contracts/diagnostics/v1"),
    )
    parser.add_argument(
        "--memory-fixture", type=Path, default=Path("/verification/memory-fixture")
    )
    parser.add_argument(
        "--postgres-url",
        help="Disposable PG database, localhost plus explicit Unix socket",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New isolated retained directory"
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="Bounded per-stage deadline, seconds"
    )
    options = parser.parse_args()
    options.output = options.output.resolve()
    options.host_source = options.host_source.resolve()
    options.memory_helper = (
        options.host_source / "tests/deployment/single_container/native_memory_check.py"
    )
    options.web_access = options.http_web_access = False
    require(not options.output.exists(), "output must be a new isolated directory")
    require(30 <= options.timeout <= 240, "bounded assertion deadline")
    if options.postgres_url:
        parsed = urlsplit(options.postgres_url)
        sockets = parse_qs(parsed.query).get("host", [])
        require(
            parsed.scheme in {"postgresql", "postgres"}
            and parsed.hostname == "localhost",
            "PG requires localhost",
        )
        require(
            len(sockets) == 1
            and sockets[0].startswith("/")
            and Path(sockets[0]).is_dir(),
            "PG requires existing explicit Unix socket",
        )
        require(
            parsed.path not in {"", "/", "/postgres", "/template0", "/template1"},
            "PG requires dedicated disposable database",
        )
    for key in (
        "TIANSHU_POSTGRES_URL",
        "TIANSHU_REDIS_URL",
        "TIANSHU_STORAGE_NAMESPACE",
    ):
        require(
            not os.environ.get(key),
            "use explicit synthetic configuration, not inherited " + key,
        )
    return options


def installed_modules():
    """Check every loaded product Python module, including fixture imports."""
    packages = (
        "services.platform",
        "tianshu_companion",
        "tianshu_memory",
        "tianshu_gateway",
        "tianshu_nonebot",
        "tianshu_plugin",
        "tianshu_storage",
    )
    for name in (
        "services.platform.service",
        "tianshu_companion.app",
        "tianshu_memory.app",
        "tianshu_gateway.server",
        "tianshu_nonebot.onebot",
        "tianshu_plugin",
        "tianshu_storage",
    ):
        importlib.import_module(name)
    locations = {}
    prefix = Path(sys.prefix).resolve()
    for name, module in tuple(sys.modules.items()):
        if not any(
            name == package or name.startswith(package + ".") for package in packages
        ):
            continue
        location = getattr(module, "__file__", None)
        if location:
            location = Path(location).resolve()
            require(
                location.is_relative_to(prefix),
                "installed-wheel import required: " + name,
            )
            locations[name] = str(location)
    return locations


async def snapshot(host):
    def take(app):
        core = app.state.core
        return {
            "backend": core.store.backend,
            "turns": core.store.list("turns"),
            "actors": core.store.list("life_actors"),
            "activities": core.store.list("life_activities"),
            "concerns": core.store.list("life_concerns"),
            "reading": core.store.list("life_reading"),
            "outbox": core.store.list("outbox"),
            "metadata": core.store.list("metadata", "life:autonomy"),
        }

    return await host.exchange.invoke("companion", take)


async def make_routine_available(host):
    def configure_routine(app):
        core = app.state.core
        actor = core.store.get("life_actors", ACTOR)
        require(actor is not None, "real recover installed actor life state")
        core.life.configure_actor(
            ACTOR,
            actor["room_id"],
            schedule=[{"minute": 0, "activity": "阅读与学习"}],
            personality_version=actor["personality_version"],
            expected=actor["version"],
        )
        core.life.tick(force=True)

    await host.exchange.invoke("companion", configure_routine)


async def run(options):
    schedule = load(
        options.host_source
        / "tests/deployment/single_container/native_schedule_check.py",
        "autonomous_schedule_helper",
    )
    helper, smoke = schedule.load_helper(options)
    smoke.installed_wheels()
    sources = Sources()
    provider = provider_fixture(schedule, secrets.token_urlsafe(32), sources)
    recorder, host, original = helper.Recorder(), None, None
    summary = {
        "ok": False,
        "checks": {},
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_source_sha256": hashlib.sha256(
            Path(autonomy_fixtures.__file__).read_bytes()
        ).hexdigest(),
        "limitations": [
            "Synthetic confirmed role registrations reuse the existing Platform test helper; real management UI and peer registration workflows are not claimed.",
            "The installed six applications exchange genuine bounded native HTTP bytes; no product client, owner app, clock or product module is replaced.",
            "Only a loopback recorded model and loopback search/article are external fixtures; production state, QQ, paid models and external account writes are not accessed.",
            "The synthetic role has a reading routine for the entire test day; production sleep/manual/calendar policy is unchanged.",
            "The autonomous article is read for 64 characters, then episode revisions ground two interpretations. Reading and interpretation never certify unseen original content.",
            "The separate Knowledge upload/decoder checks do not create Companion actor experiences or claim autonomous multimedia selection.",
            "The original ordinary user-memory extraction/segmentation/recall acceptance is reused separately through native_schedule_check.py, not repeated here.",
            "This tool does not claim AL12 silent observation, abrupt-kill recovery, cross-day sustained operation, actual Bilibili/Xiaoheihe accounts or a production deployment.",
        ],
    }
    state = None
    try:
        await sources.open()
        upstream = await provider.open()
        host_path, tokens, _, original = smoke.prepare(options, upstream)
        tokens["upstream"] = provider.token
        config = read(host_path)
        config["modules"]["gateway"]["env"]["SYNTHETIC_UPSTREAM"] = provider.token
        write(host_path, config)
        state = configure(
            options, schedule, helper, smoke, host_path, tokens, upstream, sources
        )
        schedule.seed_platform(host_path, upstream)
        await bootstrap(host_path, sources)
        summary["installed_modules"] = installed_modules()
        from deploy.single_container.host import BusinessHost, load_settings

        host = BusinessHost(load_settings(host_path))
        recorder.attach(host)
        recorder.stage("native-host-open")
        await host.open()
        owners = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in host.modules
        }
        require(
            len(owners) == 6
            and len(set(owners.values())) == 6
            and threading.get_ident() not in owners.values(),
            "six genuine owner threads",
        )
        summary["owners"] = owners
        summary["checks"]["synthetic_role_registration"] = await register_actor(host)
        await make_routine_available(host)
        recorder.stage("zero-input-autonomous-loop")

        async def closed_loop():
            failures = [
                row for row in provider.calls if row["label"] == "provider-failure"
            ]
            require(
                not failures,
                "recorded provider failed: "
                + str(failures[-1]["failure"] if failures else None),
            )
            current = await snapshot(host)
            require(
                not current["turns"],
                "autonomous loop must never manufacture a user turn",
            )
            completed = [
                row
                for row in current["activities"]
                if row["title"] == MARKER and row["state"] == "completed"
            ]
            next_goals = [
                row
                for row in current["activities"]
                if row["title"].startswith("NATIVE_NEXT_GOAL")
            ]
            return current if completed and next_goals else None

        current = await schedule.wait_for(
            closed_loop,
            "actual reading, independent Memory and next autonomous goal",
            options.timeout,
        )
        summary["core_snapshot"] = current
        require(
            current["backend"] == ("postgresql" if options.postgres_url else "sqlite"),
            "actual Companion backend matches request",
        )
        completed = next(
            row
            for row in current["activities"]
            if row["title"] == MARKER and row["state"] == "completed"
        )
        concern = next(
            row
            for row in current["concerns"]
            if row["id"] == completed["autonomous_goal_id"]
        )
        require(
            concern["state"] == "completed", "goal completion follows actual activity"
        )
        delivered = [
            row for row in current["outbox"] if row["state"] == "actor_memory_delivered"
        ]
        require(
            len(delivered) == 1 and delivered[0].get("receipt"),
            "one actual independently delivered reading episode",
        )
        next_calls = [
            row for row in provider.calls if row["label"] == "autonomous-next-goal"
        ]
        require(
            next_calls
            and UNDERSTANDING
            in json.dumps(next_calls[0]["messages"], ensure_ascii=False)
            and PREFERENCE in json.dumps(next_calls[0]["messages"], ensure_ascii=False),
            "actual successor model input uses both persisted grounded interpretations",
        )

        async def recall(app):
            return await app.state.core.actor_memory.query(
                ACTOR,
                {"purpose": "reflection", "audience": {"kind": "actor_only"}},
                "乌龙茶",
                limit=8,
                budget_bytes=16384,
            )

        material = await host.exchange.invoke("companion", recall)
        require(
            {"episode", "understanding", "preference"}
            <= {row["kind"] for row in material["items"]},
            "real Memory query returns episode and both derived kinds",
        )
        summary["checks"]["autonomous_loop"] = {
            "goal_id": concern["id"],
            "activity_id": completed["id"],
            "delivered_episode_count": len(delivered),
            "recalled_kinds": sorted({row["kind"] for row in material["items"]}),
            "successor_uses_understanding_and_preference": True,
            "user_turns": 0,
        }
        summary["actor_material"] = material
        recorder.stage("actual-knowledge-multimedia")
        summary["checks"]["multimedia"] = await media_checks(host, helper, state)
        rows = recorder.snapshot()
        for owner, path in (
            ("platform", "/internal/v1/actor-capabilities/operate"),
            ("platform", "/internal/v1/actor-self-memory/authority"),
            ("companion", "/internal/v1/actor-self-memory/source"),
            ("knowledge", "/internal/v1/knowledge/content/acquire"),
            ("knowledge", "/internal/v1/knowledge/content/read"),
            ("memory", "/internal/v1/actor-self-memory/operate"),
        ):
            require(
                any(
                    row["owner"] == owner
                    and row["path"] == path
                    and row.get("status") == 200
                    for row in rows
                ),
                "original native endpoint observed: " + owner + path,
            )
        require(
            not any(
                row["owner"] == "memory"
                and row["path"].startswith(
                    ("/internal/v1/memory/", "/internal/v2/memory/")
                )
                for row in rows
            ),
            "autonomy never calls user-memory material/learning/context routes",
        )
        require(
            all(row["thread"] == owners[row["owner"]] for row in rows),
            "native dispatch stays on actual owner thread",
        )
        require(
            any(row["path"] == "/search" for row in sources.calls)
            and any(row["path"] == "/article" for row in sources.calls),
            "actual discovery and original fetch both occurred",
        )
        summary["installed_modules_final"] = installed_modules()
        summary["checks"].update(
            actual_native_owner_dispatch=True,
            no_user_memory_route=True,
            no_user_message=True,
        )
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
                summary["failure_snapshot"] = await snapshot(host)
            except BaseException as snapshot_error:  # noqa: BLE001 - preserve the original acceptance failure
                summary["failure"]["snapshot_error_type"] = type(
                    snapshot_error
                ).__name__
        raise
    finally:
        recorder.stage("native-host-shutdown")
        provider.release.set()
        try:
            if host:
                summary["closed"] = await host.close(timeout=30)
                summary["owner_threads_remaining"] = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name.startswith("tianshu-")
                ]
                require(
                    summary["closed"] and not summary["owner_threads_remaining"],
                    "real native owners drain and terminate",
                )
        except BaseException as error:
            summary["ok"] = False
            summary["shutdown_error_type"] = type(error).__name__
            raise
        finally:
            await provider.close()
            await sources.close()
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
                if original is not None:
                    unchanged = all(
                        hashlib.sha256(
                            (options.memory_fixture / path).read_bytes()
                        ).hexdigest()
                        == digest
                        for path, digest in original.items()
                    )
                    summary["checks"]["source_fixture_databases_unchanged"] = unchanged
                    summary["ok"] = summary["ok"] and unchanged
                write(
                    options.output / "autonomous-native-requests.json",
                    recorder.snapshot(),
                )
                write(options.output / "autonomous-provider.json", provider.calls)
                write(options.output / "autonomous-sources.json", sources.calls)
                write(options.output / "native-autonomous-validation.json", summary)
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
    require(summary["ok"], "native autonomous acceptance did not close")


if __name__ == "__main__":
    asyncio.run(run(arguments()))
