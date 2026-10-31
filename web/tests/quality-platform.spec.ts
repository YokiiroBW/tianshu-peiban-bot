import { test, expect, type Page, type Route } from "@playwright/test";

const day = "2026-10-03";
const at = 1790985600;
async function reply(route: Route, body: unknown) {
  await route.fulfill({ json: body });
}
async function loggedSession(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    reply(route, {
      authenticated: true,
      csrf: "quality-fixture",
      username: "quality-owner",
      conversations: [],
      dialogue: { available: false, code: "not_configured", model: "" },
    }),
  );
}

test("blocked browser storage keeps memory selection and two server logouts usable", async ({
  page,
}) => {
  await page.addInitScript(() => {
    for (const name of ["getItem", "setItem", "removeItem"])
      Object.defineProperty(Storage.prototype, name, {
        value: () => {
          throw new DOMException("storage denied", "SecurityError");
        },
      });
  });
  let authenticated = true;
  let logouts = 0;
  const failures: string[] = [];
  page.on("pageerror", (cause) => failures.push(cause.message));
  await page.route("**/api/v1/session", (route) =>
    reply(route, {
      authenticated,
      csrf: "quality-fixture",
      username: "quality-owner",
      conversations: [],
      dialogue: { available: false, code: "not_configured", model: "" },
    }),
  );
  await page.route("**/api/v1/logout", (route) => {
    logouts++;
    authenticated = false;
    return reply(route, { authenticated: false });
  });
  await page.route("**/api/v1/login", (route) => {
    authenticated = true;
    return reply(route, { authenticated: true, csrf: "quality-fixture" });
  });
  await page.route("**/api/v1/memory/*", (route) => {
    if (route.request().url().endsWith("/state"))
      return reply(route, {
        available: true,
        code: "ready",
        actor_id: "actor:a",
        peer: { configured: true, code: "ok", verified_at: null },
        roles: ["a", "b"].map((id) => ({
          id: `actor:${id}`,
          label: id,
          version: 0,
          available: true,
          reason: null,
        })),
      });
    if (route.request().url().endsWith("/records"))
      return reply(route, {
        items: [],
        next_cursor: null,
        verified_at: "2026-10-03T00:00:00Z",
        scope_version: 1,
      });
    return reply(route, {
      memory_group_count:
        route.request().postDataJSON().role_id === "actor:a" ? 1 : 2,
      counts_truncated: false,
      verified_at: "2026-10-03T00:00:00Z",
      scope_version: 1,
    });
  });
  for (let turn = 1; turn <= 2; turn++) {
    if (turn === 2) {
      await page.getByLabel("管理员账号").fill("quality-owner");
      await page
        .getByLabel("密码", { exact: true })
        .fill("quality-password-0001");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "退出登录" }),
      ).toBeVisible();
    }
    await page.goto("/#/people");
    await expect(page.getByLabel("查看哪位角色的记忆")).toHaveValue("actor:a");
    await page.getByLabel("查看哪位角色的记忆").selectOption("actor:b");
    await expect(page.getByLabel("查看哪位角色的记忆")).toHaveValue("actor:b");
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(page.getByLabel("管理员账号")).toBeVisible();
    await expect.poll(() => logouts).toBe(turn);
  }
  expect(failures).toEqual([]);
});

test("enabled role can turn dialogue off without a model or pausing its life", async ({
  page,
}) => {
  await loggedSession(page);
  const roles: Record<string, unknown>[] = [];
  let applied: Record<string, unknown> | null = null;
  await page.route("**/api/v1/roles/*", (route) => {
    if (route.request().url().endsWith("/view"))
      return reply(route, {
        roles,
        legacy_roles: [],
        profiles: [],
        providers: [],
        default_available: false,
        capabilities: ["dialogue", "memory.read", "memory.write"],
      });
    const body = route.request().postDataJSON();
    applied = body;
    const role = {
      ...body,
      actor_id: "actor:new",
      version: 1,
      state: "active",
      stage: "complete",
      error_code: null,
    };
    roles.push(role);
    return reply(route, { role });
  });
  await page.goto("/#/roles");
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("独立生活角色");
  await page.getByLabel("启用角色", { exact: true }).check();
  await page.getByLabel("允许对话", { exact: true }).uncheck();
  await page.getByLabel("使用本角色记忆").uncheck();
  await page.getByLabel("记住新的经历").uncheck();
  await page.getByRole("button", { name: "应用设置", exact: true }).click();
  await expect.poll(() => applied).not.toBeNull();
  expect(applied).toMatchObject({
    enabled: true,
    capabilities: [],
    provider_id: null,
    provider_revision: null,
  });
  await expect(page.getByLabel("启用角色", { exact: true })).toBeChecked();
  await expect(page.getByLabel("允许对话", { exact: true })).not.toBeChecked();
});

