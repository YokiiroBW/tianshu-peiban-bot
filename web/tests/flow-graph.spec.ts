import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type {
  Relationships,
  SafeEvent,
} from "../src/features/flow-graph/types";

const correlation = "a".repeat(32);
function topology(): Relationships {
  return {
    generated_at: "2026-10-28T05:00:00Z",
    accounts: [
      {
        id: "account-a",
        label: "合成测试账号",
        adapter: "onebot",
        account_id: "10001",
        instance_id: "synthetic-sdk",
        states: ["ready"],
        sources: ["observation"],
      },
    ],
    roles: [
      {
        id: "actor:a",
        label: "星缘",
        state: "active",
        enabled: true,
        assignable: true,
        profile_id: "profile:synthetic-a",
        profile_version: 2,
        capabilities: ["dialogue", "memory.read"],
        source: "runtime",
      },
      {
        id: "actor:b",
        label: "月白",
        state: "active",
        enabled: true,
        assignable: true,
        profile_id: null,
        profile_version: null,
        capabilities: [],
        source: "runtime",
      },
    ],
    conversations: Array.from({ length: 16 }, (_, i) => ({
      id: `window-${i}`,
      label: `${i < 2 ? "私聊" : "群聊"} ${20001 + i}`,
      conversation_id: `${i < 2 ? "private" : "group"}:${20001 + i}`,
      kind: i < 2 ? ("private" as const) : ("group" as const),
      account_id: "account-a",
      actor_ids: i === 3 ? [] : ["actor:a"],
      binding:
        i === 3
          ? ("unbound" as const)
          : i === 2
            ? ("inherited" as const)
            : ("explicit" as const),
      mode: i < 2 ? "whitelist" : "reply",
      observe: true,
      reply_permitted: true,
      state: "ready",
      source: "observation",
      edit:
        i < 2
          ? null
          : {
              kind: "group" as const,
              connection_id: "observation-a",
              revision: 3,
              unlocked: true,
            },
    })),
    defaults: [
      {
        account_id: "account-a",
        kind: "group",
        actor_id: "actor:a",
        mode: "observe",
        observe: true,
      },
    ],
    discovered_complete: true,
    sources: [{ id: "observation", available: true }],
  };
}
function events(): SafeEvent[] {
  return [
    {
      schema_version: "1.0.0",
      timestamp: "2026-10-28T05:00:00.010Z",
      service: "companion",
      instance_id: "00000000-0000-4000-8000-000000000001",
      sequence: 1,
      event_id: "00000000-0000-4000-8000-000000000011",
      level: "INFO",
      event: "turn.queued",
      outcome: "started",
      correlation_id: correlation,
      duration_ms: null,
      error_code: null,
    },
    {
      schema_version: "1.0.0",
      timestamp: "2026-10-28T05:00:00.020Z",
      service: "gateway",
      instance_id: "00000000-0000-4000-8000-000000000002",
      sequence: 2,
      event_id: "00000000-0000-4000-8000-000000000012",
      level: "WARNING",
      event: "upstream.call_finished",
      outcome: "failed",
      correlation_id: correlation,
      duration_ms: 125.5,
      error_code: "dependency_unavailable",
    },
  ];
}

async function session(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        username: "synthetic-graph",
        csrf: "synthetic-csrf",
        conversations: [],
        dialogue: { available: false, code: "not_configured", model: "" },
      },
    }),
  );
  await page.route("**/api/v1/infrastructure/view", (route) =>
    route.fulfill({ json: { configured: false, nodes: [] } }),
  );
}

