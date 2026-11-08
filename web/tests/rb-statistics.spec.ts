import { test, expect } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test("statistics separate physical attempts, token coverage and unknown costs", async ({
  page,
}) => {
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/statistics"), (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    const metrics = {
      physical_attempts: { total: 3, samples: 3, unknown: 0 },
      input_tokens: { total: 1200, samples: 2, unknown: 1 },
      actual_cost: { total: null, samples: 0, unknown: 3 },
      life_intentional_idle: { total: 1, samples: 1, unknown: 0 },
      life_deferred_no_slot: { total: 2, samples: 2, unknown: 0 },
    };
    return answer(route, {
      range: {
        start: body.start,
        end: body.end,
        timezone: body.timezone,
        resolution: "hour",
      },
      overview: metrics,
      cumulative: metrics,
      series: [{ time: "2026-11-04T01:00:00Z", metrics }],
      breakdowns: { module: { models: metrics } },
      resources: null,
      coverage: {
        complete: false,
        missing: ["growth"],
        percentiles: "unavailable_without_distribution",
      },
      recovery_rate: { eligible: 5, evaluated: 4, verified: 3, rate: 0.75 },
      as_of: "2026-11-04T02:00:00Z",
    });
  });
  await page.goto("/#/system/statistics");
  await expect(
    page.getByText("统计来源尚未完整覆盖", { exact: false }),
  ).toContainText("growth");
  const attempts = page
    .locator("article.statistics-card")
    .filter({ hasText: "实际模型调用" })
    .first();
  await expect(attempts).toContainText("3");
  const input = page
    .locator("article.statistics-card")
    .filter({ hasText: "实际输入 Token" })
    .first();
  await expect(input).toContainText("未知 1");
  const cost = page
    .locator("article.statistics-card")
    .filter({ hasText: "实际费用" })
    .first();
  await expect(cost).toContainText("未知");
  await expect(page.getByText("恢复验证：", { exact: false })).toContainText(
    "已判定 4 项 · 已验证 3 项 · 验证比例 75.0%",
  );
  await page.getByLabel("时间范围").selectOption("3");
  await page.getByText("更多筛选", { exact: true }).click();
  await page.getByLabel("模块", { exact: true }).fill("life");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(2);
  const selected = requests.at(-1)!;
  expect(selected.module).toBe("life");
  expect(
    Date.parse(String(selected.end)) - Date.parse(String(selected.start)),
  ).toBe(3 * 86400000);
  await expect(
    page.locator("dt").filter({ hasText: "主动留白" }).first(),
  ).toBeVisible();
  await expect(
    page.locator("dt").filter({ hasText: "暂时排不下" }).first(),
  ).toBeVisible();
});

test("unavailable statistics remain an explicit coverage failure", async ({
  page,
}) => {
  await nativeSession(page);
  await page.route(apiPath("system/statistics"), (route) =>
    answer(route, {
      status: "unavailable",
      coverage: { complete: false, missing: ["projection_unavailable"] },
    }),
  );
  await page.goto("/#/system/statistics");
  await expect(page.getByRole("alert")).toContainText("统计投影暂不可用");
  await expect(page.getByRole("alert")).toContainText("projection_unavailable");
  await expect(page.getByText("实际模型调用", { exact: true })).toHaveCount(0);
});

test("statistics refresh advances the applied rolling range and preserves a selected historical date", async ({
  page,
}) => {
  await page.clock.setFixedTime(new Date("2026-11-08T15:00:00Z"));
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/statistics"), (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    return answer(route, {
      range: {
        start: body.start,
        end: body.end,
        timezone: body.timezone,
        resolution: "hour",
      },
      overview: {},
      cumulative: {},
      series: [],
      breakdowns: {},
      coverage: { complete: true },
      as_of: body.end,
    });
  });
  await page.goto("/#/system/statistics");
  await expect.poll(() => requests.length).toBe(1);
  await page.getByText("更多筛选", { exact: true }).click();
  await page.getByLabel("模块", { exact: true }).fill("life");
  await page.getByLabel("时间范围").selectOption("3");
  await page.clock.setFixedTime(new Date("2026-11-08T15:02:00Z"));
  await page.getByRole("button", { name: "刷新统计" }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toMatchObject({
    start: "2026-11-07T16:00:00.000Z",
    end: "2026-11-08T15:02:00.000Z",
    module: null,
  });
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBe(3);
  await page.clock.setFixedTime(new Date("2026-11-08T15:04:00Z"));
  await page.getByRole("button", { name: "刷新统计" }).click();
  await expect.poll(() => requests.length).toBe(4);
  expect(requests[3]).toMatchObject({
    end: "2026-11-08T15:04:00.000Z",
    module: "life",
  });
  expect(
    Date.parse(String(requests[3].end)) - Date.parse(String(requests[3].start)),
  ).toBe(3 * 86400000);
  await page.getByLabel("时间范围").selectOption("date");
  await page.getByLabel("统计日期").fill("2026-11-07");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBe(5);
  await page.clock.setFixedTime(new Date("2026-11-09T02:00:00Z"));
  await page.getByRole("button", { name: "刷新统计" }).click();
  await expect.poll(() => requests.length).toBe(6);
  expect(requests[5]).toMatchObject({
    start: requests[4].start,
    end: requests[4].end,
    module: "life",
  });
});

