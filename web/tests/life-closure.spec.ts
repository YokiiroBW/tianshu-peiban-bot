import { test, expect, type Page } from "@playwright/test";
import {
  answer,
  apiPath,
  command,
  coverage,
  emptyPage,
  nativeSession,
  receipt,
  state,
} from "./native-fixture";

const actual = { source_id: "actual", revision: 2, locator: {} };
const goal = {
  id: "direction",
  revision: 4,
  title: "留下两张读后小画",
  motivation: "读书后想试试用画面表达",
  origin: "autonomous",
  reason: "最近一次阅读留下了画面感",
  criteria: {
    kind: "created_artifact",
    capability_id: "images.generate",
    target_count: 2,
  },
  progress: 0.5,
  state: "active",
  priority: 0.6,
  due_at: null,
  matter_id: null,
  source_refs: [actual],
};
const activity = {
  ...state.activity,
  id: "continue-reading",
  revision: 6,
  title: "读到第六段",
  state: "dormant",
  waiting_reason: "continuation_available",
  action_id: "read-original",
  checkpoint_step: 2,
  checkpoint_position: 6000,
  checkpoint_unit: "characters",
  checkpoint_data: {
    capability_id: "content.read",
    completed_action_id: "read-original",
    coverage: { ...coverage, complete: false },
    continuation: {
      capability_id: "content.read",
      arguments: { source: actual, position: 6000 },
    },
  },
  source_refs: [actual],
};
const unknown = {
  ...activity,
  id: "unknown-activity",
  revision: 8,
  title: "等待原画作结果",
  waiting_reason: "outcome_unknown",
  action_id: "unknown-original",
  checkpoint_data: { capability_id: "images.generate" },
};
const diary = {
  id: "diary-unknown",
  revision: 9,
  day: "2026-11-02",
  state: "unknown",
  current_revision: 0,
  published_revision: null,
  generation_id: "diary-attempt",
  reason: "generation_outcome_unknown",
  source_refs: [],
  automatic: {
    state: "unknown",
    day: "2026-11-02",
    timezone: "Asia/Shanghai",
    material_revision: 3,
    reviewed_revision: 2,
    attempt_id: "diary-attempt",
    closed_at: "2026-11-02T15:00:00Z",
    last_reviewed_at: "2026-11-01T15:01:00Z",
    last_error: null,
  },
};
const draft = {
  ...diary,
  id: "diary-draft",
  day: "2026-11-01",
  revision: 5,
  state: "draft",
  current_revision: 1,
  generation_id: null,
  reason: null,
  automatic: {
    ...diary.automatic,
    state: "reviewed",
    day: "2026-11-01",
    material_revision: 2,
    reviewed_revision: 2,
    attempt_id: null,
  },
};

async function setup(page: Page) {
  await page.clock.install({ time: new Date("2026-11-02T02:00:00Z") });
  await nativeSession(page);
  await page.route(apiPath("life/goals"), (route) =>
    answer(route, { ...emptyPage, items: [goal] }),
  );
  await page.route(apiPath("life/activities"), (route) =>
    answer(route, { ...emptyPage, items: [activity, unknown] }),
  );
  await page.route(apiPath("life/diaries"), (route) =>
    answer(route, { ...emptyPage, items: [diary, draft] }),
  );
  await page.route(apiPath("capabilities/discover"), (route) =>
    answer(route, { items: [] }),
  );
  await page.route(apiPath("sources/read"), (route) =>
    answer(route, {
      content: "实际阅读原文",
      representation: "text",
      coverage,
      next_position: null,
      media: null,
    }),
  );
  await page.route(apiPath("life/day-plan"), (route) =>
    answer(route, {
      day: "2026-11-02",
      plan: {
        actor_id: "actor",
        day: "2026-11-02",
        revision: 3,
        timezone: "Asia/Shanghai",
        state: "open",
        reason: null,
        source_refs: [actual],
      },
      slots: [
        {
          id: "slot",
          actor_id: "actor",
          day: "2026-11-02",
          revision: 7,
          kind: "activity",
          state: "planned",
          starts_at: "2026-11-02T05:00:00Z",
          ends_at: "2026-11-02T06:00:00Z",
          activity_id: activity.id,
          title: "继续下午的阅读",
          activity_kind: "reading",
          goal_id: goal.id,
          waiting_reason: "day_plan_slot",
          checkpoint_data: {},
          source_refs: [actual],
        },
        {
          id: "free",
          revision: 2,
          kind: "free",
          state: "planned",
          starts_at: "2026-11-02T06:00:00Z",
          ends_at: "2026-11-02T07:00:00Z",
          title: "留白",
          activity_id: null,
          waiting_reason: null,
          checkpoint_data: {},
        },
      ],
      unfinished: [activity],
      planning: {
        revision: 2,
        reason: "unchanged_decision_material",
        generation_consumed: true,
        pending: false,
        next_due_at: null,
        last_planned_at: null,
      },
    }),
  );
  await page.route(apiPath("capabilities/get"), (route) => {
    const body = route.request().postDataJSON();
    const uncertain = body.action_id === "unknown-original";
    return answer(route, {
      action: { revision: 2 },
      receipt: {
        ...receipt(uncertain ? "unknown" : "succeeded"),
        result: uncertain
          ? {}
          : {
              content: { title: "取到的一段", text: "这是实际读到的正文。" },
              source_refs: [actual],
            },
      },
      result_revision: 2,
      result_observed_at: null,
    });
  });
}

