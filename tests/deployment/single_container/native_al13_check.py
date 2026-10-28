"""AL13 installed-wheel six-owner extension to the existing native autonomy check.

Only fixtures are imported from /validation/tools; every product stays under
sys.prefix. Chronology rows are explicitly synthetic Companion owner metadata,
backed by a genuinely acquired/read original. Memory append/query/proofs use
actual native HTTP. Public acquisition uses an explicitly declared DNS/connect
seam forwarding one global test address to the loopback original server; this
is not an external-network acceptance. No production mounts or network needed.
"""

import asyncio
import copy
import hashlib
import json
import socket
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import native_autonomous_check as base
from native_autonomous_fixtures import ACTOR, Sources, require, read, write

CONTEXT = {"purpose": "diary", "audience": {"kind": "actor_only"}}
PUBLIC = {"purpose": "diary", "audience": {"kind": "public"}}


async def timeline_checks(host):
    from tianshu_companion.diary_materials import civil_window
    import time

    def role_zone(app):
        life = app.state.core.life
        actor = life._get("actors", ACTOR)
        return life._get("worlds", actor["world_id"])["timezone"]

    timezone_name = await host.exchange.invoke("companion", role_zone)
    window = civil_window("yesterday", timezone_name, time.time())
    start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
    second = (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    last = (
        datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
        - timedelta(seconds=1)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    stamps = [
        second + ".0000019Z",
        second + ".000003Z",
        last + ".9999999Z",
        window["end"],
    ]

    async def seed(app):
        from tianshu_companion.actor_memory import SOURCE_PREFIX
        from tianshu_companion.contracts import digest

        core = app.state.core
        rows = [
            r
            for r in core.store.list("metadata")
            if r["id"].startswith(SOURCE_PREFIX)
            and r.get("actor_id") == ACTOR
            and r.get("state") == "current"
            and not r["fact"]["content_reads"]
        ]
        require(bool(rows), "original actual reading leaf is available")
        template = rows[0]
        version = await core.actor_memory.role_version(ACTOR)
        receipts = []
        for index, stamp in enumerate(stamps):
            row = copy.deepcopy(template)
            ref = dict(
                producer="companion",
                object_id=f"al13-synthetic-chronology:{index}",
                revision="1",
                content_hash=digest(["explicit-fixture", index, stamp]),
            )
            row.update(id=SOURCE_PREFIX + digest(ref), source=ref)
            row["fact"].update(
                source=ref,
                occurred_at=stamp,
                summary=f"AL13_SYNTHETIC_CHRONOLOGY_{index} 原件已读；时间为隔离夹具。"
                + "合成预算材料。" * 180,
            )
            core.store.put("metadata", row)
            receipts.append(
                await core.actor_memory._memory(
                    dict(
                        schema_version=1,
                        request_id="al13-append:" + uuid4().hex,
                        actor_id=ACTOR,
                        role_version=version,
                        operation="append_episode",
                        idempotency_key="al13-synthetic-chronology:" + str(index),
                        source=ref,
                        perspective="participant",
                    )
                )
            )
        return receipts

    receipts = await host.exchange.invoke("companion", seed)

    async def query(app, cursor=None, budget=32768, context=CONTEXT):
        return await app.state.core.actor_memory.query(
            ACTOR, context, window=window, cursor=cursor, limit=1, budget_bytes=budget
        )

    pages, ids, cursor = [], [], None
    for _ in range(4):
        page = await host.exchange.invoke("companion", lambda app: query(app, cursor))
        pages.append(page)
        ids.extend(r["ref"]["id"] for r in page["items"])
        if page["next_cursor"] is None:
            break
        require(page["next_cursor"] != cursor, "PG high-precision keyset must advance")
        cursor = page["next_cursor"]
    require(
        ids == [r["episode_id"] for r in receipts[:3]],
        "yesterday UTC half-open window: no duplicates or rounded-boundary omissions",
    )
    require(pages[-1]["complete"], "complete last page after all eligible chronology")
    tiny = await host.exchange.invoke("companion", lambda app: query(app, budget=1024))
    require(
        not tiny["items"]
        and tiny["omitted"]
        and not tiny["complete"]
        and tiny["next_cursor"],
        "small budget is incomplete, never empty-day proof",
    )
    denied = await host.exchange.invoke(
        "companion", lambda app: query(app, context=PUBLIC)
    )
    require(
        not denied["items"] and denied["omitted"],
        "public cannot upgrade actor-only basis",
    )
    return dict(
        window=window,
        timezone=timezone_name,
        synthetic_occurrences=stamps,
        episode_ids=ids,
        pages=pages,
        budget_incomplete=True,
        actor_only_not_public=True,
        fixture="Synthetic chronology metadata; real read proof, source owner and Memory HTTP",
    )


async def knowledge_checks(host, helper, state, sources, diary_evidence, provider):
    token = state["bindings"]["tokens"]["knowledge_companion"]
    path = "/internal/v1/knowledge/content/"

    def principal():
        return dict(
            kind="actor",
            request_id="al13-content:" + uuid4().hex,
            actor_id=ACTOR,
            operation_ref="al13-native-disclosure",
        )

    async def call(operation, payload, status=200):
        reply = await helper.json_call(
            host, "companion", "knowledge", path + operation, token, payload
        )
        require(reply["status"] == status, f"Knowledge {operation}: {reply}")
        return reply["body"]

    async def acquire(url):
        return await call(
            "acquire",
            dict(
                principal=principal(), source=dict(kind="url", url=url), purpose="read"
            ),
        )

    async def proof(ref, status=200):
        return await call(
            "disclosure",
            dict(
                schema_version=1,
                principal=principal(),
                content_ref=ref,
                audience=dict(kind="public"),
            ),
            status,
        )

    private = await acquire(sources.origin + "/article")
    denied = await proof(private["content_ref"])
    require(
        denied["read_allowed"] and not denied["disclosure_allowed"],
        "actual trusted loopback acquisition remains private",
    )
    original_dns, original_connect = socket.getaddrinfo, socket.create_connection
    target = urlsplit(sources.origin)

    def dns(hostname, port, *args, **kwargs):
        if hostname == "al13-public.invalid":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        return original_dns(hostname, port, *args, **kwargs)

    def connect(address, *args, **kwargs):
        if address == ("93.184.216.34", 80):
            return original_connect(("127.0.0.1", target.port), *args, **kwargs)
        return original_connect(address, *args, **kwargs)

    try:
        socket.getaddrinfo, socket.create_connection = dns, connect
        public = await acquire("http://al13-public.invalid/article")
        from native_al13_diaries import public_diary

        public_evidence = await public_diary(
            host, helper, state, "http://al13-public.invalid/article", diary_evidence
        )
    finally:
        socket.getaddrinfo, socket.create_connection = original_dns, original_connect
    allowed = await proof(public["content_ref"])
    require(
        allowed["read_allowed"]
        and allowed["disclosure_allowed"]
        and allowed["classification"] == "public_anonymous",
        "actual Knowledge original owner proves fixture anonymous global chain",
    )
    read = await call(
        "read",
        dict(
            principal=principal(),
            content_ref=public["content_ref"],
            range=dict(unit="characters", start=0, end=64),
            budget_bytes=8192,
        ),
    )
    require(bool(read.get("text")), "actual public original byte-derived read")
    # Withdrawal uses the original owner port and its unchanged access version.
    await call(
        "access",
        dict(
            principal=principal(),
            content_ref=public["content_ref"],
            action="withdraw",
            reader_scope=None,
            expected_access_version=public["access_version"],
        ),
    )
    await proof(public["content_ref"], status=404)
    from native_al13_diaries import after_public_withdrawal

    public_withdrawal = await after_public_withdrawal(
        host, helper, state, public_evidence
    )
    await call(
        "access",
        dict(
            principal=principal(),
            content_ref=private["content_ref"],
            action="withdraw",
            reader_scope=None,
            expected_access_version=private["access_version"],
        ),
    )
    from native_al13_diaries import after_private_withdrawal

    revoked_diary = await after_private_withdrawal(host, diary_evidence, provider)
    return dict(
        private_diary_withdrawal=revoked_diary,
        public_diary=public_evidence,
        public_diary_withdrawal=public_withdrawal,
        private_denied=True,
        public_allowed=True,
        withdrawn_denied=True,
        public_ref=public["content_ref"],
        disclosure_version=allowed["disclosure_version"],
        evidence="Declared one-host DNS/connect seam; real loopback bytes and Knowledge owner HTTP/storage, not external networking",
    )


async def run(options):
    sources_holder = {}
    original_provider = base.provider_fixture

    def provider_factory(*args, **kwargs):
        from native_al13_diaries import extend_provider

        provider = extend_provider(original_provider(*args, **kwargs))
        sources_holder["provider"] = provider
        return provider

    base.provider_fixture = provider_factory
    original_configure = base.configure

    def configure(*args, **kwargs):
        state = original_configure(*args, **kwargs)
        host_path = args[4]
        state["al13_core_token"] = args[5]["core"]
        companion_path = read(host_path)["modules"]["companion"]["settings"]
        companion = read(companion_path)
        companion["life_writing"] = True
        companion.setdefault("life_readers", {})["platform"] = dict(
            reader_id="al13-native-reader", actor_ids=[ACTOR]
        )
        write(companion_path, companion)
        settings_path = read(host_path)["modules"]["knowledge"]["settings"]
        settings = read(settings_path)
        caller = settings["callers"]["companion"]
        caller["operations"] = list(
            dict.fromkeys(
                caller["operations"] + ["content_access", "content_actor_access"]
            )
        )
        write(settings_path, settings)
        return state

    base.configure = configure
    original_source_open = Sources.open

    async def source_open(self):
        sources_holder["sources"] = self
        return await original_source_open(self)

    Sources.open = source_open
    al13 = {
        "ok": False,
        "pending": [
            "Subscription-specific diary sharing and ordinary dialogue recall are independently exercised by native_al13_scope_check.py",
        ],
    }

    async def checks(host, helper, state):
        al13["timeline"] = await timeline_checks(host)
        from native_al13_diaries import private_diary

        al13["private_diary"] = await private_diary(host, al13["timeline"])
        al13["knowledge"] = await knowledge_checks(
            host,
            helper,
            state,
            sources_holder["sources"],
            al13["private_diary"],
            sources_holder["provider"],
        )
        al13["ok"] = True
        return {"al13": al13, "multimedia_repeated": False}

    original_media_checks = base.media_checks
    base.media_checks = checks  # Test extension point only; no product module patched.
    try:
        await base.run(options)
    except BaseException as error:
        al13["ok"] = False
        al13["failure"] = dict(
            type=type(error).__name__,
            code=getattr(error, "code", None),
            assertion=str(error) if isinstance(error, AssertionError) else None,
        )
        raise
    finally:
        Sources.open = original_source_open
        base.configure = original_configure
        base.provider_fixture = original_provider
        base.media_checks = original_media_checks
        if options.output.is_dir():
            al13["source_sha256"] = hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest()
            al13["diary_fixture_sha256"] = hashlib.sha256(
                Path(__file__).with_name("native_al13_diaries.py").read_bytes()
            ).hexdigest()
            (options.output / "native-al13-validation.json").write_text(
                json.dumps(al13, ensure_ascii=False, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    asyncio.run(run(base.arguments(__doc__)))