test("task poll resets its cursor across a burst and removes changed status matches", async ({
  page,
}) => {
  await loggedSession(page);
  let records = Array.from({ length: 15 }, (_, index) => index + 1);
  let moved = false;
  const cursors: (string | null)[] = [];
  await page.route("**/api/v1/tasks/view", (route) => {
    const body = route.request().postDataJSON();
    cursors.push(body.cursor ?? null);
    const available = [...records]
      .reverse()
      .filter((id) => body.status !== "accepted" || !(moved && id === 30));
    const offset = Number(body.cursor ?? 0);
    const ids = available.slice(offset, offset + 10);
    return reply(route, {
      generated_at: "2026-10-03T00:00:00Z",
      filters: body,
      statuses: ["accepted", "observed"],
      sources: [
        {
          id: "platform.home",
          kind: "device.control",
          connected: true,
          state: "available",
          code: "ok",
          records: records.length,
          cancel_code: "executor_does_not_cancel",
        },
      ],
      items: ids.map((id) => ({
        task_id: `home-control:${id}`,
        source: "platform.home",
        kind: "device.control",
        title: `操作 ${id}`,
        target: `设备 ${id}`,
        status: moved && id === 30 ? "observed" : "accepted",
        stage: "accepted",
        code: "ok",
        created_at: "2026-10-03T00:00:00Z",
        updated_at: "2026-10-03T00:00:00Z",
        settled: false,
        pending: true,
        attention: false,
        source_state: "available",
        cancel: { supported: false, code: "executor_does_not_cancel" },
        evidence: {},
        module: { page: null },
      })),
      page: {
        size: 10,
        returned: ids.length,
        has_more: available.length > offset + 10,
        sort: "recorded_desc",
        next_cursor:
          available.length > offset + 10 ? String(offset + 10) : null,
      },
    });
  });
  await page.goto("/#/system/tasks");
  await expect(page.locator("article.tasks-item")).toHaveCount(10);
  records = Array.from({ length: 30 }, (_, index) => index + 1);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator("article.tasks-item").first()).toContainText(
    "操作 30",
  );
  await page.getByRole("button", { name: /加载更多/ }).click();
  await expect(page.locator("article.tasks-item")).toHaveCount(20);
  await page.getByRole("button", { name: /加载更多/ }).click();
  await expect(page.locator("article.tasks-item")).toHaveCount(30);
  expect(cursors.slice(-2)).toEqual(["10", "20"]);
  await page.locator(".tasks-filters select").first().selectOption("accepted");
  await expect(page.locator("article.tasks-item")).toHaveCount(10);
  moved = true;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator("article.tasks-item").first()).toContainText(
    "操作 29",
  );
  await expect(
    page.locator("article.tasks-item").filter({ hasText: "操作 30" }),
  ).toHaveCount(0);
});