function usageMetrics(
  input: number,
  output: number,
  calls: number,
  unknown = 0,
) {
  const known = (total: number, samples = calls) => ({
    total,
    samples,
    unknown: 0,
  });
  return {
    total_tokens: { total: input + output, samples: calls - unknown, unknown },
    input_tokens: { total: input, samples: calls - unknown, unknown },
    output_tokens: { total: output, samples: calls - unknown, unknown },
    cached_input_tokens: known(Math.round(input / 5)),
    cache_write_tokens: known(0),
    reasoning_tokens: known(Math.round(output / 3)),
    physical_attempts: known(calls),
    model_failed: known(Math.floor(calls / 10)),
    model_unknown: known(unknown),
    execution_ms: known(calls * 8681),
    actual_cost: { total: null, samples: 0, unknown: calls },
  };
}

test("usage dashboard shows model and function rankings, dates, details and fits the viewport", async ({
  page,
}, testInfo) => {
  await page.clock.setFixedTime(new Date("2026-11-08T08:00:00Z"));
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/statistics"), (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    const metrics = usageMetrics(75616440, 6372514, 8298, 128);
    const models = [
      {
        provider_id: "p1",
        provider_name: "日日新",
        model: "deepseek-v4-flash-with-a-long-historical-model-name-2026-11",
        metrics: usageMetrics(50000000, 4000000, 5000, 100),
      },
      {
        provider_id: "p1",
        provider_name: "日日新",
        model: "deepseek-v4-pro",
        metrics: usageMetrics(20000000, 2000000, 2500, 28),
      },
      {
        provider_id: "p2",
        provider_name: "另一供应商",
        model: "kimi-k2.5",
        metrics: usageMetrics(5616440, 372514, 798),
      },
    ];
    const functions = ["memory.extract", "life.plan", "chat"].map(
      (name, index) => ({ function: name, metrics: models[index].metrics }),
    );
    const daily = Array.from({ length: 30 }, (_, index) => ({
      date: new Date(Date.UTC(2026, 9, 10 + index)).toISOString().slice(0, 10),
      metrics:
        index === 9
          ? {}
          : usageMetrics(
              2000000 + index * 26000,
              200000,
              200,
              index === 29 ? 2 : 0,
            ),
      by_function: functions,
      by_model: models,
    }));
    return answer(route, {
      range: {
        start: body.start,
        end: body.end,
        timezone: body.timezone,
        resolution: "day",
      },
      overview: {
        ...metrics,
        life_scheduled: { total: 16, samples: 38, unknown: 0 },
      },
      cumulative: metrics,
      series: [],
      breakdowns: { family: { models: metrics } },
      model_usage: {
        status: "available",
        summary: metrics,
        by_model: models,
        by_function: functions,
        by_provider: [
          {
            provider_id: "p1",
            provider_name: "日日新",
            metrics: usageMetrics(70000000, 6000000, 7500, 128),
          },
          {
            provider_id: "p2",
            provider_name: "另一供应商",
            metrics: models[2].metrics,
          },
        ],
        daily,
      },
      resources: null,
      coverage: { complete: false, missing: [], incomplete: ["models"] },
      as_of: "2026-11-08T08:00:00Z",
    });
  });
  await page.goto("/#/system/statistics");
  await expect(
    page.getByRole("heading", { name: "模型用量排行" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "功能用量排行" }),
  ).toBeVisible();
  await expect(page.locator(".statistics-hero-number")).toContainText("81.99M");
  await expect(page.locator(".statistics-hero")).toContainText("未知 128 项");
  await expect(page.getByLabel("功能用量排行")).toContainText("记忆提取");
  await page.getByLabel("时间范围").selectOption("30");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(
    Date.parse(String(requests[1].end)) - Date.parse(String(requests[1].start)),
  ).toBe(30 * 86400000);
  await expect(page.getByRole("heading", { name: "每日用量" })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath(`statistics-${testInfo.project.name}.png`),
    fullPage: true,
  });
  await page.locator(".statistics-rankings").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath(`statistics-${testInfo.project.name}-usage.png`),
    fullPage: true,
  });
  await page.getByRole("button", { name: "供应商", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "供应商用量排行" }),
  ).toBeVisible();
  await page.getByLabel("排行依据").selectOption("physical_attempts");
  await expect(
    page.getByLabel("供应商用量排行").locator("ol li").first(),
  ).toContainText("7,500");
  await page
    .getByLabel("供应商用量排行")
    .getByText("查看全部 2 项及用量明细")
    .click();
  await expect(
    page.getByRole("table", { name: "供应商用量排行" }),
  ).toContainText("76,000,000");
  await page.getByLabel("趋势指标").selectOption("input_tokens");
  await expect
    .poll(
      async () =>
        (
          await page
            .getByRole("img", {
              name: "每日实际输入 Token柱状图",
              exact: false,
            })
            .boundingBox()
        )?.height ?? 0,
    )
    .toBeGreaterThan(180);
  await page.getByLabel("每日用量", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath(`statistics-${testInfo.project.name}-trend.png`),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "2026-10-10 · 实际输入 Token", exact: false })
    .click();
  await expect(page.locator(".statistics-chart-selection")).toContainText(
    "2,000,000",
  );
  await page.getByText("每日明细", { exact: true }).click();
  const missingDay = page.getByRole("row").filter({
    has: page.getByRole("rowheader", { name: "2026-10-19", exact: true }),
  });
  await expect(missingDay).toContainText("未知");
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBe(true);
  await page.getByLabel("时间范围").selectOption("date");
  await page.getByLabel("统计日期").fill("2026-11-07");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests[2].start).toBe("2026-11-06T16:00:00.000Z");
  expect(requests[2].end).toBe("2026-11-07T16:00:00.000Z");
});
