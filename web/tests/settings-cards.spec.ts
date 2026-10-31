import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  fixtureSubscription,
  fixtureView,
  installMediaFixture,
} from "./media.fixtures";

async function start(page: Page) {
  const view = fixtureView();
  view.subscriptions = [fixtureSubscription()];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await expect(
    page.getByRole("heading", { name: "已有收藏夹订阅", exact: true }),
  ).toBeVisible();
  return state;
}
async function source(page: Page) {
  await page.getByLabel("订阅名称").fill("弹出卡片订阅");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
  await page.getByRole("button", { name: "下一步", exact: true }).click();
}
async function destination(page: Page) {
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect(
    page.getByRole("button", { name: "保存订阅", exact: true }),
  ).toBeEnabled();
}

test("subscription card preserves the list and returns focus without writing on close", async ({
  page,
}, info) => {
  const state = await start(page);
  const list = page.locator(".media-subscriptions");
  const top = await list.evaluate((e) => e.getBoundingClientRect().top);
  const trigger = page.getByRole("button", { name: "新建订阅", exact: true });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "新建订阅", exact: true });
  await expect(dialog).toBeVisible();
  expect(await dialog.evaluate((e) => e.matches(":modal"))).toBe(true);
  expect(await list.evaluate((e) => e.getBoundingClientRect().top)).toBeCloseTo(
    top,
    0,
  );
  await page.getByLabel("订阅名称").fill("尚未保存");
  expect(
    (await new AxeBuilder({ page }).include("dialog[open]").analyze())
      .violations,
  ).toEqual([]);
  await page.screenshot({
    path: info.outputPath("subscription-card.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(
    state.calls.some((call) => call.operation === "subscriptions/save"),
  ).toBe(false);
  await trigger.click();
  await expect(page.getByLabel("订阅名称")).toHaveValue("");
  await page.mouse.click(2, 2);
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(
    await page.evaluate(() => getComputedStyle(document.body).overflow),
  ).not.toBe("hidden");
});

test("nested advanced card keeps keyboard focus, scroll and saved subscription values", async ({
  page,
}, info) => {
  const state = await start(page);
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await source(page);
  const parent = page.getByRole("dialog", { name: "新建订阅", exact: true });
  const advanced = parent.locator(
    ".media-stream-options > .detail-card-trigger",
  );
  await advanced.click();
  const nested = page.locator("dialog:modal").last();
  await expect(page.locator("dialog:modal")).toHaveCount(2);
  await nested
    .getByRole("combobox", { name: "最低视频质量", exact: true })
    .selectOption("64");
  await nested.getByRole("button").last().focus();
  await page.keyboard.press("Tab");
  expect(await nested.evaluate((e) => e.contains(document.activeElement))).toBe(
    true,
  );
  const scroll = await page.evaluate(() => scrollY);
  await nested.locator(".drawer-content").evaluate((e) => {
    e.scrollTop = e.scrollHeight;
  });
  expect(await page.evaluate(() => scrollY)).toBe(scroll);
  await page.screenshot({
    path: info.outputPath("advanced-card.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog:modal")).toHaveCount(1);
  await expect(parent).toBeVisible();
  await expect(advanced).toBeFocused();
  await advanced.click();
  await expect(
    page
      .locator("dialog:modal")
      .last()
      .getByRole("combobox", { name: "最低视频质量", exact: true }),
  ).toHaveValue("64");
  await page.keyboard.press("Escape");
  await destination(page);
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(page.locator("dialog:modal")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "弹出卡片订阅", exact: true }),
  ).toBeVisible();
  const saved = state.calls.find((c) => c.operation === "subscriptions/save")!;
  expect(
    (saved.body.quality as { stream_options: { video_min_quality: string } })
      .stream_options.video_min_quality,
  ).toBe("64");
});

test("a failed save stays visible inside the card and keeps entered values", async ({
  page,
}) => {
  await start(page);
  await page.route("**/api/v1/media/subscriptions/save", (route) =>
    route.fulfill({ status: 409, json: { code: "version_conflict" } }),
  );
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await source(page);
  await destination(page);
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  const card = page.getByRole("dialog", { name: "新建订阅", exact: true });
  await expect(card.getByRole("alert")).toBeVisible();
  await expect(card).toBeVisible();
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await expect(page.getByLabel("订阅名称")).toHaveValue("弹出卡片订阅");
});

test("role settings open in a card while the directory stays in place", async ({
  page,
}, info) => {
  await installMediaFixture(page);
  await page.route("**/api/v1/roles/view", (route) =>
    route.fulfill({
      json: {
        roles: [
          {
            actor_id: "actor:card",
            version: 1,
            name: "卡片测试角色",
            profile_id: null,
            profile_version: null,
            profile_revision: null,
            provider_id: null,
            provider_revision: null,
            enabled: true,
            capabilities: ["dialogue", "memory.read"],
            state: "active",
            stage: "active",
            error_code: null,
            client_request_id: "synthetic",
            legacy: false,
          },
        ],
        legacy_roles: [],
        profiles: [],
        providers: [],
        default_available: false,
        capabilities: [],
      },
    }),
  );
  await page.goto("/#/roles");
  const entry = page.getByRole("button").filter({ hasText: "卡片测试角色" });
  await expect(entry).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const before = await entry.boundingBox();
  await entry.click();
  const card = page.getByRole("dialog", {
    name: "角色设置 · 卡片测试角色",
    exact: true,
  });
  await expect(card.getByLabel("名称", { exact: true })).toHaveValue(
    "卡片测试角色",
  );
  expect(await entry.boundingBox()).toEqual(before);
  expect(await card.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
  await page.screenshot({
    path: info.outputPath("role-settings-card.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(card).not.toBeVisible();
  await expect(entry).toBeFocused();
});
