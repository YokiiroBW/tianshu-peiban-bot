import { test, expect } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test("statistics separate physical attempts, token coverage and unknown costs", async ({ page }) => {
  await nativeSession(page);
  const requests: Record<string, unknown>[] = [];
  await page.route(apiPath("system/statistics"), (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    const metrics = { physical_attempts: { total: 3, samples: 3, unknown: 0 }, input_tokens: { total: 1200, samples: 2, unknown: 1 }, actual_cost: { total: null, samples: 0, unknown: 3 }, life_intentional_idle: { total: 1, samples: 1, unknown: 0 }, life_deferred_no_slot: { total: 2, samples: 2, unknown: 0 } };
    return answer(route, { range: { start: body.start, end: body.end, timezone: body.timezone, resolution: "hour" }, overview: metrics, cumulative: metrics,
      series: [{ time: "2026-11-04T01:00:00Z", metrics }], breakdowns: { module: { models: metrics } }, resources: null,
      coverage: { complete: false, missing: ["growth"], percentiles: "unavailable_without_distribution" }, recovery_rate: { eligible: 5, evaluated: 4, verified: 3, rate: 0.75 }, as_of: "2026-11-04T02:00:00Z" });
  });
  await page.goto("/#/system/statistics");
  await expect(page.getByText("统计来源尚未完整覆盖", { exact: false })).toContainText("growth");
  const attempts = page.locator("article.statistics-card").filter({ hasText: "实际模型调用" }).first();
  await expect(attempts).toContainText("3");
  const input = page.locator("article.statistics-card").filter({ hasText: "实际输入 Token" }).first();
  await expect(input).toContainText("未知 1");
  const cost = page.locator("article.statistics-card").filter({ hasText: "实际费用" }).first();
  await expect(cost).toContainText("未知");
  await expect(page.getByText("恢复验证：", { exact: false })).toContainText("已判定 4 项 · 已验证 3 项 · 验证比例 75.0%");
  await page.getByLabel("时间范围").selectOption("3");
  await page.getByLabel("模块", { exact: true }).fill("life");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(2);
  const selected = requests.at(-1)!;
  expect(selected.module).toBe("life");
  expect(Date.parse(String(selected.end)) - Date.parse(String(selected.start))).toBe(3 * 86400000);
  await expect(page.getByText("主动留白", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("暂时排不下", { exact: true }).first()).toBeVisible();
});

test("unavailable statistics remain an explicit coverage failure", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("system/statistics"), (route) => answer(route, { status: "unavailable", coverage: { complete: false, missing: ["projection_unavailable"] } }));
  await page.goto("/#/system/statistics");
  await expect(page.getByRole("alert")).toContainText("统计投影暂不可用");
  await expect(page.getByRole("alert")).toContainText("projection_unavailable");
  await expect(page.getByText("实际模型调用", { exact: true })).toHaveCount(0);
});
