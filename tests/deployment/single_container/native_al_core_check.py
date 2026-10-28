"""Installed native core completion, extending the actual AL13 owner scenario.

Only external peers/provider and input data are synthetic. Management requests go
through Platform's original client; append/check/maintain use Companion's client,
all through Memory native HTTP. Browser session guards have separate component
coverage and are not claimed by this client-level joint scenario. Store access below
is read-only evidence inspection on each store's own owner thread, never a source
authority substitute. This does not claim real QQ, a paid model, or weeks of use.
"""

import asyncio
import hashlib
import json
import secrets
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import native_al13_check as diaries
from native_autonomous_fixtures import ACTOR, require, write

CONTEXT = {"purpose": "management", "audience": {"kind": "actor_only"}}


async def call(host, operation, **fields):
    if operation in {"manage_list", "interests", "memory_impact", "revise_memory", "forget_memory"}:
        from deploy.single_container.applications import app_platform
        from services.platform.server import create_console

        async def manage(app):
            platform = app_platform(app)
            # Explicit synthetic deployment wiring, not a substituted client.
            platform.role_runtime.config["memory"] = dict(
                base_url=host.config["modules"]["memory"]["origin"], token_env="NATIVE_CORE_MEMORY_PLATFORM"
            )
            request = dict(schema_version=1, request_id="core-native:" + uuid4().hex,
                           actor_id=ACTOR, role_version=platform.role_runtime.get(ACTOR)["version"],
                           operation=operation, **fields)
            return await create_console(platform).actor_memory.call(request)

        return await host.exchange.invoke("platform", manage)

    async def execute(app):
        owner = app.state.core.actor_memory
        request = dict(
            schema_version=1,
            request_id="core-native:" + uuid4().hex,
            actor_id=ACTOR,
            role_version=await owner.role_version(ACTOR),
            operation=operation,
            **fields,
        )
        return await owner._memory(request)

    return await host.exchange.invoke("companion", execute)


async def user_fingerprint(host):
    def inspect(app):
        store = app.state.memory.store
        with closing(store.readonly()) as db:
            if hasattr(db, "native"):
                tables = [row[0] for row in db.native(
                    "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename"
                ).fetchall()]
            else:
                tables = [row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )]
            require("actor_episodes" not in tables, "actor tables are not in the user logical source")
            rows = {}
            for table in tables:
                cursor = db.execute('SELECT * FROM "' + table.replace('"', '""') + '"')
                values = sorted(json.dumps(list(row), ensure_ascii=False, default=str) for row in cursor)
                raw = json.dumps([table, [column[0] for column in cursor.description], values], ensure_ascii=False).encode()
                rows[table] = hashlib.sha256(raw).hexdigest()
            return rows

    return await host.exchange.invoke("memory", inspect)