test("life workspace switches goals, local day arrangements and real checkpoint activities", async ({
  page,
}) => {
  await setup(page);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  for (const path of [
    "life/goals.save",
    "life/day-plan.move",
    "life/day-plan.reconsider",
    "life/activities.resume",
  ]) {
    await page.route(apiPath(path), (route) => {
      const body = route.request().postDataJSON();
      writes.push({ path, body });
      return answer(route, command(body, { id: body.id, state: "queued" }, 8));
    });
  }
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "长期目标", exact: true }).click();
  await expect(
    page.getByText("随实际经历与兴趣形成", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("实际进度 50%", { exact: true })).toBeVisible();
  await expect(
    page.getByText("完成条件：取得实际创作产物 2 次", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "调整目标", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "调整目标", exact: true });
  await expect(editor.getByLabel("完成方式", { exact: true })).toBeDisabled();
  await editor
    .getByLabel("为什么在意", { exact: true })
    .fill("想保留阅读后的具体画面");
  await editor.getByRole("button", { name: "保存目标", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toMatchObject({
    id: goal.id,
    expected_revision: 4,
    motivation: "想保留阅读后的具体画面",
  });
  expect(writes[0].body).not.toHaveProperty("origin");
  expect(writes[0].body).not.toHaveProperty("progress");
  expect(writes[0].body).not.toHaveProperty("criteria");
  await page.getByRole("button", { name: "暂放一边", exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].body.state).toBe("paused");
  await page.getByRole("tab", { name: "今日安排", exact: true }).click();
  await expect(page.getByText("自由时间", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "调整开始时间", exact: true }).click();
  await page.locator('input[name="starts_at"]').fill("2026-11-02T13:30");
  await page.getByRole("button", { name: "保存时间", exact: true }).click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2].body).toMatchObject({
    id: "slot",
    expected_revision: 7,
    starts_at: "2026-11-02T05:30:00.000Z",
  });
  await page
    .getByLabel("有了什么新想法？", { exact: true })
    .fill("想晚一点继续阅读");
  await page
    .getByRole("button", { name: "重新考虑今天的安排", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(4);
  expect(writes[3].body).toMatchObject({
    expected_revision: 3,
    reason: "想晚一点继续阅读",
  });
  await page.getByRole("tab", { name: "活动与进度", exact: true }).click();
  const waiting = page
    .locator("article.domain-record")
    .filter({ has: page.getByText(unknown.title, { exact: true }) });
  await expect(
    waiting.getByRole("button", { name: "开始或继续", exact: true }),
  ).toHaveCount(0);
  await waiting.getByText("查看实际结果与产物", { exact: true }).click();
  await expect(
    waiting.getByText("原执行结果尚未确认，外部效果仍未知", { exact: true }),
  ).toBeVisible();
  const continuing = page
    .locator("article.domain-record")
    .filter({ has: page.getByText(activity.title, { exact: true }) });
  await continuing
    .getByRole("button", { name: "从实际位置继续", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(5);
  expect(writes[4].body).toMatchObject({
    id: activity.id,
    expected_revision: 6,
  });
  expect(writes.every((item) => item.body.actor_id === "actor")).toBeTruthy();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
});

test("automatic diary unknown remains unknown and controls use original diary CAS", async ({
  page,
}) => {
  await setup(page);
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  for (const path of ["life/diaries/reconcile", "life/diaries/cancel"])
    await page.route(apiPath(path), (route) => {
      const body = route.request().postDataJSON();
      writes.push({ path, body });
      return answer(
        route,
        command(body, { id: diary.id, state: "unknown" }, 9),
      );
    });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "日记", exact: true }).click();
  await page
    .getByRole("button", { name: "打开 2026-11-02", exact: true })
    .click();
  const sheet = page.getByRole("dialog", { name: "2026-11-02", exact: true });
  await expect(
    sheet.getByText("材料版本 3 · 已复核到 2 · Asia/Shanghai", { exact: true }),
  ).toBeVisible();
  await expect(
    sheet.getByText("保留原模型请求，只核对原结果；尚未作为写作成功。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    sheet.getByRole("button", { name: "发布当前修订", exact: true }),
  ).toHaveCount(0);
  await sheet
    .getByRole("button", { name: "核对原模型任务", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toMatchObject({ id: diary.id, expected_revision: 9 });
  await sheet
    .getByRole("button", { name: "停止后续写入", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].body).toMatchObject({ id: diary.id, expected_revision: 9 });
  expect(
    writes.every((item) => item.path !== "life/diaries/request"),
  ).toBeTruthy();
});

test("a concurrent day slot change preserves the edit and shows a readable CAS error", async ({
  page,
}) => {
  await setup(page);
  let writes = 0;
  await page.route(apiPath("life/day-plan.move"), (route) => {
    writes++;
    return route.fulfill({
      status: 409,
      json: {
        error: {
          code: "revision_conflict",
          message: "revision_conflict",
          details: { revision: 8 },
          request_id: "fixture",
        },
      },
    });
  });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "今日安排", exact: true }).click();
  await page.getByRole("button", { name: "调整开始时间", exact: true }).click();
  await page.locator('input[name="starts_at"]').fill("2026-11-02T13:30");
  await page.getByRole("button", { name: "保存时间", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    "这份安排已经更新，请刷新后按最新内容调整。",
  );
  await expect(page.locator('input[name="starts_at"]')).toHaveValue(
    "2026-11-02T13:30",
  );
  expect(writes).toBe(1);
});

test("a diary draft publishes the selected revision and explicit readers only", async ({
  page,
}) => {
  await setup(page);
  await page.route(apiPath("life/diaries/read"), (route) =>
    answer(route, {
      diary: draft,
      version: {
        revision: 1,
        content: "只写下今天真正读到的一段。",
        created_at: "2026-11-01T15:00:00Z",
        recipe: { source_refs: [], coverage },
      },
    }),
  );
  await page.route(apiPath("identity/publication-recipients"), (route) =>
    answer(route, {
      ...emptyPage,
      items: [{ principal_id: "reader", display_name: "指定读者" }],
    }),
  );
  const published: Record<string, unknown>[] = [];
  await page.route(apiPath("life/diaries/publish"), (route) => {
    const body = route.request().postDataJSON();
    published.push(body);
    return answer(
      route,
      command(body, { id: draft.id, published_revision: 1 }, 6),
    );
  });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "日记", exact: true }).click();
  await page
    .getByRole("button", { name: "打开 2026-11-01", exact: true })
    .click();
  const sheet = page.getByRole("dialog", { name: "2026-11-01", exact: true });
  await expect(
    sheet.getByText("只写下今天真正读到的一段。", { exact: true }),
  ).toBeVisible();
  expect(published).toHaveLength(0);
  await sheet.getByRole("checkbox", { name: "指定读者", exact: true }).check();
  await sheet
    .getByRole("button", { name: "发布当前修订", exact: true })
    .click();
  await expect.poll(() => published.length).toBe(1);
  expect(published[0]).toMatchObject({
    id: draft.id,
    expected_revision: 5,
    reader_ids: ["reader"],
    conversation_ids: [],
  });
});

test("video continuation uses returned original identity and keeps sampling uncertainty", async ({
  page,
}) => {
  await setup(page);
  const calls: Record<string, unknown>[] = [];
  const next = {
    media: { media_id: "original-video" },
    source: actual,
    start: 30,
    reading_state: { consumption_id: "original-consumption" },
  };
  await page.route(apiPath("content/video.observe"), (route) => {
    const body = route.request().postDataJSON();
    calls.push(body);
    const result = {
      analysis: "实际采样中看到一次镜头切换。",
      consumption: {
        source: actual,
        ranges: [{ start: 0, end: 30 }],
        model_input_modalities: ["image", "audio"],
        frame_times: [0, 15, 29],
        omitted: ["sampling_only"],
        analysis_completed: true,
        requested_completed: false,
        next_position: { start: 999, end: 1000 },
      },
      continuation: { capability_id: "video.observe", arguments: next },
    };
    return answer(route, {
      ...command(body, result),
      receipt: { ...receipt("succeeded"), result },
    });
  });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "阅读与学习", exact: true }).click();
  await page
    .getByRole("button", { name: "寻找内容或打开网页", exact: true })
    .click();
  await page.getByText("实际观看视频片段", { exact: true }).click();
  await page
    .getByLabel("视频地址", { exact: true })
    .fill("https://example.test/video");
  await page.getByRole("button", { name: "观看指定窗口", exact: true }).click();
  await expect(
    page.getByText(
      "本次材料已完成分析。分段或采样处理不表示逐帧理解了整部视频。",
      { exact: true },
    ),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "从实际位置继续本次窗口", exact: true })
    .click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toMatchObject(next);
  expect(calls[1]).not.toHaveProperty("url");
  expect(calls[1].start).toBe(30);
});

