import { test, expect } from "@playwright/test";
import { answer, emptyPage, nativeSession, receipt } from "./native-fixture";

test.beforeEach(async ({ page }) => nativeSession(page));

test("original action and runtime tasks distinguish lane waiting, schedule, unknown and unresolved routing", async ({ page }) => {
  let execution = "pending";
  let waitReason = "native_task_queue";
  let writes = 0;
  page.on("request", (request) => {
    if (/\/api\/v1\/(images\/generate|capabilities\/(invoke|resume|cancel))$/.test(new URL(request.url()).pathname)) writes += 1;
  });
  await page.route("**/api/v1/capabilities/get**", (route) => {
    const body = route.request().postDataJSON();
    expect(body.action_id).toBe("original-action");
    return answer(route, {
      action: { capability_id: "images.generate", revision: 1, source_refs: [] },
      attempt: { attempt_id: "original-attempt", dispatch: "not_started" },
      receipt: { ...receipt("pending"), execution, effect: execution === "unknown" ? "unknown" : "none" },
      result_revision: 1,
      result_observed_at: null,
      execution: {
        execution_lane: "images", service_class: "interactive", job_id: 17,
        job_state: waitReason === "executing" ? "doing" : "todo", queue_name: "images",
        attempts: 0, scheduled_at: null, priority: 0, wait_reason: waitReason,
      },
    });
  });
  const jobs = [
    { id: 17, task_name: "fixture.image", queue_name: "images", execution_lane: "images", status: "todo", wait_reason: "native_task_queue", scheduled_at: null, attempts: 0, abort_requested: false },
    { id: 18, task_name: "fixture.schedule", queue_name: "schedule", execution_lane: "schedule", status: "todo", wait_reason: "scheduled", scheduled_at: "2099-01-01T00:00:00Z", attempts: 0, abort_requested: false },
    { id: 19, task_name: "fixture.future", queue_name: "future_lane", execution_lane: "future_lane", status: "todo", wait_reason: "execution_lane_unresolved", scheduled_at: null, attempts: 0, abort_requested: false },
  ];
  await page.route("**/api/v1/system/tasks**", (route) => answer(route, { jobs, counts: [], complete: true, limit: 50 }));
  await page.route("**/api/v1/system/resources**", (route) => answer(route, { processes: [], broker: {} }));
  await page.goto("/#/system/tasks?action=original-action&actor_id=actor&person_id=person&conversation_id=web-conversation");
  const original = page.locator("section.panel").filter({ has: page.getByRole("heading", { name: "原能力动作", exact: true }) });
  await expect(original.getByText("执行通道：生图", { exact: true })).toBeVisible();
  await expect(original.getByText("等待本功能执行", { exact: true })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "fixture.schedule" }).getByText("尚未到计划时间", { exact: true })).toBeVisible();
  const future = page.getByRole("row").filter({ hasText: "fixture.future" });
  await expect(future.getByText("未识别的执行通道", { exact: true })).toBeVisible();
  await expect(future.getByText("执行通道信息尚未确定", { exact: true })).toBeVisible();
  await future.getByText("调度详情", { exact: true }).click();
  await expect(future.locator("pre")).toContainText("future_lane");
  waitReason = "executing";
  execution = "running";
  await original.getByRole("button", { name: "读取实际账本", exact: true }).click();
  await expect(original.getByText("本功能执行中", { exact: true })).toBeVisible();
  execution = "unknown";
  await original.getByRole("button", { name: "读取实际账本", exact: true }).click();
  await expect(original.getByText("结果未知，请核对原任务", { exact: true })).toBeVisible();
  await expect(original.getByText("执行失败", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/GPU.*忙|模型.*忙/)).toHaveCount(0);
  expect(writes).toBe(0);
});

test("image waiting reads the original action and preserves its scoped detail link without generation or delivery", async ({ page }) => {
  let actionReads = 0;
  let generationWrites = 0;
  await page.route("**/api/v1/images/generate**", async (route) => {
    generationWrites += 1;
    await route.abort();
  });
  await page.route("**/api/v1/images/view**", (route) => answer(route, {
    engines: [], profiles: [], workflows: [], identity: null, outfits: [], scene: null,
    album: [], jobs: [{ ...receipt("pending"), reason: null }],
  }));
  await page.route("**/api/v1/life/outfits**", (route) => answer(route, emptyPage));
  await page.route("**/api/v1/conversation/messages**", (route) => answer(route, emptyPage));
  await page.route("**/api/v1/capabilities/get**", (route) => {
    actionReads += 1;
    expect(route.request().postDataJSON().action_id).toBe("original-action");
    return answer(route, {
      action: { capability_id: "images.generate", revision: 1 }, attempt: {},
      receipt: receipt("pending"), result_revision: 0, result_observed_at: null,
      execution: { execution_lane: "images", service_class: "interactive", job_id: 27,
        job_state: "todo", wait_reason: "native_task_queue" },
    });
  });
  await page.goto("/#/roles/chat");
  await expect(page.getByRole("textbox", { name: "说点什么", exact: true })).toBeVisible();
  await page.evaluate(() => { window.location.hash = "#/roles/life"; });
  await page.getByRole("tab", { name: "生图与相册", exact: true }).click();
  const job = page.locator("article.domain-record").filter({
    has: page.getByRole("link", { name: "原任务与投递详情", exact: true }),
  });
  await expect(job.getByText("执行通道：生图", { exact: true })).toBeVisible();
  await expect(job.getByText("等待本功能执行", { exact: true })).toBeVisible();
  await expect(job.getByRole("button", { name: "请求取消后续生成", exact: true })).toBeVisible();
  const href = await job.getByRole("link", { name: "原任务与投递详情", exact: true }).getAttribute("href");
  expect(href).toContain("action=original-action");
  expect(href).toContain("actor_id=actor");
  expect(href).toContain("conversation_id=web-conversation");
  expect(actionReads).toBeGreaterThan(0);
  expect(generationWrites).toBe(0);
  await expect(job.getByText(/GPU.*忙|模型.*忙/)).toHaveCount(0);
});
