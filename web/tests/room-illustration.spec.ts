import { test, expect } from "@playwright/test";

import { nativeSession } from "./native-fixture";

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-31T06:00:00Z"));
  await nativeSession(page);
});

test("illustration loads in authenticated room, controls change pixels and state survives mode switch", async ({
  page,
}, info) => {
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  await page.goto("/#/roles/room");
  const canvas = page.locator("canvas");
  await expect(canvas).toHaveAttribute("data-ready", "true");
  expect(requests.some((url) => /\/renderer-/.test(url))).toBe(false);
  await expect(page.locator(".room-fallback")).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath("room-day.png"),
    fullPage: true,
  });
  const day = await canvas.screenshot();
  await page.getByRole("button", { name: "夜晚", exact: true }).click();
  await expect(canvas).toHaveAttribute("data-day", "0.000");
  expect((await canvas.screenshot()).equals(day)).toBe(false);
  await page.getByRole("button", { name: /环境控制/ }).click();
  const lamp = page.getByRole("slider", { name: "床头灯亮度", exact: true });
  await lamp.fill("100");
  await expect
    .poll(() => canvas.screenshot().then((b) => b.toString("base64")))
    .not.toBe(day.toString("base64"));
  await page.screenshot({
    path: info.outputPath("room-night.png"),
    fullPage: true,
  });
  const lit = await canvas.screenshot();
  await lamp.fill("0");
  await expect
    .poll(() => canvas.screenshot().then((b) => b.equals(lit)))
    .toBe(false);
  await page.getByRole("button", { name: "关闭卡片", exact: true }).click();
  await page.getByRole("button", { name: "午后", exact: true }).click();
  await page.getByRole("button", { name: /环境控制/ }).click();
  await expect(canvas).toHaveAttribute("data-day", "1.000");
  await page.getByRole("slider", { name: "纱帘开度", exact: true }).fill("0");
  const curtains = await canvas.screenshot();
  await page.getByRole("slider", { name: "纱帘开度", exact: true }).fill("100");
  await expect
    .poll(() => canvas.screenshot().then((b) => b.equals(curtains)))
    .toBe(false);
  const character = await canvas.screenshot();
  await page.getByLabel("显示角色姿态", { exact: true }).uncheck();
  await expect
    .poll(() => canvas.screenshot().then((b) => b.equals(character)))
    .toBe(false);
  await page.getByRole("button", { name: "关闭卡片", exact: true }).click();
  await page.getByRole("button", { name: "原环境预览", exact: true }).click();
  await page.getByRole("button", { name: /环境控制/ }).click();
  await expect(canvas).toHaveAttribute("data-frames", /[1-9]/);
  await expect(
    page.getByRole("slider", { name: "书桌灯亮度", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭卡片", exact: true }).click();
  await page.getByRole("button", { name: "返回插画小屋", exact: true }).click();
  await page.getByRole("button", { name: /环境控制/ }).click();
  await expect(canvas).toHaveAttribute("data-ready", "true");
  await expect(lamp).toHaveValue("0");
  await expect(
    page.getByRole("slider", { name: "纱帘开度", exact: true }),
  ).toHaveValue("100");
  await page.getByRole("button", { name: "关闭卡片", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    page.getByRole("link", { name: "查看日程与日记" }),
  ).toHaveAttribute("href", "#/roles/life");
});

test("layer failure can retry and leaving disposes the canvas", async ({
  page,
}) => {
  await page.route(
    "**/room-illustration-assets/character-reading-v1.png",
    (r) => r.abort(),
  );
  await page.goto("/#/roles/room");
  await expect(
    page.getByRole("heading", { name: "小屋素材暂时未能加载" }),
  ).toBeVisible();
  await page.unroute("**/room-illustration-assets/character-reading-v1.png");
  await page.getByRole("button", { name: "重试画面" }).click();
  await expect(page.locator("canvas")).toHaveAttribute("data-ready", "true");
  await page.evaluate(() => (location.hash = "#/workbench"));
  await expect(page.locator("canvas")).toHaveCount(0);
  await page.evaluate(() => (location.hash = "#/roles/room"));
  await expect(page.locator("canvas")).toHaveCount(1);
  await expect(page.locator("canvas")).toHaveAttribute("data-ready", "true");
});

test("system and app reduced motion stop animation", async ({ page }) => {
  await page.goto("/#/roles/room");
  const canvas = page.locator("canvas");
  await expect(canvas).toHaveAttribute("data-motion", "reduced");
  const frames = await canvas.getAttribute("data-frames");
  await page.waitForTimeout(250);
  await expect(canvas).toHaveAttribute("data-frames", frames!);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(canvas).toHaveAttribute("data-motion", "normal");
  await page.getByRole("button", { name: "外观设置", exact: true }).click();
  await page.getByRole("checkbox", { name: /减少动态/ }).check();
  await expect(canvas).toHaveAttribute("data-motion", "reduced");
});