test("life album keeps generated image, scene conflict notice and unknown result separate", async ({
  page,
}) => {
  await setup(page);
  const artifact = {
    artifact_id: "photo",
    media: {
      media_id: "photo",
      uri: "owned:photo",
      mime_type: "image/png",
      size_bytes: 68,
      sha256: null,
      duration_ms: null,
      width: 1,
      height: 1,
    },
    source_refs: [],
    coverage: null,
    metadata: {},
  };
  await page.route(apiPath("images/view"), (route) =>
    answer(route, {
      engines: [],
      profiles: [],
      workflows: [],
      identity: null,
      outfits: [],
      scene: null,
      jobs: [
        {
          action_id: "photo",
          attempt_id: "photo-attempt",
          execution: "succeeded",
          effect: "confirmed",
          delivery: "not_requested",
          artifacts: [artifact],
          reason: null,
          result: {
            consistency: {
              state: "conflict",
              checks: [
                {
                  field: "lighting",
                  status: "conflict",
                  observed: "画面偏白天",
                },
              ],
              regeneration_attempts: 0,
            },
            sharing: {
              decision: "archive",
              notice: "这张图已生成，但光照与当时的夜间场景不同。",
            },
          },
        },
        {
          action_id: "unknown-original",
          attempt_id: "unknown-attempt",
          execution: "unknown",
          effect: "unknown",
          artifacts: [],
          reason: null,
          result: {
            consistency: {
              state: "unknown",
              checks: [],
              regeneration_attempts: 0,
            },
          },
        },
      ],
      album: [artifact],
    }),
  );
  await page.route("**/api/v1/capabilities/media/photo?*", (route) =>
    route.fulfill({
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lV0AAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
  await page.route(apiPath("life/outfits"), (route) =>
    answer(route, emptyPage),
  );
  let submissions = 0;
  await page.route(apiPath("images/generate"), (route) => {
    submissions++;
    return route.abort();
  });
  await page.goto("/#/roles/life");
  await page.getByRole("tab", { name: "生图与相册", exact: true }).click();
  await expect(
    page.getByText("成图与当时场景有差异", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("这张图已生成，但光照与当时的夜间场景不同。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("本次作品留在相册，没有因生成完成而自动分享。", {
      exact: true,
    }),
  ).toBeVisible();
  const uncertain = page
    .locator("article.domain-record")
    .filter({ has: page.getByText("结果未知", { exact: true }) });
  await expect(uncertain.getByText("生成完成", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("img", { name: "媒体原件", exact: true }).first(),
  ).toBeVisible();
  expect(submissions).toBe(0);
});
