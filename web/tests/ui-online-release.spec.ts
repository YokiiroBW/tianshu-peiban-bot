import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import {
  UI_BUILD_ID,
  evaluateUiVersionChange,
  publicAssetUrl,
} from "../src/app/uiRelease";
import { answer, apiPath, nativeSession } from "./native-fixture";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "../..");
const PYTHON_EXE = process.env.TIANSHU_TEST_PYTHON || "python";
const GIT_EXE = process.env.TIANSHU_TEST_GIT || "git";
const SHARED_NODE_MODULES = path.join(REPO_ROOT, "web", "node_modules");
const SOURCE_SHA = execFileSync(GIT_EXE, ["rev-parse", "HEAD"], {
  cwd: REPO_ROOT,
  encoding: "utf-8",
}).trim();

const VERIFY_ROOT = path.join(
  REPO_ROOT,
  ".runtime",
  "ui-online-release-verification-20261108",
  SOURCE_SHA,
);
const BUILD_A = `ui-test-${SOURCE_SHA.slice(0, 12)}-a`;
const BUILD_B = `ui-test-${SOURCE_SHA.slice(0, 12)}-b`;
const ARCHIVE_A = path.join(
  VERIFY_ROOT,
  "owned",
  "pkg-a",
  `ui-release-${BUILD_A}.tar.gz`,
);
const ARCHIVE_B = path.join(
  VERIFY_ROOT,
  "owned",
  "pkg-b",
  `ui-release-${BUILD_B}.tar.gz`,
);

function ensurePackagesBuilt() {
  if (fs.existsSync(ARCHIVE_A) && fs.existsSync(ARCHIVE_B)) {
    return;
  }
  const env = {
    ...process.env,
    PYTHONPATH: [path.join(REPO_ROOT, "src"), REPO_ROOT].join(path.delimiter),
  };
  const headSha = execFileSync(GIT_EXE, ["rev-parse", "HEAD"], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  }).trim();
  const ownedRoot = path.join(VERIFY_ROOT, "owned");
  fs.mkdirSync(ownedRoot, { recursive: true });
  for (const [pkgDir, buildId, archive] of [
    ["pkg-a", BUILD_A, ARCHIVE_A],
    ["pkg-b", BUILD_B, ARCHIVE_B],
  ] as const) {
    if (fs.existsSync(archive)) continue;
    const outDir = path.join(ownedRoot, pkgDir);
    if (fs.existsSync(outDir)) {
      if (path.dirname(path.resolve(outDir)) !== path.resolve(ownedRoot))
        throw new Error("UI test output escaped its owned root");
      fs.rmSync(outDir, { recursive: true, force: true });
    }
    execFileSync(
      PYTHON_EXE,
      [
        path.join(REPO_ROOT, "deploy/core/ui_release.py"),
        "build",
        "--checkout",
        REPO_ROOT,
        "--source-sha",
        headSha,
        "--owned-root",
        ownedRoot,
        "--output",
        outDir,
        "--build-id",
        buildId,
        "--node-modules",
        SHARED_NODE_MODULES,
        "--min-free-bytes",
        "1048576",
      ],
      { cwd: REPO_ROOT, env, stdio: "pipe" },
    );
  }
}

function runReleaseCli(args: string[]): Record<string, any> {
  const env = {
    ...process.env,
    PYTHONPATH: [path.join(REPO_ROOT, "src"), REPO_ROOT].join(path.delimiter),
  };
  const out = execFileSync(
    PYTHON_EXE,
    [path.join(REPO_ROOT, "deploy/core/ui_release.py"), ...args],
    { cwd: REPO_ROOT, env, encoding: "utf-8" },
  );
  return JSON.parse(out.trim().split(/\r?\n/).pop()!);
}

