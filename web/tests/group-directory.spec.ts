import { expect, test, type Page } from "@playwright/test";

import { group } from "./fixtures/group-directory";

const actors = [{ id: "actor:a", label: "角色甲" }];
const directory = (
  groups: ReturnType<typeof group>[],
  next_cursor: null | { owner_id: string; group_id: string } = null,
) => ({ available: true, unlocked: true, actors, groups, next_cursor });

async function session(page: Page) {
  await page.addInitScript(() =>
    sessionStorage.setItem("tianshu-memory-role:synthetic-owner", "actor:a"),
  );
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        csrf: "group-directory-synthetic",
        username: "synthetic-owner",
        conversations: [],
        dialogue: { available: false, code: "model_not_configured", model: "" },
      },
    }),
  );
  const content: string[] = [];
  await page.route("**/api/v1/memory/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").at(-1)!;
    if (path !== "state") content.push(path);
    return route.fulfill({
      json: { available: false, code: "memory_not_configured", roles: [] },
    });
  });
  return content;
}

test("empty group profiles and four modes work here without Memory or a settings detour", async ({
  page,
}, info) => {
  const content = await session(page);
  let row = group();
  const changes: unknown[] = [];
  await page.route("**/api/v1/bot-observation/*", (route) => {
    if (route.request().url().endsWith("/groups"))
      return route.fulfill({ json: directory([row]) });
    const body = route.request().postDataJSON();
    changes.push(body);
    row = {
      ...row,
      revision: row.revision + 1,
      rule: body.rule,
      pending: true,
      state: "unknown",
    };
    return route.fulfill({
      json: {
        group: row,
        connection: {
          pending: true,
          state: "unknown",
          revision: row.revision,
          group_policy: {
            version: 2,
            default: { mode: "disabled", actor_id: null, keywords: [] },
            groups: { [row.group_id]: row.rule },
          },
        },
      },
    });
  });
  await page.goto("/#/groups");
  await expect(
    page.getByRole("heading", { name: "空档案", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".group-directory-list")).toContainText("人数未知");
  const mode = page.getByLabel("此群状态", { exact: true });
  await expect(mode).toHaveValue("disabled");
  await expect(mode.locator("option")).toHaveText([
    "停用",
    "仅观察",
    "启用回复",
    "主动",
  ]);
  await mode.selectOption("observe");
  await expect.poll(() => changes.length).toBe(1);
  await expect(mode).toBeEnabled();
  await mode.selectOption("active");
  await expect.poll(() => changes.length).toBe(2);
  await expect(mode).toBeEnabled();
  await page.getByRole("button", { name: "回复关键词", exact: true }).click();
  await page
    .getByLabel("此群关键词", { exact: true })
    .fill("字面关键词\n[原文].*");
  await page.getByRole("button", { name: "保存关键词" }).click();
  await expect(
    page.getByText("状态已保存，等待宿主确认。", { exact: true }),
  ).toBeVisible();
  expect(changes).toHaveLength(3);
  expect(changes[2]).toMatchObject({
    owner_id: "obs:directory",
    group_id: "20001",
    expected_revision: 3,
    rule: {
      mode: "active",
      actor_id: "actor:a",
      keywords: ["字面关键词", "[原文].*"],
    },
  });
  expect(content).toEqual([]);
  expect(new URL(page.url()).hash).toBe("#/groups");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("group-directory.png"),
    fullPage: true,
  });
});

test("refresh retains loaded group pages", async ({ page }) => {
  await session(page);
  const cursors: (string | null)[] = [];
  let entered!: () => void;
  let release!: () => void;
  const waiting = new Promise<void>((done) => {
    entered = done;
  });
  const resume = new Promise<void>((done) => {
    release = done;
  });
  await page.route("**/api/v1/bot-observation/groups", async (route) => {
    const cursor = route.request().postDataJSON().cursor?.group_id ?? null;
    cursors.push(cursor);
    if (cursors.length === 2) {
      entered();
      await resume;
    }
    const row = group(cursor === null ? "20001" : "20002");
    return route.fulfill({
      json: directory(
        [row],
        cursor === null
          ? { owner_id: row.owner_id, group_id: row.group_id }
          : null,
      ),
    });
  });
  await page.goto("/#/groups");
  await expect(page.getByRole("button", { name: "加载更多群" })).toBeVisible();
  await page.getByRole("button", { name: "刷新群档案" }).click();
  await waiting;
  await page.getByRole("button", { name: "加载更多群" }).click();
  await expect(page.locator(".group-directory-list li")).toHaveCount(2);
  const settled = page.waitForResponse((response) =>
    response.url().endsWith("/memory/state"),
  );
  release();
  await settled;
  await expect(page.locator(".group-directory-list li")).toHaveCount(2);
  await page.getByRole("button", { name: "刷新群档案" }).click();
  await expect.poll(() => cursors.length).toBe(6);
  expect(cursors).toEqual([null, null, null, "20001", null, "20001"]);
  await expect(page.locator(".group-directory-list li")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "加载更多群" })).toHaveCount(0);
});

test("an older metadata response cannot undo the saved group state", async ({
  page,
}) => {
  await session(page);
  let row = group();
  let calls = 0;
  let release!: () => void;
  let entered!: () => void;
  const waiting = new Promise<void>((done) => {
    entered = done;
  });
  const resume = new Promise<void>((done) => {
    release = done;
  });
  await page.route("**/api/v1/bot-observation/*", async (route) => {
    if (route.request().url().endsWith("/groups")) {
      const snapshot = structuredClone(row);
      if (++calls === 2) {
        entered();
        await resume;
      }
      return route.fulfill({ json: directory([snapshot]) });
    }
    const body = route.request().postDataJSON();
    row = { ...row, revision: 2, rule: body.rule };
    return route.fulfill({
      json: {
        group: row,
        connection: {
          pending: false,
          state: "ready",
          revision: 2,
          group_policy: {
            version: 2,
            default: { mode: "disabled", actor_id: null, keywords: [] },
            groups: { [row.group_id]: row.rule },
          },
        },
      },
    });
  });
  try {
    await page.goto("/#/groups");
    await page.getByRole("button", { name: "刷新群档案" }).click();
    await waiting;
    await page.getByLabel("此群状态", { exact: true }).selectOption("observe");
    await expect(
      page.getByText("群状态已保存并由宿主确认。", { exact: true }),
    ).toBeVisible();
    const settled = page.waitForResponse((response) =>
      response.url().endsWith("/memory/state"),
    );
    release();
    await settled;
    await expect(
      page.locator(".group-directory-list button[aria-pressed=true]"),
    ).toContainText("仅观察");
    await expect(page.getByLabel("此群状态", { exact: true })).toHaveValue(
      "observe",
    );
  } finally {
    release();
  }
});