test("life plan and history page through the same role and discard a late former role", async ({
  page,
}, testInfo) => {
  await loggedSession(page);
  await page.route("**/api/v1/weather/current", (route) =>
    reply(route, {
      revision: 0,
      can_manage: false,
      configured: false,
      credential_configured: false,
      host: null,
      location: null,
      server_time: at,
      weather: null,
      fetched_at: null,
      stale: false,
      code: "weather_not_configured",
    }),
  );
  let delayA = false;
  let releaseA: (() => void) | undefined;
  let enteredA = false;
  const timelineRequests: Record<string, unknown>[] = [];
  const retryRequests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/roles/view", (route) =>
    reply(route, {
      roles: ["a", "b"].map((id) => ({
        actor_id: `actor:${id}`,
        name: `actor:${id}`,
        state: "enabled",
      })),
      legacy_roles: [],
    }),
  );
  await page.route("**/api/v1/life/*", async (route) => {
    const name = route.request().url().split("/").at(-1);
    const body = route.request().postDataJSON();
    const actor = body.actor_id ?? "actor:a";
    if (name === "retry") {
      retryRequests.push(body);
      return reply(route, {
        schema_version: 1,
        actor_id: actor,
        plan_id: `plan:${actor}`,
        plan_version: 2,
        state: "queued",
      });
    }
    if (name === "state")
      return reply(route, {
        available: true,
        code: "ready",
        can_retry: true,
        peer: { configured: true, code: "ok", verified_at: null },
      });
    if (name === "actors")
      return reply(route, {
        schema_version: 1,
        fictional: true,
        items: ["a", "b"].map((id) => ({
          actor_id: `actor:${id}`,
          actor_version: 1,
          world_id: "world",
          room_id: "room",
        })),
        next_after_actor_id: null,
      });
    if (name === "snapshot")
      return reply(route, {
        schema_version: 1,
        fictional: true,
        actor_id: actor,
        actor_version: 1,
        world_id: "world",
        room_id: "room",
        timezone: "Asia/Shanghai",
        activity: "reading",
        mood: "平静",
        outfit_ref: null,
        changed_at: at,
        observed_at: at,
        state_basis: "last_persisted",
      });
    if (name === "diaries")
      return reply(route, {
        schema_version: 1,
        fictional: true,
        actor_id: actor,
        items: [],
        next_after: null,
      });
    if (name === "today") {
      if (actor === "actor:a" && delayA) {
        enteredA = true;
        await new Promise<void>((resolve) => {
          releaseA = resolve;
        });
      }
      return reply(route, {
        schema_version: 1,
        fictional: true,
        actor_id: actor,
        day,
        timezone: "Asia/Shanghai",
        enabled: true,
        observed_at: at,
        state_basis: "last_persisted",
        plan: {
          plan_id: `plan:${actor}`,
          version: 1,
          state: "active",
          generated_by: "baseline",
          generation_state: "unavailable",
          current_phase_id: "phase:1",
          entries: [
            {
              phase_id: "phase:1",
              minute: 540,
              activity: `${actor} 阅读`,
              detail: null,
              state: "current",
              generation_state: "unavailable",
            },
            {
              phase_id: "phase:2",
              minute: 720,
              activity: "午后散步",
              detail: null,
              state: "planned",
              generation_state: "queued",
            },
          ],
        },
      });
    }
    if (name === "timeline") {
      timelineRequests.push(body);
      return reply(route, {
        schema_version: 1,
        fictional: true,
        actor_id: actor,
        day: body.day,
        state_basis: "last_persisted",
        items: [
          {
            event_id: "event:1",
            known_id: `${actor}:${body.after ? 1 : 2}`,
            position: body.after ? at - 1 : at,
            kind: "activity",
            summary: `${actor} ${body.after ? "清晨经历" : "上午经历"}`,
            occurred_at: at,
            learned_at: at,
            via: "self",
            phase_id: "phase:1",
            plan_id: `plan:${actor}`,
            generated_by: "baseline",
          },
        ],
        next_after: body.after
          ? null
          : { position: at, known_id: `${actor}:2` },
      });
    }
    throw new Error(`unexpected life request ${name}`);
  });
  await page.goto("/#/roles/life");
  await expect(
    page
      .locator(".life-timeline .life-summary")
      .filter({ hasText: "actor:a 上午经历" }),
  ).toBeVisible();
  await expect(page.locator(".life-plan")).toContainText("尚未发生");
  await expect(
    page.getByRole("button", { name: "重试当前阶段内容" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "重试今日安排内容" }).click();
  await expect.poll(() => retryRequests.length).toBe(1);
  expect(retryRequests[0]).toMatchObject({
    actor_id: "actor:a",
    phase_id: null,
    expected_revision: 1,
  });
  await page.getByRole("button", { name: "继续读取经历" }).click();
  await expect(
    page
      .locator(".life-timeline .life-summary")
      .filter({ hasText: "actor:a 清晨经历" }),
  ).toBeVisible();
  expect(timelineRequests.at(-1)?.after).toEqual({
    position: at,
    known_id: "actor:a:2",
  });
  delayA = true;
  await page.getByRole("button", { name: "刷新日常", exact: true }).click();
  await expect.poll(() => enteredA).toBe(true);
  await page.getByLabel("当前角色", { exact: true }).selectOption("actor:b");
  await expect(
    page
      .locator(".life-timeline .life-summary")
      .filter({ hasText: "actor:b 上午经历" }),
  ).toBeVisible();
  releaseA?.();
  await expect(
    page
      .locator(".life-timeline .life-summary")
      .filter({ hasText: "actor:a 上午经历" }),
  ).toHaveCount(0);
  await expect(page.locator(".life-now")).toContainText("actor:b 阅读");
  await page.screenshot({
    path: testInfo.outputPath("life-daily.png"),
    fullPage: true,
  });
});