async function startRealAiohttpServer(uiRoot: string): Promise<{
  baseUrl: string;
  stop: () => Promise<void>;
}> {
  const builtinDir = path.join(REPO_ROOT, "web", "dist");
  const serverScript = `
import asyncio, json, os, sys
from pathlib import Path
from uuid import uuid4
from aiohttp import web

sys.path.insert(0, r"${path.join(REPO_ROOT, "src")}")
from tianshu.runtime.static import register_static

SERVER_PID = os.getpid()
SERVER_INSTANCE_ID = str(uuid4())
DATA_GENERATION = "gen-online-release-fixed"
CSRF_TOKEN = "csrf-online-release-1"
SESSION_COOKIE = "session-online-release-1"

state = {
    "background_ticks": 0,
    "api_requests": 0,
    "recover_writes": 0,
}

async def background_worker():
    while True:
        state["background_ticks"] += 1
        await asyncio.sleep(0.03)

empty_page = {"items": [], "next_cursor": None, "coverage": {"complete": True, "total": None, "missing": []}}
room_controls = {"window": 0, "sheer": 0.5, "curtain": 0, "desk_light": 0, "bedside_light": 0, "desk_warmth": 0.8, "bedside_warmth": 1}
incident = {
    "incident_id": "inc-1", "dependency": "model-broker", "error_family": "broker_reload_pending",
    "version": 1, "severity": "warning", "state": "open", "revision": 1, "occurrences": 1,
    "first_seen": "2026-11-08T00:00:00Z", "last_seen": "2026-11-08T00:01:00Z", "resolved_at": None,
    "summary": "测试事件", "affected": [], "diagnostic_state": "pending",
    "recovery_action": "model_configuration_refresh", "recovery_lineage": None, "last_observation": None,
}
values = {
    "companion/roles/list": {**empty_page, "items": [{"actor_id": "actor", "name": "小屋角色", "revision": 1, "enabled": True}]},
    "identity/conversations": {**empty_page, "items": [{"conversation_id": "web-conversation", "actor_id": "actor", "person_id": "person", "kind": "private", "label": "网页私聊", "revision": 1, "scope_id": "scope", "adapter": "web", "target_ref": "web:web-conversation"}]},
    "life/access": {"can_read": True, "can_manage": True, "can_read_published": True},
    "life/state": {"actor_id": "actor", "revision": 7, "timezone": "Asia/Shanghai", "world_id": "world", "room_id": "room", "weather_location": None, "activity": {"id": "reading", "revision": 1, "title": "正在阅读", "kind": "reading", "state": "running", "checkpoint_step": 0, "checkpoint_position": 0, "checkpoint_unit": "characters", "waiting_reason": None, "goal_id": None, "next_due_at": None, "resume_condition": None, "action_id": None, "checkpoint_data": {}, "source_refs": []}, "outfit": None, "affect": {"feelings": [], "regulation": {"mode": "steady", "expression_direction": "平静地表达"}, "source_refs": [], "missing": []}, "room_controls": room_controls, "observed_at": "2026-10-31T06:00:00Z", "missing": []},
    "life/routine": {"actor_id": "actor", "revision": 1, "enabled": True, "timezone": "Asia/Shanghai", "wake_at": "08:00", "sleep_at": "23:00", "idle_seconds": 5400, "next_due_at": "2026-11-01T06:00:00Z", "last_tick_at": None, "schedule_status": "waiting", "schedule_reason": None, "planning": None, "observed_at": "2026-11-01T05:00:00Z"},
    "life/room": {"actor_id": "actor", "revision": 7, "timezone": "Asia/Shanghai", "world_id": "world", "room_id": "room", "controls": room_controls, "weather_location": None, "fictional": True, "observed_at": "2026-10-31T06:00:00Z"},
    "life/activities": empty_page,
    "life/commitments": empty_page,
    "memory/episodes": {**empty_page, "watermark": "0"},
    "memory/claims": empty_page,
    "memory/matters": empty_page,
    "memory/query": empty_page,
    "companion/growth/view": {"items": [], "reflections": [], "next_cursor": None, "missing": [], "review": None, "can_review": True},
    "companion/growth/examples/view": {"items": [], "next_cursor": None, "missing": []},
    "companion/growth/reflections": empty_page,
    "infrastructure/host": {"configured": False, "node": None},
    "capabilities/discover": [],
    "capabilities/catalog/view": {"actor_id": "actor", "revision": 1, "items": [], "sources": []},
    "devices/view": {"connections": [], "readings": [], "can_manage": True, "can_control": True, "control_capability": "device.control"},
    "operations/incidents": {**empty_page, "items": [incident]},
    "operations/incidents/get": {**incident, "diagnostics": [], "recoveries": [], "notifications": []},
    "operations/settings": {"revision": 1, "value": {"diagnosis": {"primary_route_id": None, "backup_route_id": None}, "notifications": {"qq": None, "smtp": None}}, "diagnosis_configured": False, "notification_configured": {"qq": False, "smtp": False}, "smtp_credential_configured": None},
}

async def handle_proof(_request):
    return web.json_response({
        "pid": SERVER_PID,
        "instance_id": SERVER_INSTANCE_ID,
        "data_generation": DATA_GENERATION,
        "background_ticks": state["background_ticks"],
        "api_requests": state["api_requests"],
        "recover_writes": state["recover_writes"],
    })

async def handle_api(request: web.Request):
    state["api_requests"] += 1
    op = request.match_info["op"]
    supplied_gen = request.headers.get("X-Tianshu-Data-Generation") or request.query.get("data_generation")
    if supplied_gen and supplied_gen != DATA_GENERATION:
        return web.json_response(
            {"error": {"code": "data_generation_changed", "message": "数据已切换", "details": {"data_generation": DATA_GENERATION}, "request_id": "req-gen"}},
            status=409,
            headers={"X-Tianshu-Data-Generation": DATA_GENERATION},
        )
    if op == "session":
        resp = web.json_response({
            "authenticated": True,
            "initialized": True,
            "setup_token_required": False,
            "principal": {"principal_id": "author", "display_name": "测试作者", "permissions": ["*"]},
            "csrf_token": CSRF_TOKEN,
            "data_generation": DATA_GENERATION,
        }, headers={"X-Tianshu-Data-Generation": DATA_GENERATION})
        resp.set_cookie("tianshu_session", SESSION_COOKIE, path="/", httponly=True, samesite="Lax")
        return resp
    if request.cookies.get("tianshu_session") != SESSION_COOKIE:
        return web.json_response(
            {"error": {"code": "unauthorized", "message": "未登录", "details": None, "request_id": "req-auth"}},
            status=401,
            headers={"X-Tianshu-Data-Generation": DATA_GENERATION},
        )
    if op == "operations/recover":
        if request.headers.get("X-CSRF-Token") != CSRF_TOKEN:
            return web.json_response(
                {"error": {"code": "csrf_invalid", "message": "CSRF 无效", "details": None, "request_id": "req-csrf"}},
                status=403,
                headers={"X-Tianshu-Data-Generation": DATA_GENERATION},
            )
        state["recover_writes"] += 1
        return web.json_response(
            {"error": {"code": "upstream_timeout", "message": "结果待确认", "details": {"execution_state": "unknown"}, "request_id": "req-unk"}},
            status=502,
            headers={"X-Tianshu-Data-Generation": DATA_GENERATION},
        )
    if op in values:
        return web.json_response(values[op], headers={"X-Tianshu-Data-Generation": DATA_GENERATION})
    return web.json_response(
        {"error": {"code": "not_found", "message": op, "details": None, "request_id": "req-404"}},
        status=404,
        headers={"X-Tianshu-Data-Generation": DATA_GENERATION},
    )

async def main():
    app = web.Application()
    app.router.add_get("/_test/runtime-proof", handle_proof)
    app.router.add_route("*", "/api/v1/{op:.*}", handle_api)
    register_static(app, Path(r"${builtinDir}"), ui_release_directory=Path(r"${uiRoot}"))
    bg_task = asyncio.create_task(background_worker())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    print(f"READY_URL=http://127.0.0.1:{port}", flush=True)
    stop_event = asyncio.Event()
    await stop_event.wait()

asyncio.run(main())
`;

  const child: ChildProcess = spawn(PYTHON_EXE, ["-c", serverScript], {
    cwd: REPO_ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });

  const baseUrl = await new Promise<string>((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error(`Timed out starting real aiohttp server: ${output}`));
    }, 10_000);
    child.stdout?.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf-8");
      const match = output.match(/READY_URL=(http:\/\/127\.0\.0\.1:\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(match[1]);
      }
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf-8");
    });
    child.on("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`aiohttp server exited early (${code}): ${output}`));
    });
  });

  return {
    baseUrl,
    stop: async () => {
      child.kill();
      await new Promise((r) => child.once("exit", r));
    },
  };
}

