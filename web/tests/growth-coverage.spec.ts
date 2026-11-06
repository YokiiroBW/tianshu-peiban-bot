import { test, expect } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test.beforeEach(async ({ page }) => nativeSession(page));

for (const partial of [true, false]) {
  test(`Growth ${partial ? "explains unassessed bodies" : "keeps complete observations quiet"}`, async ({
    page,
  }) => {
    await page.route(apiPath("companion/growth/view"), (route) =>
      answer(route, {
        items: [],
        reflections: [],
        next_cursor: null,
        missing: [],
        can_review: true,
        review: {
          revision: 3,
          last_error: null,
          can_request_review: false,
          pending_inputs: 0,
          partial_inputs: partial ? 2 : 0,
          material_missing: partial
            ? ["discovery_metadata_only_body_not_read", "action_content_not_assessed"]
            : [],
          input_missing: [],
          batch: null,
        },
      }),
    );
    await page.goto("/#/roles/growth");
    const range = page.getByLabel("反思材料范围");
    if (partial) {
      await expect(range).toBeVisible();
      await expect(range.getByText(/有 2 条经历按有限材料整理/)).toBeVisible();
      await expect(range.getByText(/不包含正文阅读/)).toBeVisible();
      await expect(range.getByText(/完整内容尚未纳入反思/)).toBeVisible();
      await expect(page.getByText("当前不可用材料")).toHaveCount(0);
    } else {
      await expect(range).toHaveCount(0);
    }
  });
}

test("Growth shows retry due time and lets only a stopped batch request explicit review", async ({ page }) => {
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("companion/growth/view"), (route) => answer(route, {
    items: [], reflections: [], next_cursor: null, missing: [], can_review: true,
    review: {
      revision: 12, last_error: null, can_request_review: false, pending_inputs: 21,
      pending_original_requests: 0, batch: null,
      retry_summary: { waiting: 1, active: 0, blocked: 1, next_retry_at: "2026-11-03T00:05:00+08:00" },
      retry_batches: [{ retry_key: "original-material", current_material_key: "original-material:retry:2",
        previous_material_key: "original-material", attempt_id: "second-attempt", attempt_number: 2,
        state: "waiting", due_at: "2026-11-03T00:05:00+08:00", last_error: "transport_unknown",
        waiting_on_original: false, outcome: null, can_request_review: false },
      { retry_key: "invalid-output", current_material_key: "invalid-output", previous_material_key: null,
        attempt_id: "stopped-attempt", attempt_number: 1, state: "blocked", due_at: null,
        last_error: "reflection_output_invalid", waiting_on_original: false, outcome: null, can_request_review: true }],
    },
  }));
  await page.route(apiPath("companion/growth/review"), async (route) => {
    writes.push(route.request().postDataJSON());
    return answer(route, { command_id: "synthetic-review", data: { state: "queued" }, revision: 13 });
  });
  await page.goto("/#/roles/growth");
  await expect(page.getByLabel("成长自动重试")).toContainText("等待重试 1 批");
  await expect(page.getByLabel("成长自动重试")).toContainText("后续经历可以继续整理");
  await expect(page.getByLabel("成长自动重试")).toContainText("下一次重试");
  await expect(page.getByLabel("成长自动重试")).toContainText("2026");
  await page.getByText("重试批次与处理次数", { exact: true }).click();
  await expect(page.getByText(/已安排尝试 2 次/)).toBeVisible();
  await expect(page.getByText("已关联本次执行记录", { exact: true })).toBeVisible();
  const review = page.getByRole("button", { name: "重新整理这批材料" });
  await expect(review).toHaveCount(1);
  await review.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ retry_key: "invalid-output", expected_revision: 12 });
});
