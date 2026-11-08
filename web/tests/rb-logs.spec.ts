import { test, expect } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test("log continuation keeps the fixed range and distinct events at the same timestamp", async ({
  page,
}) => {
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/logs"), (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    const record = (id: string) => ({
      event_id: id,
      instance_id: "one",
      sequence: id,
      timestamp_ns: "1793850000000000000",
      role: "worker",
      level: "ERROR",
      module: "life",
      message: `event ${id}`,
    });
    return answer(route, {
      entries: body.cursor
        ? [record("2"), record("3")]
        : [record("1"), record("2")],
      next_cursor: body.cursor ? null : "opaque-2",
      coverage: {
        complete: true,
        snapshot_complete: !!body.cursor,
        has_more: !body.cursor,
        missing: [],
      },
      freshness: { state: "current" },
    });
  });
  await page.goto("/#/system/logs");
  await expect(page.getByText("event 1")).toBeVisible();
  await page.getByLabel("级别").selectOption("ERROR");
  await page.getByLabel("模块").fill("life");
  await page.getByLabel("关键词").fill("event");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(2);
  const fixed = requests.at(-1)!;
  await page.getByRole("button", { name: "加载下一页" }).click();
  await expect(page.getByText("event 3")).toBeVisible();
  expect(requests.at(-1)).toMatchObject({
    start_ns: fixed.start_ns,
    end_ns: fixed.end_ns,
    level: "ERROR",
    module: "life",
    keyword: "event",
    cursor: "opaque-2",
    exclude_conversation: true,
  });
  await expect(page.getByText("event 2")).toHaveCount(1);
  await page.getByText("采集状态与覆盖", { exact: false }).click();
  await expect(
    page.getByText("当前快照已读完。", { exact: false }),
  ).toBeVisible();
});

test("live logs wait for an in-flight read then advance the range and pause for details", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-11-08T15:00:00Z") });
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  let finishFirst!: () => void;
  const firstResponse = new Promise<void>((resolve) => {
    finishFirst = resolve;
  });
  await page.route(apiPath("system/logs"), async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    const number = requests.length;
    if (number === 1) await firstResponse;
    return answer(route, {
      entries: [
        {
          event_id: `event-${number}`,
          instance_id: "one",
          sequence: number,
          timestamp_ns: body.end_ns,
          role: "interactive",
          level: "INFO",
          module: "runtime",
          message: `live log ${number}`,
        },
      ],
      next_cursor: null,
      coverage: { snapshot_complete: true, missing: [] },
    });
  });
  await page.goto("/#/system/logs");
  await expect.poll(() => requests.length).toBe(1);
  await page.clock.runFor(16000);
  expect(requests).toHaveLength(1);
  finishFirst();
  await expect(page.getByText("live log 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "暂停跟随" })).toBeVisible();
  await page.clock.runFor(5000);
  await expect(page.getByText("live log 2", { exact: true })).toBeVisible();
  expect(BigInt(String(requests[1].end_ns))).toBeGreaterThan(
    BigInt(String(requests[0].end_ns)),
  );
  expect(
    BigInt(String(requests[1].end_ns)) - BigInt(String(requests[1].start_ns)),
  ).toBe(3600000000000n);
  await page.getByText("live log 2", { exact: true }).click();
  await expect(page.getByRole("button", { name: "自动跟随" })).toBeVisible();
  await page.clock.runFor(10000);
  expect(requests).toHaveLength(2);
});

test("missing log configuration is distinct from an empty result", async ({
  page,
}) => {
  await nativeSession(page);
  await page.route(apiPath("system/logs"), (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          code: "log_query_not_configured",
          message: "not configured",
          details: null,
          request_id: "fixture",
        },
      },
    }),
  );
  await page.goto("/#/system/logs");
  await expect(page.getByRole("alert")).toContainText("日志查询尚未配置");
  await expect(page.getByText("当前快照没有匹配日志。")).toHaveCount(0);
});

test("a log job link requests the original job rather than only the latest page", async ({
  page,
}) => {
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/tasks"), (route) => {
    const body = route.request().postDataJSON();
    requests.push(body);
    return answer(route, {
      jobs: [
        {
          id: 42,
          task_name: "历史任务",
          queue_name: "memory_consolidation",
          status: "succeeded",
          scheduled_at: null,
          attempts: 1,
          abort_requested: false,
        },
      ],
      counts: [],
      complete: true,
      limit: 50,
    });
  });
  await page.route(apiPath("system/resources"), (route) =>
    answer(route, { processes: [], broker: {} }),
  );
  await page.goto("/#/system/tasks?job_id=42");
  await expect(page.getByRole("row", { name: "原任务 42" })).toContainText(
    "历史任务",
  );
  expect(requests[0].job_id).toBe("42");
});
