import { test, expect } from "@playwright/test";
import { answer, emptyPage, nativeSession, state } from "./native-fixture";

const routine = {
  actor_id: "actor",
  revision: 2,
  enabled: true,
  timezone: "Asia/Shanghai",
  wake_at: "07:00",
  sleep_at: "23:00",
  idle_seconds: 5400,
  next_due_at: "2026-11-01T05:34:00Z",
  last_tick_at: "2026-11-01T04:04:00Z",
  schedule_status: "waiting",
  schedule_reason: null,
  planning: {
    revision: 19,
    reason: "planner_unknown",
    next_due_at: "2026-11-01T05:34:00Z",
    last_planned_at: "2026-11-01T04:04:00Z",
    generation_consumed: false,
    pending: true,
  },
};

test.beforeEach(async ({ page }) => {
  await nativeSession(page);
  await page.route("**/api/v1/life/state**", (route) =>
    answer(route, {
      ...state,
      world_id: "default",
      room_id: "room",
      activity: null,
    }),
  );
  await page.route("**/api/v1/life/activities**", (route) =>
    answer(route, emptyPage),
  );
  await page.route("**/api/v1/life/routine**", (route) =>
    answer(route, routine),
  );
  await page.route("**/api/v1/companion/growth/view**", (route) =>
    answer(route, {
      items: [],
      reflections: [
        {
          id: "note",
          revision: 1,
          kind: "reflection",
          title: "读后的想法",
          content: "想把今天读到的内容整理成一份提纲。",
          occurred_at: "2026-11-01T04:00:00Z",
          evidence: [],
        },
      ],
      next_cursor: null,
      missing: [],
      review: null,
      can_review: true,
    }),
  );
});

test("life distinguishes blocked scheduling, subjective notes and missing original material", async ({
  page,
}) => {
  await page.route("**/api/v1/life/routine**", (route) =>
    answer(route, {
      ...routine,
      schedule_status: "blocked",
      schedule_reason: "forbidden",
    }),
  );
  await page.route("**/api/v1/memory/episodes**", (route) =>
    answer(route, {
      ...emptyPage,
      watermark: "1",
      items: [
        {
          episode_id: "old-action",
          revision: 1,
          summary: "原任务已执行",
          origin_kind: "tool_result",
          occurred_at: "2026-11-01T04:00:00Z",
          source_refs: [],
          actual_result: {},
          extraction_state: "no_original_material",
          coverage: { complete: false, missing: ["coverage_unknown"] },
        },
      ],
    }),
  );
  await page.goto("/#/roles/life");
  await expect(
    page.getByRole("alert").filter({ hasText: "当前授权已不足以继续这份作息" }),
  ).toBeVisible();
  await expect(
    page.getByText("还没有已保存的活动安排。", { exact: true }),
  ).toBeVisible();
  const mood = page
    .getByRole("heading", { name: "心情与感想", exact: true })
    .locator("..");
  await expect(
    mood.getByText("暂时没有新的主观记录。", { exact: true }),
  ).toBeVisible();
  const notes = page
    .getByRole("heading", { name: "最近的主观手记", exact: true })
    .locator("..");
  await expect(
    notes.getByText("想把今天读到的内容整理成一份提纲。", { exact: true }),
  ).toBeVisible();
  await expect(
    mood.getByText("想把今天读到的内容整理成一份提纲。", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("日常小屋", { exact: true })).toBeVisible();
  await expect(page.getByText("default · room", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByText("本次记录没有提供素材覆盖范围，不能据此认定已读完或看完。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "记忆整理：这条记录没有可用于整理的原始材料，未从中抽取人物事实。",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByText(/coverage_unknown|no_original_material/),
  ).toHaveCount(0);
});

test("life reports the original unknown planner and an overdue clock without claiming activity", async ({
  page,
}) => {
  await page.goto("/#/roles/life");
  await expect(
    page.getByText("原规划结果尚未确认，正在保留原请求身份，未重新提交。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("还没有已保存的活动安排。", { exact: true }),
  ).toBeVisible();
  await page.route("**/api/v1/life/routine**", (route) =>
    answer(route, {
      ...routine,
      schedule_status: "overdue",
      planning: {
        ...routine.planning,
        reason: "planner_output_invalid",
        pending: false,
      },
    }),
  );
  await page.reload();
  await expect(
    page.getByText(
      "生活安排已到预定时间，尚未推进；若长时间没有变化，请查看系统任务。",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByText("这次模型输出没有形成有效计划，未保存活动。", {
      exact: true,
    }),
  ).toBeVisible();
});
