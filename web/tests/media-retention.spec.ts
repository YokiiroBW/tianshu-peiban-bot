import { test, expect } from "@playwright/test";
import {
  fixtureJob,
  fixtureView,
  installMediaFixture,
  selectMediaSection,
} from "./media.fixtures";

function retentionView() {
  const view = fixtureView();
  view.jobs = [
    fixtureJob({
      job_id: "old-failure",
      title: "之前失败的下载",
      state: "failed",
      stage: "downloading",
      code: "engine_unavailable",
      can_delete: true,
      can_retry: true,
      can_cancel: false,
    }),
    fixtureJob({
      job_id: "retained-media",
      title: "保留的媒体成品",
      state: "completed",
      stage: "completed",
      code: "",
      can_delete: true,
      can_cancel: false,
    }),
    fixtureJob({
      job_id: "active-download",
      title: "正在执行的下载",
      state: "downloading",
      stage: "downloading",
      code: "",
      can_delete: false,
    }),
  ];
  view.overview!.jobs = {
    total: 3,
    published: 1,
    processing: 1,
    queued: 0,
    attention: 1,
    cancelled: 0,
  };
  return view;
}

test("delete a failed record closes its detail and refreshes actual list and counters", async ({
  page,
}, info) => {
  const state = await installMediaFixture(page, retentionView());
  await page.goto("/#/media/jobs");
  const active = page.locator('[data-job-id="active-download"]');
  await expect(
    active.getByRole("button", { name: "删除记录", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("筛选任务").selectOption("attention");
  const failure = page.locator('[data-job-id="old-failure"]');
  await failure.getByRole("button", { name: "查看任务详情" }).click();
  const detail = page.getByRole("complementary", { name: "任务详情" });
  await expect(
    detail.getByRole("heading", { name: "之前失败的下载", exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "删除记录", exact: true }).click();
  await expect(detail).toHaveCount(0);
  await expect(failure).toHaveCount(0);
  await expect(
    page.getByText("这个筛选下没有任务", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("status").filter({ hasText: "任务记录已删除" }),
  ).toContainText("媒体文件会保留");
  const deletion = state.calls.find(
    (call) => call.operation === "jobs/delete",
  )!;
  expect(deletion.body).toEqual({
    job_id: "old-failure",
    expected_revision: 3,
    client_request_id: expect.stringMatching(/^[\da-f-]{36}$/),
  });
  expect(
    state.calls.filter((call) => call.operation === "jobs/list").at(-1)!.body,
  ).toMatchObject({
    cursor: null,
    states: ["failed", "unknown", "waiting_metadata", "auth_required"],
  });
  await selectMediaSection(page, "概览");
  await expect(page.locator(".overview-total")).toContainText("累计 2 个任务");
  await expect(page.locator(".overview-kpi-value").nth(3)).toHaveText("0");
  await selectMediaSection(page, "下载中心");
  await expect(page.locator('[data-job-id="retained-media"]')).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath(`records-after-delete-${info.project.name}.png`),
    fullPage: true,
  });
});

test("revision conflict preserves the failed record and open detail", async ({
  page,
}) => {
  const state = await installMediaFixture(page, retentionView());
  state.deleteHandler = async (route) => {
    await route.fulfill({ status: 409, json: { code: "revision_conflict" } });
  };
  await page.goto("/#/media/jobs");
  const failure = page.locator('[data-job-id="old-failure"]');
  await failure.getByRole("button", { name: "查看任务详情" }).click();
  const detail = page.getByRole("complementary", { name: "任务详情" });
  await detail.getByRole("button", { name: "删除记录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("记录已被其他操作更新");
  await expect(detail).toBeVisible();
  await expect(failure).toHaveCount(1);
  await expect(
    detail.getByRole("button", { name: "删除记录", exact: true }),
  ).toBeEnabled();
  expect(state.view.jobs).toHaveLength(3);
  expect(
    state.calls.filter((call) => call.operation === "jobs/delete"),
  ).toHaveLength(1);
  expect(
    state.calls.filter((call) => call.operation === "jobs/list"),
  ).toHaveLength(1);
});

test("deletion waits for the owner and disables repeated clicks", async ({
  page,
}) => {
  const state = await installMediaFixture(page, retentionView());
  let release: () => void = () => undefined;
  let arrived: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  state.deleteHandler = async (route) => {
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    arrived();
    await waiting;
    state.view.jobs = state.view.jobs.filter(
      (job) => job.job_id !== "old-failure",
    );
    await route.fulfill({ json: { deleted: true, job_id: "old-failure" } });
  };
  await page.goto("/#/media/jobs");
  const failure = page.locator('[data-job-id="old-failure"]');
  await failure.getByRole("button", { name: "删除记录", exact: true }).click();
  await started;
  await expect(failure).toBeVisible();
  await expect(
    failure.getByRole("button", { name: "正在删除…", exact: true }),
  ).toBeDisabled();
  await expect(
    page
      .locator('[data-job-id="retained-media"]')
      .getByRole("button", { name: "删除记录", exact: true }),
  ).toBeDisabled();
  expect(
    state.calls.filter((call) => call.operation === "jobs/delete"),
  ).toHaveLength(1);
  release();
  await expect(failure).toHaveCount(0);
  await expect(page.locator('[data-job-id="retained-media"]')).toBeVisible();
});

test("deletion finishes against the current filter after a selection change", async ({
  page,
}) => {
  const state = await installMediaFixture(page, retentionView());
  let release: () => void = () => undefined;
  let arrived: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  state.deleteHandler = async (route) => {
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    arrived();
    await waiting;
    state.view.jobs = state.view.jobs.filter(
      (job) => job.job_id !== "old-failure",
    );
    await route.fulfill({ json: { deleted: true, job_id: "old-failure" } });
  };
  await page.goto("/#/media/jobs");
  await page
    .locator('[data-job-id="old-failure"]')
    .getByRole("button", { name: "删除记录", exact: true })
    .click();
  await started;
  await page.getByLabel("筛选任务").selectOption("finished");
  await expect(page.locator('[data-job-id="retained-media"]')).toBeVisible();
  release();
  await expect(
    page.getByRole("status").filter({ hasText: "任务记录已删除" }),
  ).toBeVisible();
  await expect(page.locator('[data-job-id="retained-media"]')).toBeVisible();
  await expect(page.getByText("正在读取下载任务", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("筛选任务")).toHaveValue("finished");
  expect(
    state.calls.filter((call) => call.operation === "jobs/list").at(-1)!.body
      .states,
  ).toEqual(["completed", "published"]);
});

test("deleting on a later page resets the server cursor and reloads the first page", async ({
  page,
}) => {
  const view = fixtureView();
  view.jobs = Array.from({ length: 42 }, (_, index) =>
    fixtureJob({
      job_id: `page-${index}`,
      title: `历史失败 ${index}`,
      state: "failed",
      can_delete: true,
      can_cancel: false,
    }),
  );
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/jobs");
  await page.getByRole("button", { name: /加载更多任务/ }).click();
  await expect(page.locator("article.media-job")).toHaveCount(40);
  await page
    .locator('[data-job-id="page-25"]')
    .getByRole("button", { name: "删除记录", exact: true })
    .click();
  await expect(page.locator("article.media-job")).toHaveCount(20);
  expect(
    state.calls.filter((call) => call.operation === "jobs/list").at(-1)!.body
      .cursor,
  ).toBeNull();
  await page.getByRole("button", { name: /加载更多任务/ }).click();
  await expect(page.locator("article.media-job")).toHaveCount(40);
  await expect(page.locator('[data-job-id="page-25"]')).toHaveCount(0);
  await expect(page.locator('[data-job-id="page-40"]')).toBeVisible();
  await page.getByRole("button", { name: /加载更多任务/ }).click();
  await expect(page.locator("article.media-job")).toHaveCount(41);
  expect(
    new Set(
      await page
        .locator("article.media-job")
        .evaluateAll((elements) =>
          elements.map((element) => element.getAttribute("data-job-id")),
        ),
    ).size,
  ).toBe(41);
});

test("attention entry preserves unrelated URL query and history state then consumes its local filter", async ({
  page,
}) => {
  const state = await installMediaFixture(page, retentionView());
  await page.addInitScript(() =>
    history.replaceState({ syntheticMarker: "retained" }, ""),
  );
  await page.goto("/?keep=synthetic#/media");
  const link = page.locator(".overview-kpi").filter({ hasText: "需要处理" });
  await expect(link).toHaveAttribute(
    "href",
    "/?keep=synthetic&media-filter=attention#/media/jobs",
  );
  await link.click();
  await expect(page.getByLabel("筛选任务")).toHaveValue("attention");
  await expect(page).toHaveURL(/\?keep=synthetic#\/subscriptions\/2$/);
  expect(await page.evaluate(() => history.state)).toEqual({
    syntheticMarker: "retained",
  });
  expect(
    state.calls.filter((call) => call.operation === "jobs/list").at(-1)!.body
      .states,
  ).toEqual(["failed", "unknown", "waiting_metadata", "auth_required"]);
  await page.goBack();
  await expect(
    page.getByRole("region", { name: "订阅概览", exact: true }),
  ).toBeVisible();
  await page.goForward();
  await expect(page.getByLabel("筛选任务")).toHaveValue("all");
  await page.goto("/?keep=synthetic&media-filter=attention#/media/jobs");
  await expect(page.getByLabel("筛选任务")).toHaveValue("attention");
  await selectMediaSection(page, "链接下载");
  await selectMediaSection(page, "下载中心");
  await expect(page.getByLabel("筛选任务")).toHaveValue("all");
});
