import { test, expect, type Page } from "@playwright/test";
import { createPeopleAccount, profiles, portrait } from "./fixtures/people";
import { sharedProvenance } from "./fixtures/memory-records";

async function fixture(
  page: Page,
  options: { locked?: boolean; memoryFailure?: boolean } = {},
) {
  let unlocked = !options.locked;
  let discoveredReads = 0;
  let memoryRevoked = false;
  let overviewReads = 0;
  const writes: Record<string, unknown>[] = [];
  const account = createPeopleAccount();
  const roles = [
    { id: "role:a", label: "橙汐", version: 1, available: true, reason: null },
    { id: "role:b", label: "雪色", version: 2, available: true, reason: null },
  ];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/",
      "",
    );
    const body =
      route.request().method() === "POST" ? route.request().postDataJSON() : {};
    let result: unknown;
    if (path === "session")
      result = {
        authenticated: true,
        csrf: "people-fixture",
        username: "YokiiroBW",
        conversations: [],
        dialogue: { available: false, code: "not_configured", model: "" },
      };
    else if (path === "qq-admin/view") result = { version: 0, grants: [] };
    else if (path === "memory/state") {
      if (options.memoryFailure)
        return route.fulfill({
          status: 503,
          json: { code: "memory_not_configured" },
        });
      result = { available: true, code: "ready", actor_id: "role:a", roles };
    } else if (path === "bot-observation/groups") result = { actors: roles };
    else if (path === "qq-admin/profiles")
      result = profiles(Boolean(body.after));
    else if (path === "bot-observation/view")
      result = { available: true, unlocked, connections: [account] };
    else if (path === "bots/unlock") {
      unlocked = true;
      result = { unlocked: true };
    } else if (path === "bot-observation/discovered") {
      discoveredReads++;
      if (!unlocked)
        return route.fulfill({
          status: 403,
          json: { code: "management_required" },
        });
      result = {
        items: [
          {
            conversation: "private:10002",
            author: "10002",
            count: 1,
            last_at: 1791104400,
            decision: {
              observe: true,
              reply_permitted: false,
              reply_triggered: false,
            },
          },
          {
            conversation: "group:80001",
            author: "10001",
            count: 4,
            last_at: 1791104300,
            decision: {
              observe: true,
              reply_permitted: false,
              reply_triggered: false,
            },
          },
        ],
        next_cursor: null,
      };
    } else if (path === "bot-observation/archive")
      result = {
        memory_state: "available",
        backlog: {},
        items: [],
        archive_items: [
          {
            source_ref: "message:one",
            author: "10002",
            text: "hi",
            content_state: "available",
            sent_at: "2026-10-04T10:00:00Z",
          },
        ],
        archive_next_cursor: null,
      };
    else if (path === "memory/overview") {
      overviewReads++;
      if (memoryRevoked)
        return route.fulfill({
          status: 403,
          json: { code: "upstream_forbidden" },
        });
      result = { scope_version: 1 };
    } else if (path === "memory/records") result = portrait(body.role_id);
    else if (path === "bot-observation/private-access") {
      writes.push(body);
      account.revision++;
      account.private_policy = {
        observe: true,
        mode: "whitelist",
        list: body.enabled ? [body.qq_id] : [],
        actor_id: body.actor_id || account.private_policy.actor_id,
      };
      result = { connection: account };
    } else
      return route.fulfill({ status: 404, json: { code: "not_configured" } });
    return route.fulfill({ json: result });
  });
  await page.goto("/#/people");
  return {
    writes,
    errors,
    discoveredReads: () => discoveredReads,
    setMemoryRevoked: (value: boolean) => {
      memoryRevoked = value;
    },
    overviewReads: () => overviewReads,
  };
}

test("shared portrait displays new provenance and retains fact conditions without private excerpts", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.route("**/api/v1/memory/records", (route) => {
    const result = portrait("role:b");
    return route.fulfill({
      json: {
        ...result,
        items: result.items.map((item) => ({
          ...item,
          provenance: sharedProvenance,
          units: item.units.map((unit) => ({
            ...unit,
            conditions: "仅在周末",
            record_version: 2,
          })),
        })),
      },
    });
  });
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  const record = page.locator(".people-memory-records .memory-records > li");
  await expect(record).toContainText("已更新 1 条既有记忆");
  await page.getByRole("button", { name: "人物画像", exact: true }).click();
  await expect(record).toHaveCount(1);
  await record.getByText("查看整理依据", { exact: true }).click();
  await expect(record).toContainText("本条未附可展示的原话摘录。");
  await expect(record).toContainText("关联了 2 条已有记忆");
  await record.getByRole("button", { name: "查看事实的适用条件" }).click();
  await expect(page.getByRole("dialog")).toContainText("仅在周末");
  await page.keyboard.press("Escape");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("shared-memory.png"),
    fullPage: true,
  });
  expect(state.errors).toEqual([]);
});