test("chain graph relationships filter, select, preserve policy and save through the original API", async ({
  page,
}) => {
  await session(page);
  const value = topology();
  await page.route("**/api/v1/flow-graph/relationships", (route) =>
    route.fulfill({ json: value }),
  );
  await page.route("**/api/v1/bot-observation/view", (route) =>
    route.fulfill({
      json: {
        connections: [
          {
            id: "observation-a",
            revision: 3,
            group_policy: {
              version: 2,
              default: { mode: "observe", actor_id: "actor:a", keywords: [] },
              groups: {
                "20003": {
                  mode: "reply",
                  actor_id: "actor:a",
                  keywords: ["合成关键词"],
                },
              },
            },
            private_policy: {
              observe: true,
              mode: "observe_only",
              list: [],
              actor_id: "actor:a",
            },
          },
        ],
      },
    }),
  );
  let saved: Record<string, unknown> | null = null;
  await page.route("**/api/v1/bot-observation/policy", async (route) => {
    saved = route.request().postDataJSON();
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf");
    value.conversations[2].actor_ids = ["actor:b"];
    await route.fulfill({
      json: { connection: { state: "ready", pending: false } },
    });
  });
  await page.goto("/#/system/graph");
  await expect(
    page.getByRole("heading", { name: "链路图", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "私聊 2", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "私聊 2", exact: true }).click();
  await expect(page.locator(".flow-window")).toHaveCount(2);
  await page.getByRole("button", { name: "全部 16", exact: true }).click();
  await page.getByLabel("搜索关系").fill("20003");
  await expect(page.locator(".flow-window")).toHaveCount(1);
  await page.locator(".flow-window").click();
  await expect(page.locator(".flow-connectors .is-active")).toHaveCount(2);
  await page.getByLabel("该群角色").selectOption("actor:b");
  await page.getByRole("button", { name: "保存角色映射" }).click();
  await expect(
    page.getByText("映射已保存。图中状态来自重新读取。"),
  ).toBeVisible();
  expect(saved).toMatchObject({
    id: "observation-a",
    expected_revision: 3,
    group_policy: {
      groups: {
        "20003": {
          actor_id: "actor:b",
          mode: "reply",
          keywords: ["合成关键词"],
        },
      },
    },
    private_policy: { observe: true, mode: "observe_only" },
  });
  const accessibility = await new AxeBuilder({ page })
    .include(".flow-page")
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("chain graph curves follow independent column scroll and narrow horizontal scroll", async ({
  page,
}, testInfo) => {
  await session(page);
  await page.route("**/api/v1/flow-graph/relationships", (route) =>
    route.fulfill({ json: topology() }),
  );
  await page.goto("/#/system/graph");
  const right = page
    .getByRole("region", { name: "会话窗口", exact: true })
    .locator(".flow-column-body");
  await expect(
    page.locator('.flow-connectors path[data-to="window-0"]'),
  ).toHaveCount(1);
  await right.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(
    page.locator('.flow-connectors path[data-to="window-15"]'),
  ).toHaveCount(1);
  const aligned = await page.locator(".flow-board").evaluate((board) => {
    const svg = board.querySelector("svg")!;
    const path = board.querySelector<SVGPathElement>('[data-to="window-15"]')!;
    const point = path.getPointAtLength(path.getTotalLength());
    const svgRect = svg.getBoundingClientRect();
    const node = board
      .querySelector('[data-flow-node="window-15"]')!
      .getBoundingClientRect();
    return (
      Math.abs(point.x + svgRect.left - node.left) < 2 &&
      Math.abs(point.y + svgRect.top - (node.top + node.height / 2)) < 2
    );
  });
  expect(aligned).toBe(true);
  if (testInfo.project.name === "mobile") {
    await page.locator(".flow-board-viewport").evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
    });
    await page.locator('[data-flow-node="window-15"]').scrollIntoViewIfNeeded();
    await expect(page.locator('[data-flow-node="window-15"]')).toBeInViewport();
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("chain-relationships.png"),
    fullPage: true,
  });
});

