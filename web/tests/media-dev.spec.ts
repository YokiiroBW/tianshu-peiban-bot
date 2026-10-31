import { test, expect } from "@playwright/test";
import {
  installMediaFixture,
  selectMediaSection,
  expectMediaSection,
} from "./media.fixtures";

test("media StrictMode remount reads facts and clears cancelled loading", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await expectMediaSection(page, "链接下载");
  await selectMediaSection(page, "下载中心");
  await expect(
    page.getByRole("heading", { name: "隔离样例 · 城市与山海", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    location.hash = "#/workbench";
  });
  await expect(page.getByRole("navigation", { name: "订阅页面" })).toHaveCount(
    0,
  );
  await page.evaluate(() => {
    location.hash = "#/media/download";
  });
  await expectMediaSection(page, "链接下载");
  expect(
    state.calls.filter((call) => call.operation === "view").length,
  ).toBeGreaterThanOrEqual(2);
});