test("publicAssetUrl and evaluateUiVersionChange follow the frozen release contract", () => {
  expect(publicAssetUrl("room-illustration-assets/day.png", "/")).toBe(
    "/room-illustration-assets/day.png",
  );
  expect(publicAssetUrl("/life-evening.png", "/")).toBe("/life-evening.png");
  expect(
    publicAssetUrl(
      "room-illustration-assets/day.png",
      "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/",
    ),
  ).toBe(
    "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/room-illustration-assets/day.png",
  );
  expect(
    publicAssetUrl(
      "/life-evening.png",
      "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBe("/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/life-evening.png");

  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t010000z-aaaaaaaaaaaa",
        served_mode: "external",
        compatible: true,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBeNull();

  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t020000z-bbbbbbbbbbbb",
        served_mode: "external",
        compatible: false,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBeNull();

  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t020000z-bbbbbbbbbbbb",
        served_mode: "external",
        compatible: true,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBe("ui-20261108t020000z-bbbbbbbbbbbb");
});

test("real aiohttp static service and two real built packages complete A->B->A online switch without restart or mixed assets", async ({
  context,
  page: pageA,
}, testInfo) => {
  test.setTimeout(90_000);
  ensurePackagesBuilt();

  const uiRoot = path.join(
    VERIFY_ROOT,
    `ui-data-${testInfo.project.name}-${Date.now()}`,
  );
  fs.mkdirSync(uiRoot, { recursive: true });

  const server = await startRealAiohttpServer(uiRoot);
  try {
    // 1. Preserve legacy root & verify builtin baseline before any external release
    const legacyReceipt = runReleaseCli([
      "preserve-legacy",
      "--ui-root",
      uiRoot,
      "--source-dist",
      path.join(REPO_ROOT, "web", "dist"),
    ]);
    expect(legacyReceipt.preserved).toBe(true);

    const ver0 = await (await fetch(`${server.baseUrl}/_ui/version`)).json();
    expect(ver0.served_mode).toBe("builtin");
    expect(ver0.degraded).toBe(false);

    // 2. Install real packages A and B, and activate A with HTTP verification
    const instA = runReleaseCli([
      "install",
      "--ui-root",
      uiRoot,
      "--package",
      ARCHIVE_A,
      "--min-free-bytes",
      "1048576",
    ]);
    expect(instA.package_installed).toBe(true);
    expect(instA.pointer_switched).toBe(false);

    const instB = runReleaseCli([
      "install",
      "--ui-root",
      uiRoot,
      "--package",
      ARCHIVE_B,
      "--min-free-bytes",
      "1048576",
    ]);
    expect(instB.package_installed).toBe(true);
    expect(instB.pointer_switched).toBe(false);

    const actA = runReleaseCli([
      "activate",
      "--ui-root",
      uiRoot,
      "--build-id",
      BUILD_A,
      "--verify-url",
      server.baseUrl,
    ]);
    expect(actA.package_installed).toBe(true);
    expect(actA.pointer_switched).toBe(true);
    expect(actA.http_verified).toBe(true);
    expect(actA.activation_sequence).toBe(1);

    const proof0 = await (
      await fetch(`${server.baseUrl}/_test/runtime-proof`)
    ).json();
    expect(proof0.data_generation).toBe("gen-online-release-fixed");

    // 3. Open Page A on operations page, fill draft input, trigger unknown command in CommandMonitor
    await pageA.clock.setFixedTime(new Date("2026-10-31T06:00:00Z"));
    await pageA.goto(`${server.baseUrl}/#/system/operations?incident_id=inc-1`);
    await expect(pageA.locator("html")).toHaveAttribute(
      "data-ui-build-id",
      BUILD_A,
    );
    await expect(pageA.getByLabel("界面版本更新提示")).toHaveCount(0);

    const draftInput = pageA.getByLabel("原材料 ID（可选）");
    await draftInput.fill("draft-preserved-across-release");
    await pageA
      .getByRole("button", { name: "请求 model_configuration_refresh" })
      .click();
    await expect(
      pageA.getByLabel("原操作状态").getByText("有一项提交尚未确认"),
    ).toBeVisible();

    // 4. Online switch A -> B on the SAME running aiohttp server (zero restart)
    const actB = runReleaseCli([
      "activate",
      "--ui-root",
      uiRoot,
      "--build-id",
      BUILD_B,
      "--expected-current",
      BUILD_A,
      "--verify-url",
      server.baseUrl,
    ]);
    expect(actB.pointer_switched).toBe(true);
    expect(actB.http_verified).toBe(true);
    expect(actB.previous_build_id).toBe(BUILD_A);
    expect(actB.current_build_id).toBe(BUILD_B);
    expect(actB.activation_sequence).toBe(2);

    const proof1 = await (
      await fetch(`${server.baseUrl}/_test/runtime-proof`)
    ).json();
    expect(proof1.pid).toBe(proof0.pid);
    expect(proof1.instance_id).toBe(proof0.instance_id);
    expect(proof1.data_generation).toBe(proof0.data_generation);
    expect(proof1.background_ticks).toBeGreaterThan(proof0.background_ticks);
    expect(proof1.recover_writes).toBe(1);

    // Page A detects B via /_ui/version and renders non-blocking Notification without auto-reload
    await pageA.evaluate(() => window.dispatchEvent(new Event("focus")));
    const notice = pageA.getByLabel("界面版本更新提示");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(BUILD_B);
    await expect(pageA.locator("html")).toHaveAttribute(
      "data-ui-build-id",
      BUILD_A,
    );
    await expect(draftInput).toHaveValue("draft-preserved-across-release");
    await expect(
      pageA.getByLabel("原操作状态").getByText("有一项提交尚未确认"),
    ).toBeVisible();

    // 5. Already-open Page A navigates for the FIRST TIME to lazy RoomPage & LifePage after switch to B
    const pageARequestsAfterSwitch: string[] = [];
    pageA.on("request", (req) => pageARequestsAfterSwitch.push(req.url()));

    await pageA.evaluate(() => {
      window.location.hash = "#/roles/room";
    });
    const canvasA = pageA.locator("canvas");
    await expect(canvasA).toHaveAttribute("data-ready", "true");
    expect(
      pageARequestsAfterSwitch.some((u) =>
        u.includes(`/_ui/releases/${BUILD_A}/assets/RoomPage-`),
      ),
    ).toBe(true);
    expect(
      pageARequestsAfterSwitch.some((u) =>
        u.includes(
          `/_ui/releases/${BUILD_A}/room-illustration-assets/room-clean-afternoon-v1.png`,
        ),
      ),
    ).toBe(true);
    expect(
      pageARequestsAfterSwitch.some((u) =>
        u.includes(`/_ui/releases/${BUILD_B}/`),
      ),
    ).toBe(false);

    await pageA.evaluate(() => {
      window.location.hash = "#/roles/life";
    });
    const lifeImgA = pageA.locator("img.life-scene");
    await expect(lifeImgA).toBeVisible();
    expect(
      await lifeImgA.evaluate((el: HTMLImageElement) => el.naturalWidth > 0),
    ).toBe(true);
    expect(
      pageARequestsAfterSwitch.some((u) =>
        u.includes(`/_ui/releases/${BUILD_A}/life-evening.png`),
      ),
    ).toBe(true);
    expect(
      pageARequestsAfterSwitch.some((u) =>
        u.includes(`/_ui/releases/${BUILD_B}/`),
      ),
    ).toBe(false);

    // 6. Newly opened Page B loads version B exclusively
    const pageB = await context.newPage();
    await pageB.clock.setFixedTime(new Date("2026-10-31T06:00:00Z"));
    const pageBRequests: string[] = [];
    pageB.on("request", (req) => pageBRequests.push(req.url()));
    await pageB.goto(`${server.baseUrl}/#/roles/room`);
    await expect(pageB.locator("html")).toHaveAttribute(
      "data-ui-build-id",
      BUILD_B,
    );
    await expect(pageB.locator("canvas")).toHaveAttribute("data-ready", "true");
    expect(
      pageBRequests.some((u) =>
        u.includes(`/_ui/releases/${BUILD_B}/assets/RoomPage-`),
      ),
    ).toBe(true);
    expect(
      pageBRequests.some((u) =>
        u.includes(
          `/_ui/releases/${BUILD_B}/room-illustration-assets/room-clean-afternoon-v1.png`,
        ),
      ),
    ).toBe(true);
    expect(
      pageBRequests.some((u) => u.includes(`/_ui/releases/${BUILD_A}/`)),
    ).toBe(false);

    // 7. Online rollback B -> A via same tool without restarting server
    const rbReceipt = runReleaseCli([
      "rollback",
      "--ui-root",
      uiRoot,
      "--expected-current",
      BUILD_B,
      "--verify-url",
      server.baseUrl,
    ]);
    expect(rbReceipt.pointer_switched).toBe(true);
    expect(rbReceipt.http_verified).toBe(true);
    expect(rbReceipt.rolled_back_to).toBe(BUILD_A);
    expect(rbReceipt.activation_sequence).toBe(3);

    // Page A notice disappears automatically when server is back on A
    await pageA.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(pageA.getByLabel("界面版本更新提示")).toHaveCount(0);

    // Reloading Page B after rollback loads A cleanly
    const pageBReloadRequests: string[] = [];
    pageB.on("request", (req) => pageBReloadRequests.push(req.url()));
    await pageB.reload();
    await expect(pageB.locator("html")).toHaveAttribute(
      "data-ui-build-id",
      BUILD_A,
    );
    await expect(pageB.locator("canvas")).toHaveAttribute("data-ready", "true");
    expect(
      pageBReloadRequests.some((u) => u.includes(`/_ui/releases/${BUILD_B}/`)),
    ).toBe(false);

    const proof2 = await (
      await fetch(`${server.baseUrl}/_test/runtime-proof`)
    ).json();
    expect(proof2.pid).toBe(proof0.pid);
    expect(proof2.instance_id).toBe(proof0.instance_id);
    expect(proof2.data_generation).toBe(proof0.data_generation);
    expect(proof2.background_ticks).toBeGreaterThan(proof1.background_ticks);
    expect(proof2.recover_writes).toBe(1);

    await pageB.close();
  } finally {
    await server.stop();
    if (path.dirname(path.resolve(uiRoot)) !== path.resolve(VERIFY_ROOT))
      throw new Error("UI test data escaped its owned root");
    fs.rmSync(uiRoot, { recursive: true, force: true });
  }
});
