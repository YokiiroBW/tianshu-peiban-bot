import { test, expect, type Page, type Route } from "@playwright/test";
import { sharedProvenance } from "./fixtures/memory-records";

import {
  ROLES,
  GROUPS,
  ROLE_B_GROUP,
  HASH,
  LOCATOR,
  record,
  insights,
  type Body,
} from "./fixtures/group-insights";

type Seen = { path: string; body: Body };

async function answer(route: Route, value: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

async function mockApi(
  page: Page,
  intercept?: (route: Route, seen: Seen) => Promise<boolean>,
) {
  const sent: Seen[] = [];
  await page.route("**/api/v1/session", (route) =>
    answer(route, {
      authenticated: true,
      csrf: "ts316-browser-only",
      username: "ts316-synthetic-owner",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
  let revision = 1;
  const rules: Record<
    string,
    { mode: string; actor_id: string; keywords: string[] }
  > = {
    "20001": { mode: "observe", actor_id: "actor:a", keywords: [] },
    "20002": { mode: "observe", actor_id: "actor:a", keywords: [] },
  };
  const directoryGroup = (index: number) => {
    const source = GROUPS[index],
      groupId = String(20001 + index),
      rule = rules[groupId];
    return {
      id: `directory-${index}`,
      owner_id: "obs:ui",
      owner_name: "合成 BOT",
      account_id: "90001",
      group_id: groupId,
      name: source.label,
      member_count: 10,
      max_member_count: 200,
      first_seen_at: 1792418400,
      last_seen_at: 1792504799,
      revision,
      rule,
      pending: false,
      read_enabled: true,
      has_archive: true,
      state: "ready",
      ordinary_claim_available: true,
      memory_group_id:
        rule.actor_id === "actor:b" ? ROLE_B_GROUP.id : source.id,
    };
  };
  await page.route("**/api/v1/bot-observation/*", async (route) => {
    const path = new URL(route.request().url()).pathname.split("/").at(-1);
    if (path === "groups")
      return answer(route, {
        available: true,
        unlocked: true,
        actors: ROLES,
        groups:
          route.request().postDataJSON().role_id === "actor:b"
            ? [
                {
                  ...directoryGroup(0),
                  rule: { ...rules["20001"], actor_id: "actor:b" },
                  memory_group_id: ROLE_B_GROUP.id,
                },
              ]
            : GROUPS.map((_, index) => directoryGroup(index)),
        next_cursor: null,
      });
    if (path === "group-policy") {
      const body = route.request().postDataJSON();
      rules[body.group_id] = body.rule;
      revision++;
      return answer(route, {
        group: directoryGroup(Number(body.group_id) - 20001),
        connection: {
          pending: false,
          state: "ready",
          revision,
          group_policy: {
            version: 2,
            default: { mode: "disabled", actor_id: null, keywords: [] },
            groups: rules,
          },
        },
      });
    }
    throw new Error(`Unexpected directory operation ${path}`);
  });
  await page.route("**/api/v1/memory/*", async (route) => {
    const path = new URL(route.request().url()).pathname.split("/").at(-1)!;
    if (path === "state")
      return answer(route, {
        available: true,
        code: "ready",
        actor_id: "actor:a",
        roles: ROLES,
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    const body = route.request().postDataJSON() as Body;
    expect(body.role_version).toBe(body.role_id === "actor:a" ? 0 : 2);
    const seen = { path, body };
    sent.push(seen);
    if (intercept && (await intercept(route, seen))) return;
    if (path === "groups")
      return answer(route, {
        available: true,
        groups: body.role_id === "actor:b" ? [ROLE_B_GROUP] : GROUPS,
        chat_configured: true,
      });
    if (path === "group-insights") return answer(route, insights(body));
    if (path === "group-history")
      return answer(route, {
        records: [record(body.cursor ? 2 : 1)],
        next_cursor: body.cursor ? null : "history:next-page",
        visibility: { withdrawn: 1, unsupported: 0, archive_returned: 2 },
      });
    if (path === "group-evidence")
      return answer(route, {
        status: "archive_current",
        target: record(),
        context: { before: [], after: [record(2)] },
      });
    throw new Error(`unexpected memory UI operation ${path}`);
  });
  return sent;
}

function signal() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function answerLate(route: Route, value: unknown) {
  try {
    await answer(route, value);
  } catch (error) {
    // The prior fetch must be aborted by the real component. Other routing failures are errors.
    if (!route.request().failure()?.errorText.includes("ERR_ABORTED"))
      throw error;
  }
}

test("group heat, explicit interaction evidence, person topics and zoom remain separate", async ({
  page,
}, testInfo) => {
  const sent = await mockApi(page);
  await page.goto("/#/groups");
  await expect(
    page.locator(".group-directory-list button[aria-pressed=true]"),
  ).toContainText("甲群");
  await expect(page.locator(".group-metrics")).toContainText("6 条已扫描消息");
  await expect(page.locator(".group-metrics")).toContainText("2 个发言账号");
  await expect(page.getByText(/互动结构不可解析 1 条/)).toBeVisible();
  await expect(page.locator(".group-heat-bucket")).toHaveCount(2);
  const interaction = page.getByRole("group", {
    name: "定向互动图",
    exact: true,
  });
  await interaction
    .getByRole("button", { name: "QQ 10001", exact: true })
    .click();
  await expect(page.getByText(/10002 · 2 次定向互动/)).toBeVisible();
  await page.getByRole("button", { name: "证据 1", exact: true }).click();
  const evidence = page.getByRole("article", { name: "原始证据" });
  await expect(
    evidence.getByText("10001 当时的完整原话。", { exact: true }),
  ).toBeVisible();
  await expect(
    evidence.getByText("10002 随后的完整原话。", { exact: true }),
  ).toBeVisible();
  expect(
    sent.find((item) => item.path === "group-evidence")?.body,
  ).toMatchObject({
    group_id: "group-a",
    locator: LOCATOR,
    stored_content_sha256: HASH,
  });

  await page.getByRole("button", { name: "话题点云", exact: true }).click();
  const topics = page.getByRole("group", { name: "整理话题点云", exact: true });
  await expect(
    topics.getByRole("button", { name: "reader-a", exact: true }),
  ).toBeVisible();
  await expect(
    topics.getByRole("button", { name: "QQ 10001", exact: true }),
  ).toHaveCount(0);
  await topics
    .getByRole("button", { name: "角色甲 / 甲群：读书讨论", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "当前话题", exact: true }),
  ).toBeVisible();
  const selection = page.locator("article.group-selection").filter({
    has: page.getByRole("heading", { name: "当前话题", exact: true }),
  });
  await expect(selection).toContainText("person:reader-a、person:reader-b");
  await selection.getByText("查看原归属依据", { exact: true }).click();
  await expect(selection).toContainText("人物 person:reader-a");
  await expect(selection).toContainText("BOT actor:a");

  const zoom = page.getByLabel("图缩放", { exact: true });
  await zoom.focus();
  await zoom.press("End");
  await expect(zoom).toHaveValue("3");
  await expect(topics.locator("g[transform]").first()).toHaveAttribute(
    "transform",
    /scale\(3\)/,
  );
  await page.getByRole("button", { name: "还原视图", exact: true }).click();
  await expect(zoom).toHaveValue("1");
  const graphBox = await page.locator(".group-graph-scroll").boundingBox();
  expect(graphBox).not.toBeNull();
  expect(graphBox!.x).toBeGreaterThanOrEqual(0);
  expect(graphBox!.x + graphBox!.width).toBeLessThanOrEqual(
    page.viewportSize()!.width + 1,
  );
  await page.screenshot({
    path: testInfo.outputPath(`group-insights-${testInfo.project.name}.png`),
    fullPage: true,
  });
});

test("group portrait shows organization metadata while keeping group evidence scoped", async ({
  page,
}) => {
  const sent = await mockApi(page, async (route, seen) => {
    if (seen.path !== "group-insights") return false;
    const value = insights(seen.body);
    value.records.data.items[0] = {
      ...value.records.data.items[0],
      provenance: sharedProvenance,
    } as (typeof value.records.data.items)[0];
    await answer(route, value);
    return true;
  });
  await page.goto("/#/groups");
  const record = page.locator(".memory-records > li");
  await expect(record).toContainText("已更新 1 条既有记忆");
  await record.getByText("查看整理依据", { exact: true }).click();
  await expect(record).toContainText("本条未附可展示的原话摘录。");
  await expect(record).toContainText("关联了 2 条已有记忆");
  expect(sent.find((row) => row.path === "group-insights")?.body).toMatchObject(
    { role_id: "actor:a", group_id: "group-a" },
  );
});

test("history next page retains the submitted filters rather than unsent form edits", async ({
  page,
}) => {
  const sent = await mockApi(page);
  await page.goto("/#/groups");
  await expect(
    page.getByText("角色甲 / 甲群 当前群画像", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("历史关键词", { exact: true }).fill("读书");
  await page.getByLabel("历史作者ID", { exact: true }).fill("10001");
  await page
    .getByLabel("历史开始时间", { exact: true })
    .fill("2026-10-20T09:00");
  await page
    .getByLabel("历史结束时间", { exact: true })
    .fill("2026-10-21T09:00");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(
    page.getByText("10001 当时的完整原话。", { exact: true }),
  ).toBeVisible();
  const first = sent.find((item) => item.path === "group-history")!.body;
  expect(first).toMatchObject({
    role_id: "actor:a",
    role_version: 0,
    group_id: "group-a",
    cursor: null,
    limit: 20,
  });
  expect(first.filters).toEqual({
    keyword: "读书",
    sender_id: "10001",
    start_timestamp: 1792458000,
    end_timestamp: 1792544400,
  });
  await page.getByLabel("历史关键词", { exact: true }).fill("尚未提交的新词");
  await page.getByLabel("历史作者ID", { exact: true }).fill("10002");
  await page
    .getByLabel("历史开始时间", { exact: true })
    .fill("2026-10-19T09:00");
  await page.getByRole("button", { name: "下一页历史", exact: true }).click();
  await expect(
    page.getByText("10002 随后的完整原话。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("10001 当时的完整原话。", { exact: true }),
  ).toBeVisible();
  const pages = sent.filter((item) => item.path === "group-history");
  expect(pages).toHaveLength(2);
  expect(pages[1].body).toEqual({ ...first, cursor: "history:next-page" });
  expect(
    pages.every(
      (item) =>
        !("channel" in item.body) &&
        !("account" in item.body) &&
        !("origin" in item.body),
    ),
  ).toBe(true);
});

test("graph floats, pans, drags and zooms inside the independently scrolling detail pane", async ({
  page,
}, info) => {
  test.skip(
    info.project.name !== "desktop",
    "Pointer interactions use the desktop canvas",
  );
  await mockApi(page);
  await page.goto("/#/groups");
  const canvas = page.getByRole("group", { name: "定向互动图", exact: true });
  const node = canvas.getByRole("button", { name: "QQ 10001", exact: true });
  await node.scrollIntoViewIfNeeded();
  const first = await node.getAttribute("transform");
  await expect.poll(() => node.getAttribute("transform")).not.toBe(first);
  await page.getByRole("button", { name: "暂停浮动", exact: true }).click();
  const paused = await node.getAttribute("transform");
  const box = (await node.locator("circle").first().boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 70, box.y + 35, { steps: 8 });
  await page.mouse.up();
  await expect(node).not.toHaveAttribute("transform", paused!);
  const view = canvas.locator(":scope > g");
  const initialView = await view.getAttribute("transform");
  const area = (await canvas.boundingBox())!;
  await page.mouse.move(area.x + 20, area.y + 20);
  await page.mouse.down();
  await page.mouse.move(area.x + 70, area.y + 45, { steps: 5 });
  await page.mouse.up();
  await expect(view).not.toHaveAttribute("transform", initialView!);
  await page.mouse.wheel(0, -120);
  await expect
    .poll(async () =>
      Number(await page.getByLabel("图缩放", { exact: true }).inputValue()),
    )
    .toBeGreaterThan(1);
  await expect
    .poll(() =>
      page.evaluate(() => ({
        page: document.documentElement.scrollHeight <= innerHeight + 1,
        outer: document.querySelector(".page-content")!.scrollTop,
        detail: document.querySelector(".group-directory-content")!.scrollTop,
        list: document.querySelector(".group-directory-list ul")!.scrollTop,
      })),
    )
    .toMatchObject({ page: true, outer: 0, list: 0 });
  expect(
    await page
      .locator(".group-directory-content")
      .evaluate((el) => el.scrollTop),
  ).toBeGreaterThan(0);
  await page.screenshot({ path: info.outputPath("graph-interaction.png") });
});

test("a late old-group response cannot replace the newly selected group", async ({
  page,
}) => {
  const entered = signal(),
    release = signal(),
    finished = signal();
  let held = false;
  await mockApi(page, async (route, { path, body }) => {
    if (path !== "group-insights" || body.group_id !== "group-a" || held)
      return false;
    held = true;
    entered.resolve();
    await release.promise;
    try {
      await answerLate(route, insights(body));
    } finally {
      finished.resolve();
    }
    return true;
  });
  try {
    await page.goto("/#/groups");
    await entered.promise;
    await page
      .locator(".group-directory-list")
      .getByRole("button", { name: /乙群/ })
      .click();
    await expect(
      page.getByText("角色甲 / 乙群 当前群画像", { exact: true }),
    ).toBeVisible();
    release.resolve();
    await finished.promise;
    await expect(
      page.locator(".group-directory-list button[aria-pressed=true]"),
    ).toContainText("乙群");
    await expect(
      page.getByText("角色甲 / 乙群 当前群画像", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("角色甲 / 甲群 当前群画像", { exact: true }),
    ).toHaveCount(0);
  } finally {
    release.resolve();
  }
});

test("a late old-role response cannot populate a remounted group view", async ({
  page,
}) => {
  const entered = signal(),
    release = signal(),
    finished = signal();
  let held = false;
  await mockApi(page, async (route, { path, body }) => {
    if (path !== "group-insights" || body.role_id !== "actor:a" || held)
      return false;
    held = true;
    entered.resolve();
    await release.promise;
    try {
      await answerLate(route, insights(body));
    } finally {
      finished.resolve();
    }
    return true;
  });
  try {
    await page.goto("/#/groups");
    await entered.promise;
    await page.getByLabel("查看角色", { exact: true }).selectOption("actor:b");
    await expect(
      page.getByText("角色乙 / 甲群（角色乙） 当前群画像", { exact: true }),
    ).toBeVisible();
    release.resolve();
    await finished.promise;
    await expect(page.getByLabel("查看角色", { exact: true })).toHaveValue(
      "actor:b",
    );
    await expect(
      page.getByText("角色乙 / 甲群（角色乙） 当前群画像", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("角色甲 / 甲群 当前群画像", { exact: true }),
    ).toHaveCount(0);
  } finally {
    release.resolve();
  }
});