test("people directory keeps observed hi users and separates shared portrait from identity", async ({
  page,
}, testInfo) => {
  const state = await fixture(page);
  const directory = page.getByRole("complementary", { name: "用户目录" });
  await expect(
    directory.getByText("QQ 10002", { exact: true }).last(),
  ).toBeVisible();
  await directory.getByRole("button").filter({ hasText: "QQ 10002" }).click();
  await expect(
    page.getByRole("heading", { name: "QQ 10002", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "最近互动", exact: true }).click();
  await expect(page.getByText("hi", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  await expect(
    page.getByText("人物身份尚未确认；收到消息不代表已经建立画像。"),
  ).toBeVisible();
  await directory.getByRole("button").filter({ hasText: "QQ 10001" }).click();
  await expect(page.getByText("群里的小雨", { exact: false })).toBeVisible();
  await expect(
    page.locator(".people-detail").getByText("person:one"),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("people-overview.png"),
    fullPage: true,
  });
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await page.screenshot({
    path: testInfo.outputPath("people-overview-dark.png"),
    fullPage: true,
  });
  await page.evaluate(() => (document.documentElement.dataset.theme = "light"));
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "还没有形成共享画像" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "查看角色", exact: true })
    .selectOption("role:b");
  await expect(page.locator(".people-detail")).toHaveCount(0);
  await directory.getByRole("button").filter({ hasText: "QQ 10001" }).click();
  await expect(
    page.getByRole("tab", { name: "概览", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  await expect(page.getByText("小雨喜欢在傍晚读书。")).toBeVisible();
  await page.getByRole("button", { name: "加载更多用户", exact: true }).click();
  await expect(directory.getByText("QQ 10003", { exact: true })).toBeVisible();
  await page
    .getByRole("textbox", { name: "搜索已加载用户" })
    .fill("群里的小雨");
  await expect(directory.getByText("QQ 10001", { exact: true })).toBeVisible();
  await expect(directory.getByText("QQ 10002", { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(state.errors).toEqual([]);
});

test("people observation unlock reveals contacts and targets only selected private reply permission", async ({
  page,
}) => {
  const state = await fixture(page, { locked: true });
  await expect(
    page.getByRole("button", { name: "解锁用户会话" }),
  ).toBeVisible();
  expect(state.discoveredReads()).toBe(0);
  await page
    .getByRole("textbox", { name: "管理员密码", exact: true })
    .fill("synthetic-only");
  await page.getByRole("button", { name: "解锁用户会话" }).click();
  const directory = page.getByRole("complementary", { name: "用户目录" });
  await directory.getByRole("button").filter({ hasText: "QQ 10002" }).click();
  await page.getByRole("tab", { name: "回复权限", exact: true }).click();
  await page.getByRole("button", { name: "回复权限设置", exact: true }).click();
  await page
    .getByRole("combobox", { name: "家里的机器人默认回复角色", exact: true })
    .selectOption("role:a");
  await page
    .getByRole("button", { name: "允许回复此用户", exact: true })
    .click();
  await expect(page.getByText("这位用户的私聊回复权限已保存。")).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]).toMatchObject({
    id: "bot:one",
    qq_id: "10002",
    expected_revision: 1,
    actor_id: "role:a",
    enabled: true,
  });
  expect(state.writes[0]).not.toHaveProperty("group_policy");
  await page
    .getByRole("button", { name: "停止回复此用户", exact: true })
    .click();
  expect(state.writes[1]).toMatchObject({
    qq_id: "10002",
    expected_revision: 2,
    actor_id: null,
    enabled: false,
  });
  expect(state.errors).toEqual([]);
});

test("QQ identity directory remains readable when memory service fails", async ({
  page,
}) => {
  const state = await fixture(page, { memoryFailure: true });
  const directory = page.getByRole("complementary", { name: "用户目录" });
  await expect(directory.getByText("QQ 10001", { exact: true })).toBeVisible();
  await directory.getByRole("button").filter({ hasText: "QQ 10001" }).click();
  await expect(page.getByText("群里的小雨", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  await expect(page.getByText("当前角色的记忆读取尚不可用。")).toBeVisible();
  await expect(page.getByText("还没有形成共享画像")).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test("portrait scope revalidation hides revoked records and rechecks when visible", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-10-04T10:00:00Z") });
  const state = await fixture(page);
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  await page
    .getByRole("combobox", { name: "查看角色", exact: true })
    .selectOption("role:b");
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  await page.getByRole("tab", { name: "画像与记忆", exact: true }).click();
  await expect(page.getByText("小雨喜欢在傍晚读书。")).toBeVisible();
  state.setMemoryRevoked(true);
  await page.clock.fastForward(15001);
  await expect(page.getByText("小雨喜欢在傍晚读书。")).toHaveCount(0);
  await expect(
    page.getByRole("alert").filter({ hasText: "upstream_forbidden" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("complementary", { name: "用户目录" })
      .getByText("QQ 10001", { exact: true }),
  ).toBeVisible();
  const previous = state.overviewReads();
  state.setMemoryRevoked(false);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.getByText("小雨喜欢在傍晚读书。")).toBeVisible();
  expect(state.overviewReads()).toBeGreaterThan(previous);
  expect(state.errors).toEqual([]);
});

test("account link resumes only through current status and retries revoke with its actual version", async ({
  page,
}) => {
  const fixtureState = await fixture(page);
  const writes: Record<string, any>[] = [];
  let consented = false,
    linked = false,
    revoked = false;
  const association = () => ({
    association_id: "association:fixture",
    version: revoked ? 5 : 4,
    state: revoked ? "revoked" : "linked",
    scopes: [],
  });
  await page.route("**/api/v1/memory-links/*", async (route) => {
    const name = route.request().url().split("/").at(-1);
    const body = route.request().postDataJSON();
    let result;
    if (name === "begin")
      result = {
        challenge_id: "challenge:fixture",
        state: "pending",
        expires_at: "2030-01-01T00:00:00Z",
        consent_sentence: "仅用于合成界面测试的本人确认句",
      };
    else if (name === "status")
      result = {
        challenge_id: "challenge:fixture",
        state: linked
          ? revoked
            ? "revoked"
            : "linked"
          : consented
            ? "consented"
            : "pending",
        association: linked ? association() : null,
      };
    else if (name === "complete") {
      linked = true;
      result = association();
    } else {
      writes.push(body);
      if (writes.length === 1)
        return route.fulfill({
          status: 503,
          json: { code: "dependency_unavailable", execution_state: "unknown" },
        });
      revoked = true;
      result = association();
    }
    return route.fulfill({ json: result });
  });
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10002" })
    .click();
  await page.getByRole("tab", { name: "账号关联", exact: true }).click();
  await page.getByRole("button", { name: "账号关联设置", exact: true }).click();
  await page.getByRole("button", { name: "开始账号关联", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "完成账号关联", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".life-consent-sentence")).toContainText(
    "本人确认句",
  );
  consented = true;
  await page.getByRole("button", { name: "查询确认状态", exact: true }).click();
  await page.getByRole("button", { name: "完成账号关联", exact: true }).click();
  await expect(
    page.getByText("关联已由记忆服务确认", { exact: false }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "概览", exact: true }).click();
  await page.getByRole("tab", { name: "账号关联", exact: true }).click();
  await page.getByRole("button", { name: "账号关联设置", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "撤销关联读取", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "撤销关联读取", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("alert")
      .filter({ hasText: "写入结果尚未确认" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "撤销关联读取", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "撤销关联读取", exact: true }),
  ).toHaveCount(0);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
  expect(writes[1]).toMatchObject({
    actor_id: "role:a",
    challenge_id: "challenge:fixture",
    expected_revision: 4,
  });
  expect(fixtureState.errors).toEqual([]);
});

test("proactive preferences preserve failed operation identity and show partial or unknown delivery accurately", async ({
  page,
}) => {
  const fixtureState = await fixture(page);
  const writes: Record<string, any>[] = [];
  let subscription: Record<string, any> | null = null;
  await page.route("**/api/v1/people-life/*", async (route) => {
    const name = route.request().url().split("/").at(-1);
    const body = route.request().postDataJSON();
    let value;
    if (name === "view")
      value = {
        control: {
          schema_version: 2,
          request_id: "control:fixture",
          actor_id: "role:a",
          subscriptions: subscription ? [subscription] : [],
          motives: [],
        },
        delivery: {
          available: true,
          items: [
            {
              expression_id: "delivery:partial",
              kind: "text",
              state: "partial",
              final: true,
              created_at: 1791104400,
              sent_segments: 1,
              total_segments: 2,
            },
            {
              expression_id: "delivery:unknown",
              kind: "text",
              state: "unknown",
              final: true,
              created_at: 1791104401,
              sent_segments: 0,
              total_segments: 2,
            },
          ],
        },
        private_life: { available: false, code: "scope_required" },
      };
    else {
      writes.push(body);
      if (writes.length === 1)
        return route.fulfill({
          status: 503,
          json: { code: "dependency_unavailable", execution_state: "unknown" },
        });
      subscription =
        name === "subscription"
          ? {
              ...body.value,
              id: "subscription:fixture",
              state: "active",
              version: 1,
            }
          : { ...subscription, state: body.value.state, version: 2 };
      value = {
        schema_version: 2,
        request_id: body.client_request_id,
        actor_id: body.actor_id,
        operation:
          name === "subscription"
            ? "proactive.subscription"
            : "proactive.subscription.state",
        result: {
          object_id: subscription!.id,
          version: subscription!.version,
          state: subscription!.state,
          replayed: false,
        },
      };
    }
    return route.fulfill({ json: value });
  });
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10002" })
    .click();
  await page.getByRole("tab", { name: "主动偏好", exact: true }).click();
  await expect(
    page.getByText("结果未确认，不能视作失败或再次发送。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/已送 1\/2 段/)).toBeVisible();
  await page.getByRole("button", { name: "登记主动偏好", exact: true }).click();
  await page.getByLabel("每天次数", { exact: true }).fill("5");
  await page.getByRole("button", { name: "保存主动偏好", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("alert")
      .filter({ hasText: "写入结果尚未确认" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存主动偏好", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "暂停主动联系", exact: true }),
  ).toBeVisible();
  expect(writes[1]).toEqual(writes[0]);
  expect(writes[0]).toMatchObject({
    actor_id: "role:a",
    qq_id: "10002",
    conversation: "private:10002",
    expected_revision: 0,
    value: { daily_quota: 5 },
  });
  expect(writes[0].value).not.toHaveProperty("person_id");
  expect(writes[0].value).not.toHaveProperty("scope");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "暂停主动联系", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "恢复主动联系", exact: true }),
  ).toBeVisible();
  expect(writes[2]).toMatchObject({
    expected_revision: 1,
    value: { id: "subscription:fixture", state: "paused" },
  });
  expect(fixtureState.errors).toEqual([]);
});

test("manual proactive contact preserves the original source and deadline after unknown and completed responses", async ({
  page,
}) => {
  const fixtureState = await fixture(page);
  const writes: Record<string, any>[] = [];
  let confirmed = false;
  let expression = "expression:confirmed";
  await page.route("**/api/v1/bot-observation/discovered", (route) => {
    const more = Boolean(route.request().postDataJSON().cursor);
    return route.fulfill({
      json: {
        items: [
          {
            conversation: more ? "private:10002" : "group:80001",
            author: "10002",
            count: 1,
            last_at: 1791104400,
            decision: {
              observe: true,
              reply_permitted: true,
              reply_triggered: false,
            },
          },
        ],
        next_cursor: more ? null : "private-source:next",
      },
    });
  });
  await page.route("**/api/v1/people-life/*", async (route) => {
    const body = route.request().postDataJSON();
    if (route.request().url().endsWith("/view"))
      return route.fulfill({
        json: {
          control: {
            subscriptions: [
              {
                id: "subscription:fixture",
                version: 1,
                state: "active",
                timezone_name: "Asia/Shanghai",
                quiet_start: "22:00",
                quiet_end: "08:00",
                cooldown_seconds: 3600,
                daily_quota: 1,
                unanswered_limit: 1,
                expiry_seconds: 3600,
              },
            ],
            motives: [],
          },
          delivery: {
            available: true,
            items: [
              {
                expression_id: expression,
                kind: "response",
                state: confirmed ? "sent" : "unknown",
                final: true,
                created_at: 1791104400,
                sent_segments: confirmed ? 1 : 0,
                total_segments: 1,
              },
            ],
          },
          private_life: { available: false, code: "scope_required" },
        },
      });
    writes.push(body);
    expression = "expression:new-reply-after-unknown";
    return route.fulfill({
      json: {
        request_id: body.client_request_id,
        actor_id: body.actor_id,
        operation: "proactive.motive",
        result: {
          object_id: "motive:fixture",
          version: 1,
          state: writes.length === 1 ? "unknown" : "active",
          replayed: writes.length > 1,
        },
      },
    });
  });
  await page.reload();
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10002" })
    .click();
  await page.getByRole("tab", { name: "主动偏好", exact: true }).click();
  await expect(
    page.getByText("尚无已登记的真实私聊来源", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "加载更多用户", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "测试主动联系", exact: true }),
  ).toBeDisabled();
  confirmed = true;
  await page
    .getByRole("button", { name: "刷新偏好与送达", exact: true })
    .click();
  await page.getByRole("button", { name: "测试主动联系", exact: true }).click();
  const check = page.getByRole("button", {
    name: "检查本次主动联系",
    exact: true,
  });
  await expect(check).toBeEnabled();
  await check.click();
  await expect(check).toBeEnabled();
  await check.click();
  await expect(check).toBeEnabled();
  expect(writes).toHaveLength(3);
  expect(writes[1]).toEqual(writes[0]);
  expect(writes[2]).toEqual(writes[0]);
  expect(writes[0]).toMatchObject({
    actor_id: "role:a",
    qq_id: "10002",
    conversation: "private:10002",
    expected_revision: 0,
    value: {
      subscription_id: "subscription:fixture",
      expression_id: "expression:confirmed",
    },
  });
  expect(writes[0].value.expires_at - writes[0].value.due_at).toBe(3600);
  expect(fixtureState.errors).toEqual([]);
});

test("user administrator switch persists, isolates users and refreshes conflicts", async ({
  page,
}, testInfo) => {
  await fixture(page);
  let view = { version: 2, grants: [] as Record<string, unknown>[] };
  let fail = false;
  const writes: Record<string, unknown>[] = [];
  await page.route("**/api/v1/qq-admin/*", async (route) => {
    const action = route.request().url().split("/").at(-1);
    if (action === "profiles") return route.fallback();
    if (action !== "view") {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (fail) {
        fail = false;
        view.version++;
        return route.fulfill({
          status: 409,
          json: { code: "version_conflict" },
        });
      }
      expect(body.expected_revision).toBe(view.version);
      view = {
        version: view.version + 1,
        grants: action === "grant" ? [body] : [],
      };
    }
    return route.fulfill({ json: view });
  });
  await page.reload();
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  const toggle = page.getByRole("switch", { name: "设为管理员" });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  await expect(toggle).toBeChecked();
  expect(writes[0]).toMatchObject({
    qq_id: "10001",
    actor_ids: [],
    conversations: [],
    capabilities: ["identity.explain"],
  });
  await page.reload();
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  await expect(toggle).toBeChecked();
  await page.screenshot({
    path: testInfo.outputPath("user-admin.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const directory = page.getByRole("complementary", { name: "用户目录" });
  await directory.getByRole("button").filter({ hasText: "QQ 10002" }).click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  await directory.getByRole("button").filter({ hasText: "QQ 10001" }).click();
  await expect(toggle).toBeChecked();
  fail = true;
  await toggle.click();
  await expect(
    page.getByText("管理员设置已变化，请刷新状态后重试。"),
  ).toBeVisible();
  await expect(toggle).toBeDisabled();
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  await page.goto("/#/people");
  await expect(directory).toBeVisible();
  await page.goto("/#/settings/connections");
  await expect(
    page.getByRole("link", { name: "QQ 管理身份", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "角色技能", exact: true }),
  ).toHaveAttribute("href", "#/settings/skills");
});

test("admin status errors remain unknown and scoped grants stay scoped", async ({
  page,
}) => {
  await fixture(page);
  await page.route("**/api/v1/qq-admin/view", (route) =>
    route.fulfill({ status: 503, json: { code: "dependency_unavailable" } }),
  );
  await page.reload();
  await page
    .getByRole("complementary", { name: "用户目录" })
    .getByRole("button")
    .filter({ hasText: "QQ 10001" })
    .click();
  const toggle = page.getByRole("switch", { name: "设为管理员" });
  await expect(page.getByText("状态未确认", { exact: true })).toBeVisible();
  await expect(toggle).toBeDisabled();
  await page.route("**/api/v1/qq-admin/view", (route) =>
    route.fulfill({
      json: {
        version: 5,
        grants: [
          {
            qq_id: "10001",
            actor_ids: ["actor:a"],
            conversations: ["group:123"],
            capabilities: ["identity.explain"],
          },
        ],
      },
    }),
  );
  await page.getByRole("button", { name: "刷新状态", exact: true }).click();
  await expect(toggle).toBeChecked();
  await expect(
    page.getByText("管理员 · 限定范围", { exact: true }),
  ).toBeVisible();
  await page.route("**/api/v1/qq-admin/revoke", (route) =>
    route.fulfill({ status: 403, json: { code: "forbidden" } }),
  );
  await toggle.click();
  await expect(page.getByText("当前账号不能修改管理员设置。")).toBeVisible();
  await expect(toggle).toBeDisabled();
});