test("chain graph runtime uses actual failure evidence and leaves unobserved stages unknown", async ({
  page,
}, testInfo) => {
  await session(page);
  const calls: string[] = [];
  await page.route("**/api/v1/flow-graph/*", async (route) => {
    const operation = route.request().url().split("/").at(-1)!;
    calls.push(operation);
    if (operation === "relationships")
      return route.fulfill({ json: topology() });
    if (operation === "traces")
      return route.fulfill({
        json: {
          availability: "ready",
          window: { start_ns: "1", end_ns: "2" },
          complete: false,
          event_count: 2,
          traces: [
            {
              correlation_id: correlation,
              first_at: events()[0].timestamp,
              last_at: events()[1].timestamp,
              event_count: 2,
              services: ["companion", "gateway"],
              has_failure: true,
              has_unknown: false,
              business_observed: true,
            },
            {
              correlation_id: "b".repeat(32),
              first_at: events()[0].timestamp,
              last_at: events()[1].timestamp,
              event_count: 2,
              services: ["platform"],
              has_failure: false,
              has_unknown: false,
              business_observed: false,
            },
          ],
        },
      });
    expect(route.request().postDataJSON()).toEqual({
      correlation_id: correlation,
      start_ns: "1",
      end_ns: "2",
    });
    return route.fulfill({
      json: {
        availability: "ready",
        correlation_id: correlation,
        window: { start_ns: "1", end_ns: "2" },
        complete: false,
        events: events(),
        stages: [
          {
            id: "companion",
            label: "陪伴处理",
            event_ids: [events()[0].event_id],
          },
          { id: "memory", label: "记忆读取", event_ids: [] },
          { id: "model", label: "模型调用", event_ids: [events()[1].event_id] },
          { id: "schedule", label: "日程与生活", event_ids: [] },
        ],
      },
    });
  });
  await page.goto("/#/system/graph");
  await page.getByRole("tab", { name: "实际运行" }).click();
  await expect(page.locator(".flow-trace-summary")).toHaveCount(2);
  await page.getByLabel("关联类型").selectOption("service");
  await expect(page.locator(".flow-trace-summary")).toHaveCount(1);
  await expect(page.locator(".flow-trace-summary")).toContainText(
    "服务观测关联",
  );
  await page.getByLabel("关联类型").selectOption("business");
  await expect(page.locator(".flow-trace-summary")).toHaveCount(1);
  await page.locator(".flow-trace-summary").click();
  await expect(
    page.getByText("upstream.call_finished", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "记忆读取 未观测", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/本次结果未完整覆盖时间窗/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "模型调用 有失败事件", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /upstream.call_finished/ }).click();
  await expect(page.getByRole("region", { name: "事件证据" })).toContainText(
    "125.5 ms（此事件记录值）",
  );
  expect(calls.filter((operation) => operation === "trace")).toHaveLength(1);
  expect(calls.filter((operation) => operation === "traces")).toHaveLength(1);
  await page.screenshot({
    path: testInfo.outputPath("chain-runtime.png"),
    fullPage: true,
  });
});

test("chain graph loading, retry and missing log reader remain distinct", async ({
  page,
}) => {
  await session(page);
  let count = 0;
  await page.route("**/api/v1/flow-graph/relationships", async (route) => {
    count++;
    if (count === 1)
      return route.fulfill({
        status: 503,
        json: { code: "dependency_unavailable" },
      });
    return route.fulfill({
      json: {
        ...topology(),
        accounts: [],
        roles: [],
        conversations: [],
        sources: [{ id: "observation", available: false }],
      },
    });
  });
  await page.route("**/api/v1/flow-graph/traces", (route) =>
    route.fulfill({
      json: {
        availability: "not_configured",
        complete: false,
        event_count: 0,
        traces: [],
        window: { start_ns: "1", end_ns: "2" },
      },
    }),
  );
  await page.goto("/#/system/graph");
  await expect(
    page.getByRole("heading", { name: "配置关系读取失败" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "尚未配置机器人连接" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "实际运行" }).click();
  await expect(
    page.getByRole("heading", { name: "运行日志查询未接入" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "选择一个运行关联" }),
  ).toHaveCount(0);
});