async def lifecycle_checks(host, helper, state):
    deadline = time.monotonic() + 30
    while True:
        pending = await host.exchange.invoke("companion", lambda app: [
            item["id"] for item in app.state.core.store.list("outbox")
            if item.get("actor_id") == ACTOR
            and item["state"] in {"actor_memory_pending", "actor_memory_submitting", "actor_memory_unknown"}
        ])
        if not pending:
            break
        require(time.monotonic() < deadline, "existing real actor outbox drains before CAS management checks")
        await asyncio.sleep(0.05)
    before = await user_fingerprint(host)
    current = await call(
        host, "manage_list", context=CONTEXT,
        states=["active"], kinds=["episode"], cursor=None, limit=32, budget_bytes=131072,
    )
    candidates = [
        item for item in current["items"]
        if item["kind"] == "episode" and item["text"]
        and any(source["producer"] == "companion" and source["object_id"].startswith("reading-action:")
                for source in item["source_refs"])
    ]
    require(bool(candidates), "actual original owner episodes remain readable after AL13 checks")
    basis = candidates[0]["episode_refs"]
    text = "CORE_NATIVE_INTEREST 我想继续了解真实数字经历里的茶香来源。"
    command = dict(
        idempotency_key="core-native-interest", kind="preference", text=text,
        episode_refs=basis, replaces=[], importance="normal", retention="long",
        interest=dict(topic="core-native-tea", stance="curious"),
    )
    created = await call(host, "append_derived", **command)
    interests = await call(host, "interests", context=CONTEXT, limit=32, budget_bytes=131072)
    topic = next(item for item in interests["items"] if item["topic"] == "core-native-tea")
    require(topic["stance"] == "curious" and topic["support_count"] >= 1,
            "owner interest projection uses actual independent original source")
    impact = await call(host, "memory_impact", context=CONTEXT, ref=created["ref"])
    changed = await call(
        host, "revise_memory", context=CONTEXT, idempotency_key="core-native-revise",
        ref=created["ref"], expected_domain_version=impact["domain_version"],
        text="CORE_NATIVE_INTEREST 我更喜欢循着实际资料了解茶香，未读部分还不能断言。",
        importance="high", retention="long", interest=dict(topic="core-native-tea", stance="like"),
    )
    interests = await call(host, "interests", context=CONTEXT, limit=32, budget_bytes=131072)
    revised = next(item for item in interests["items"] if item["topic"] == "core-native-tea")
    require(revised["ref"] == changed["ref"] and revised["stance"] == "like"
            and {item["stance"] for item in revised["history"]} == {"curious", "like"},
            "correction replaces exact opinion and preserves source-proven interest history")
    impact = await call(host, "memory_impact", context=CONTEXT, ref=changed["ref"])
    require(created["ref"]["id"] in {ref["id"] for ref in impact["dependent_refs"]}
            and impact["owner_originals_affected"] is False,
            "forget impact includes replacement history, not original owner content")
    forgotten = await call(
        host, "forget_memory", context=CONTEXT, idempotency_key="core-native-forget",
        ref=changed["ref"], expected_domain_version=impact["domain_version"],
    )
    check = await call(host, "check_memory", context=CONTEXT, memories=[changed["ref"]])
    require(not check["items"][0]["read_allowed"], "forgotten exact opinion is unavailable")
    try:
        await call(host, "append_derived", **dict(command, idempotency_key="core-native-late",
                                               text="CORE_NATIVE_LATE 同一旧依据换措辞，不是新认识。"))
    except Exception as error:
        require(getattr(error, "code", None) == "source_withdrawn", "late rewording has explicit suppression")
    else:
        raise AssertionError("forgotten opinion must not be resurrected from the old basis")

    def inspect_erasure(app):
        store = app.state.actor_memory.get().store
        from tianshu_memory.actor_memory.store import checkpoint

        with store.transaction() as db:
            result = checkpoint(db)
            require(result["schema"] == 3, "actual installed actor schema3")
            for ref in [created["ref"], changed["ref"]]:
                row = db.execute("SELECT payload FROM actor_derived WHERE id=? AND actor_id=?", (ref["id"], ACTOR)).fetchone()
                require(row is not None and row[0] == "{}", "current and replaced opinion bodies erased")
                require(not db.execute("SELECT 1 FROM actor_search_terms WHERE kind='derived' AND memory_id=?", (ref["id"],)).fetchone(),
                        "forgotten opinion is absent from the rebuildable term index")
            return dict(schema=result["schema"], body_and_history_erased=True, index_erased=True)

    erased = await host.exchange.invoke("memory", inspect_erasure)
    page = await call(host, "manage_list", context=CONTEXT, states=["active"],
                      kinds=["episode", "preference", "reflection", "understanding"],
                      cursor=None, limit=1, budget_bytes=131072)
    maintained = await call(host, "maintain", idempotency_key="core-native-idle", limit=32)
    require(maintained["expired_count"] == maintained["compressed_count"] == 0
            and maintained["domain_version"] == page["domain_version"],
            "idle bounded maintenance does not invalidate content cursors")
    if page["next_cursor"]:
        await call(host, "manage_list", context=CONTEXT, states=["active"],
                   kinds=["episode", "preference", "reflection", "understanding"],
                   cursor=page["next_cursor"], limit=1, budget_bytes=131072)
    after = await user_fingerprint(host)
    require(before == after, "all original user logical-source table fingerprints remain unchanged")
    return dict(ok=True, schema=erased["schema"], actual_interest_history=True,
                forget_receipt=forgotten, erasure=erased, late_old_basis_blocked=True,
                idle_cursor_preserved=True, user_tables_unchanged=len(before),
                user_fingerprints=after)


async def run(options):
    report = dict(ok=False, limitations=[
        "Original actual native HTTP owners, explicitly synthetic provider and peers.",
        "Current logical actor copies erased; user originals/backups/WAL are not claimed deleted.",
        "No real QQ, paid-model quality, external account or multi-week continuous-use acceptance.",
    ])

    async def verify(host, helper, state):
        report["lifecycle"] = await lifecycle_checks(host, helper, state)
        report["ok"] = True
        return report

    original_configure = diaries.base.configure

    def configure(*args, **kwargs):
        state = original_configure(*args, **kwargs)
        host_path = args[4]
        config = diaries.read(host_path)
        token = secrets.token_urlsafe(32)
        config["modules"]["platform"]["env"]["NATIVE_CORE_MEMORY_PLATFORM"] = token
        memory_path = config["modules"]["memory"]["settings"]
        memory = diaries.read(memory_path)
        memory["callers"]["platform-core-management"] = dict(
            token=token, allowed_actors=[ACTOR],
            actor_memory_operations=["manage_list", "interests", "get_episode", "memory_impact", "revise_memory", "forget_memory"],
        )
        write(memory_path, memory)
        write(host_path, config)
        return state

    diaries.base.configure = configure
    try:
        # The following original AL13 checks deliberately withdraw the reading
        # owner. Exercise lifecycle first while that real basis remains valid.
        await diaries.run(options, before_knowledge=verify)
    except BaseException as error:
        report["ok"] = False
        report["failure"] = dict(type=type(error).__name__, code=getattr(error, "code", None),
                                 assertion=str(error) if isinstance(error, AssertionError) else None)
        raise
    finally:
        diaries.base.configure = original_configure
        report["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if options.output.is_dir():
            write(options.output / "native-al-core-validation.json", report)


if __name__ == "__main__":
    asyncio.run(run(diaries.base.arguments(__doc__)))
