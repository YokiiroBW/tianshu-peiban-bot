#!/usr/bin/env python3
"""Installed-wheel graph checks on native_host_smoke's isolated BusinessHost.

Use the unchanged native_host_smoke arguments, including --http-web-access.
The only provider is its local unused HTTP fixture. No model call or QQ socket
client is made. The public HTTP console and TLS API are real loopback sockets.
HTTP health requests are rejected with 403 by the outer native Ingress before
the Platform public app; its own 404 boundary remains covered by its unit tests.
The graph reader reads only this new fixture's own safe Platform JSONL files;
that seam is deliberately not evidence of a Loki/guard deployment.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

FRONTEND_SOURCE_COMMIT = "e639b08b5a9b3c29da4d60ac4ef1e2380be36469"
FRONTEND_FILE_COUNT = 71
FRONTEND_FILES_SHA256 = "667060c2ac22ab77740cea57a8de4a564a83df8f9d427ac7e086b7cff4e93328"
STATIC_ROOT = Path("/srv/tianshu/web")


class CheckFailed(RuntimeError):
    pass


def check(condition, code):
    if not condition:
        raise CheckFailed(code)


def load_native_smoke():
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--host-source", type=Path, required=True)
    known, _ = preliminary.parse_known_args()
    path = known.host_source.resolve() / "tests/deployment/single_container/native_host_smoke.py"
    spec = importlib.util.spec_from_file_location("chain_graph_original_native_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def timestamp_ns(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    check(stamp.utcoffset() is not None, "fixture_log_timestamp_without_timezone")
    return int(stamp.timestamp() * 1_000_000_000)


def static_records():
    check(STATIC_ROOT.is_dir(), "installed_static_directory_missing")
    records = []
    for path in STATIC_ROOT.rglob("*"):
        check(not path.is_symlink(), "installed_static_link")
        if path.is_file():
            raw = path.read_bytes()
            records.append(
                {
                    "path": path.relative_to(STATIC_ROOT).as_posix(),
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    records.sort(key=lambda row: row["path"])
    digest = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    check(len(records) == FRONTEND_FILE_COUNT, "installed_static_file_count_mismatch")
    check(digest == FRONTEND_FILES_SHA256, "installed_static_bundle_hash_mismatch")
    return records, digest


def extend_fixture(native, options, upstream):
    from test_bots import bot_settings

    path, tokens, account, source_databases = native.prepare(options, upstream)
    root = options.output.resolve()
    host_document = json.loads(Path(path).read_bytes())
    platform_path = Path(host_document["modules"]["platform"]["settings"]).resolve()
    check(platform_path.is_relative_to(root), "platform_fixture_settings_outside_output")
    platform = json.loads(platform_path.read_bytes())
    check(platform["web"]["username"] == "synthetic-admin", "unexpected_web_fixture_identity")
    helper_directory = root / "graph-bot-helper"
    helper_directory.mkdir()
    bots = bot_settings(str(helper_directory))
    for key in ("entries", "input_entries"):
        platform[key].update(
            {name: item for name, item in bots[key].items() if name.startswith("bot-")}
        )
    principal = platform["web"]["principal"]
    if "bot.manage" not in platform["principals"][principal]["actions"]:
        platform["principals"][principal]["actions"].append("bot.manage")
    platform["bot_connections"] = bots["bot_connections"]
    platform["web"]["static_directory"] = str(STATIC_ROOT)
    # This is the same generated certificate actually used by the TLS listener,
    # rather than a mocked readiness result or a relaxed TLS mode.
    platform["tls"] = {
        "certificate_file": str(root / "tls/guard.pem"),
        "private_key_file": str(root / "tls/guard.key"),
    }
    write_json(platform_path, platform)
    check(
        "flow_query" not in host_document["modules"]["platform"],
        "unexpected_external_log_query_in_fixture",
    )
    return path, tokens, account, source_databases


def install_probe_observer(state):
    """Observe the original Probe on the exact private app, without replacing verdicts."""
    from services.platform import runtime_health, server

    original_create = server.create_app

    def create(platform, *args, **kwargs):
        if not kwargs.get("public", False) and kwargs.get("probe") is None:
            check(state.get("probe") is None, "more_than_one_native_platform_probe")
            probe = runtime_health.Probe(platform.health)
            original_ready, original_evaluate = probe.ready, probe.evaluate

            async def ready():
                with state["lock"]:
                    wave = state["wave"]
                    if wave is not None:
                        wave["ready_entries"] += 1
                        if wave["ready_entries"] >= 2:
                            wave["second_entry"].set()
                return await original_ready()

            def evaluate():
                with state["lock"]:
                    wave = state["wave"]
                    if wave is not None:
                        wave["evaluation_entries"] += 1
                if wave is not None:
                    wave["evaluation_started"].set()
                    check(wave["release"].wait(2), "readiness_overlap_fixture_release_timeout")
                return original_evaluate()

            probe.ready, probe.evaluate = ready, evaluate
            state["probe"] = probe
            kwargs["probe"] = probe
        return original_create(platform, *args, **kwargs)

    server.create_app = create
    return lambda: setattr(server, "create_app", original_create)


def own_log_reader(directory):
    """Read existing records only; never manufacture an event after the query end."""

    def read(*, correlation_id, start_ns, end_ns, limit):
        events, partial = [], False
        for path in sorted(directory.glob("*.jsonl")):
            check(path.resolve().is_relative_to(directory), "fixture_log_path_escape")
            for line in path.read_bytes().splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    partial = True
                    continue
                check(len(line) <= 4096, "fixture_safe_event_line_too_large")
                event = json.loads(line)
                actual = event.get("correlation_id")
                if not isinstance(actual, str) or len(actual) != 32:
                    continue
                if correlation_id is not None and actual != correlation_id:
                    continue
                if start_ns <= timestamp_ns(event["timestamp"]) <= end_ns:
                    events.append(event)
        events.sort(
            key=lambda row: (
                timestamp_ns(row["timestamp"]),
                row["service"],
                row["instance_id"],
                row["sequence"],
            )
        )
        return {"events": events[:limit], "complete": not partial and len(events) <= limit}

    async def reader(**kwargs):
        return await asyncio.to_thread(read, **kwargs)

    return reader


def assert_projection_safe(value):
    if isinstance(value, dict):
        check(
            not {
                "token",
                "access_key",
                "address",
                "origin_ref",
                "secret_ref",
                "payload",
                "text",
                "base_url",
            }.intersection(value),
            "sensitive_projection_field",
        )
        for item in value.values():
            assert_projection_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_projection_safe(item)


async def json_response(response, status, code):
    check(response.status_code == status, code + "_http_" + str(response.status_code))
    check("application/json" in response.headers.get("content-type", ""), code + "_not_json")
    check(response.headers.get("cache-control") == "no-store", code + "_cache_policy")
    return response.json()


async def graph_checks(host, settings, root, origin, public, summary):
    from deploy.single_container.applications import app_platform
    from web_fixtures import PASSWORD

    module = host.modules["platform"]
    check(
        await host.exchange.invoke(
            "platform", lambda app: module.console.flow_graph.event_reader is None
        ),
        "graph_reader_should_start_unconfigured",
    )

    def create_disabled_bot(app):
        receipt = app_platform(app).bots.create("qq-onebot-main", ["actor:a"])
        return {"id": receipt["connection_id"], "enabled": receipt["enabled"]}

    created = await host.exchange.invoke("platform", create_disabled_bot)
    check(created["enabled"] is False, "synthetic_bot_was_enabled")
    initial = await json_response(
        await public.get(origin + "/api/web/session"), 200, "anonymous_session"
    )
    check(initial["authenticated"] is False, "fixture_browser_already_authenticated")
    csrf = initial["csrf"]
    headers = {"Origin": origin, "X-CSRF-Token": csrf}
    await json_response(
        await public.post(origin + "/api/web/flow-graph/relationships", json={}, headers=headers),
        401,
        "anonymous_graph_denied",
    )
    logged = await json_response(
        await public.post(
            origin + "/api/web/login",
            json={"username": "synthetic-admin", "password": PASSWORD},
            headers=headers,
        ),
        200,
        "actual_web_login",
    )
    check(logged["authenticated"] is True, "actual_web_login_not_authenticated")
    headers["X-CSRF-Token"] = logged["csrf"]
    await json_response(
        await public.post(
            origin + "/api/web/flow-graph/relationships",
            json={},
            headers={**headers, "X-CSRF-Token": "synthetic-wrong-csrf"},
        ),
        403,
        "graph_csrf_denied",
    )

    correlation = uuid.uuid4().hex
    relation = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/relationships",
            json={},
            headers={**headers, "X-Tianshu-Correlation-Id": correlation},
        ),
        200,
        "actual_relationship_graph",
    )
    assert_projection_safe(relation)
    check(
        any(row["account_id"] == "4242" for row in relation["accounts"]),
        "actual_bot_account_missing_from_graph",
    )
    check(
        any(row["id"] == "actor:a" for row in relation["roles"]),
        "actual_bot_role_missing_from_graph",
    )
    check(
        any(
            row["conversation_id"] == "group:123"
            and row["actor_ids"] == ["actor:a"]
            and row["mode"] == "disabled"
            for row in relation["conversations"]
        ),
        "actual_disabled_group_mapping_missing",
    )
    check(PASSWORD not in json.dumps(relation), "fixture_password_in_projection")

    index = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/traces", json={"minutes": 15}, headers=headers
        ),
        200,
        "unconfigured_trace_index",
    )
    check(
        index["availability"] == "not_configured"
        and index["complete"] is False
        and index["traces"] == [],
        "unconfigured_trace_index_claimed_success",
    )
    end_ns = time.time_ns()
    detail = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/trace",
            json={
                "correlation_id": correlation,
                "start_ns": str(end_ns - 60 * 10**9),
                "end_ns": str(end_ns),
            },
            headers=headers,
        ),
        200,
        "unconfigured_trace_detail",
    )
    check(
        detail["availability"] == "not_configured"
        and detail["complete"] is False
        and detail["events"] == [],
        "unconfigured_trace_detail_claimed_success",
    )

    log_directory = Path(settings["modules"]["platform"]["env"]["TIANSHU_LOG_DIR"]).resolve()
    check(log_directory.is_relative_to(root), "log_reader_outside_synthetic_output")
    reader = own_log_reader(log_directory)
    await host.exchange.invoke(
        "platform", lambda app: setattr(module.console.flow_graph, "event_reader", reader)
    )
    index = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/traces", json={"minutes": 15}, headers=headers
        ),
        200,
        "actual_own_log_trace_index",
    )
    check(index["availability"] == "ready", "fixture_log_reader_not_ready")
    actual = next((item for item in index["traces"] if item["correlation_id"] == correlation), None)
    check(
        actual is not None and actual["business_observed"] is False,
        "real_http_correlation_missing_or_misclassified",
    )
    detail = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/trace",
            json={"correlation_id": correlation, **index["window"]},
            headers=headers,
        ),
        200,
        "actual_own_log_trace_detail",
    )
    check(
        detail["events"]
        and all(event["correlation_id"] == correlation for event in detail["events"]),
        "actual_trace_correlation_mismatch",
    )
    check(
        any(event["event"] == "http.request.started" for event in detail["events"]),
        "actual_http_started_event_missing",
    )
    check(
        all(not stage["event_ids"] for stage in detail["stages"]),
        "generic_http_invented_business_phase",
    )
    assert_projection_safe(detail)

    async def unavailable_reader(**kwargs):
        raise RuntimeError("synthetic-reader-unavailable")

    await host.exchange.invoke(
        "platform",
        lambda app: setattr(module.console.flow_graph, "event_reader", unavailable_reader),
    )
    failure = await json_response(
        await public.post(
            origin + "/api/web/flow-graph/traces", json={"minutes": 15}, headers=headers
        ),
        503,
        "unavailable_log_reader",
    )
    check(failure.get("code") == "flow_query_unavailable", "unavailable_reader_error_not_closed")
    check("synthetic-reader-unavailable" not in json.dumps(failure), "reader_exception_disclosed")
    await host.exchange.invoke(
        "platform", lambda app: setattr(module.console.flow_graph, "event_reader", None)
    )
    summary["graphs"] = {
        "real_http_cookie_login": True,
        "anonymous_status": 401,
        "bad_csrf_status": 403,
        "accounts": len(relation["accounts"]),
        "roles": len(relation["roles"]),
        "conversations": len(relation["conversations"]),
        "bot_disabled": True,
        "query_unconfigured_index_and_detail": True,
        "query_unavailable_status": 503,
        "trace_correlation": correlation,
        "trace_events": detail["events"],
        "trace_complete": detail["complete"],
        "no_invented_business_phase": True,
        "reader_scope": "fixture-only actual Platform safe logs; no Loki or guard query exercised",
    }


async def static_checks(origin, public, records, digest, summary):
    entry = await public.get(origin + "/#/settings/12")
    check(
        entry.status_code == 200 and "<html" in entry.text, "actual_settings_12_static_entry_failed"
    )
    check(
        entry.content == (STATIC_ROOT / "index.html").read_bytes(),
        "http_entry_differs_from_installed_bundle",
    )
    graph_chunks = [
        row
        for row in records
        if row["path"].startswith("assets/FlowGraphPage-") and row["path"].endswith(".js")
    ]
    check(len(graph_chunks) == 1, "actual_graph_lazy_chunk_missing")
    content = (STATIC_ROOT / graph_chunks[0]["path"]).read_text(encoding="utf-8")
    check(
        all(word in content for word in ("配置关系", "实际运行", "flow-graph/")),
        "actual_graph_chunk_does_not_contain_both_views",
    )
    settings_chunks = [
        row
        for row in records
        if row["path"].startswith("assets/SettingsPage-") and row["path"].endswith(".js")
    ]
    check(
        any(
            "#/settings/12" in (STATIC_ROOT / row["path"]).read_text(encoding="utf-8")
            for row in settings_chunks
        ),
        "actual_settings_navigation_missing_graph",
    )
    served = []
    for record in records:
        response = await public.get(origin + "/" + record["path"])
        check(response.status_code == 200, "static_file_not_served_" + record["path"])
        check(
            len(response.content) == record["size"]
            and hashlib.sha256(response.content).hexdigest() == record["sha256"],
            "static_http_content_mismatch_" + record["path"],
        )
        served.append(
            {"path": record["path"], "status": response.status_code, "sha256": record["sha256"]}
        )
    summary["frontend"] = {
        "source_commit": FRONTEND_SOURCE_COMMIT,
        "files_sha256": digest,
        "installed_directory": str(STATIC_ROOT),
        "hash_route": "#/settings/12",
        "entry_http_path": "/",
        "hash_fragment_is_client_side": True,
        "both_view_chunk_present": True,
        "browser_javascript_execution_tested": False,
        "http_files": served,
    }


async def ready_checks(host, api_origin, tls_client, state, summary):
    module = host.modules["platform"]
    check(
        await host.exchange.invoke(
            "platform",
            lambda app: (
                module.api_listener is not None
                and module.api_listener.app is app
                and not module.api_listener.own_lifecycle
            ),
        ),
        "tls_listener_not_sharing_exact_native_app",
    )
    token = module.environment["TIANSHU_DIAGNOSTICS_TOKEN"]
    waves = []
    for number in range(5):
        # The production TTL is one second. Let it expire naturally instead of
        # replacing the probe's cache, clock, checks or ready() implementation.
        await asyncio.sleep(1.15)
        wave = {
            "ready_entries": 0,
            "evaluation_entries": 0,
            "evaluation_started": threading.Event(),
            "second_entry": threading.Event(),
            "release": threading.Event(),
        }
        with state["lock"]:
            state["wave"] = wave
        tasks = [
            asyncio.create_task(
                tls_client.get(
                    api_origin + "/health/ready", headers={"Authorization": "Bearer " + token}
                )
            )
            for _ in range(2)
        ]
        try:
            entered = await asyncio.to_thread(wave["second_entry"].wait, 3)
        finally:
            wave["release"].set()
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        with state["lock"]:
            state["wave"] = None
        statuses = [
            item.status_code if not isinstance(item, BaseException) else type(item).__name__
            for item in responses
        ]
        bodies = [
            item.json()
            if not isinstance(item, BaseException)
            and item.headers.get("content-type", "").startswith("application/json")
            else None
            for item in responses
        ]
        waves.append(
            {
                "wave": number + 1,
                "statuses": statuses,
                "both_entered_original_probe": entered,
                "ready_entries": wave["ready_entries"],
                "actual_evaluations": wave["evaluation_entries"],
                "checks": [
                    body.get("checks") if isinstance(body, dict) else None for body in bodies
                ],
            }
        )
    summary["native_tls_ready"] = {
        "real_tls_network": True,
        "certificate_verified": True,
        "same_native_app": True,
        "controlled_evaluation_overlap": True,
        "original_checks_and_verdicts_preserved": True,
        "waves": waves,
    }
    check(
        all(
            wave["statuses"] == [200, 200]
            and wave["both_entered_original_probe"]
            and wave["ready_entries"] == 2
            and wave["actual_evaluations"] == 1
            for wave in waves
        ),
        "native_tls_ready_overlap_failed",
    )


async def run(native, options):
    import httpx
    from fixtures import DOCUMENTS
    from deploy.single_container.host import BusinessHost
    from deploy.single_container.host_settings import load_settings

    native.installed_wheels()
    records, bundle_digest = static_records()
    root = options.output.resolve()
    check(not root.exists(), "output_must_be_new")
    upstream = await native.upstream_fixture(DOCUMENTS["config"]["providers"][0]["model_id"])
    host, restore_probe = None, None
    state = {"lock": threading.Lock(), "wave": None, "probe": None}
    summary = {
        "schema_version": "1.0.0",
        "suite": "chain-graph-native-installed-wheel",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "scope": "new isolated synthetic fixture only",
        "production_configuration_read": False,
        "remote_qq_messages": 0,
        "external_model_calls": 0,
    }
    source_databases = None
    try:
        summary["stage"] = "prepare"
        path, _, _, source_databases = extend_fixture(native, options, upstream[1])
        settings = load_settings(path)
        restore_probe = install_probe_observer(state)
        host = BusinessHost(settings)
        summary["stage"] = "native-host-open"
        await host.open()
        origin = json.loads(Path(settings["modules"]["platform"]["settings"]).read_bytes())["web"][
            "origin"
        ]
        api_origin = "https://127.0.0.1:" + str(settings["frontend"]["port"])
        check(origin.startswith("http://127.0.0.1:"), "expected_existing_http_web_access_mode")
        verify = ssl.create_default_context(cafile=str(root / "tls/ca.pem"))
        async with (
            httpx.AsyncClient(verify=verify, trust_env=False, timeout=10) as public,
            httpx.AsyncClient(verify=verify, trust_env=False, timeout=10) as tls_client,
        ):
            summary["stage"] = "static-http"
            await static_checks(origin, public, records, bundle_digest, summary)
            summary["stage"] = "web-console-graphs"
            await graph_checks(host, settings, root, origin, public, summary)
            summary["stage"] = "public-probe-isolation"
            hidden = {}
            for path in ("/health/live", "/health/ready"):
                response = await public.get(origin + path)
                hidden[path] = response.status_code
            summary["public_health"] = hidden
            summary["public_health_boundary"] = (
                "outer native Ingress rejects insecure health requests with 403 before the "
                "Platform public app; its own 404 boundary is covered separately by existing unit tests"
            )
            summary["platform_public_app_health_404_exercised"] = False
            check(
                all(status == 403 for status in hidden.values()),
                "insecure_public_probe_not_rejected_by_ingress",
            )
            summary["stage"] = "same-native-tls-ready-overlap"
            await ready_checks(host, api_origin, tls_client, state, summary)
        check(not upstream[4], "unexpected_local_provider_request")
        summary.update(
            passed=True,
            local_provider_calls=0,
            no_model_request_made=True,
            no_qq_websocket_client=True,
        )
    except BaseException as error:
        summary["error"] = {
            "type": type(error).__name__,
            "code": str(error) if isinstance(error, CheckFailed) else "native_fixture_failed",
        }
        raise
    finally:
        with state["lock"]:
            wave = state["wave"]
            if wave is not None:
                wave["release"].set()
            state["wave"] = None
        try:
            if host is not None and host.started:
                summary["clean_shutdown"] = await host.close(timeout=30)
                if not summary["clean_shutdown"]:
                    summary["passed"] = False
        finally:
            if restore_probe is not None:
                restore_probe()
            upstream[2].set()
            await upstream[0].cleanup()
            if source_databases is not None:
                unchanged = all(
                    hashlib.sha256((options.memory_fixture / relative).read_bytes()).hexdigest()
                    == digest
                    for relative, digest in source_databases.items()
                )
                summary["source_fixture_databases_unchanged"] = unchanged
                if not unchanged:
                    summary["passed"] = False
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            if root.is_dir():
                write_json(root / "chain-graph-native-validation.json", summary)
            print(
                json.dumps(
                    {
                        "passed": summary["passed"],
                        "stage": summary.get("stage"),
                        "evidence": str(root / "chain-graph-native-validation.json"),
                        "error": summary.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    check(summary["passed"], "native_graph_validation_failed")


def main():
    try:
        native = load_native_smoke()
        options = native.arguments()
        check(
            options.http_web_access and not options.web_access,
            "use_existing_--http-web-access_mode_for_ingress403_and_native_tls200",
        )
        sys.path[:0] = [
            str(options.host_source.resolve()),
            str(options.platform_fixtures.resolve()),
        ]
        os.environ["TS012_CONTRACT_DIR"] = str(options.contracts.resolve())
        asyncio.run(run(native, options))
    except Exception as error:
        # Fixture failures may contain addresses or generated credentials. The
        # retained JSON has the same closed error code; never dump the exception.
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": {
                        "type": type(error).__name__,
                        "code": str(error)
                        if isinstance(error, CheckFailed)
                        else "native_fixture_failed",
                    },
                }
            ),
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
