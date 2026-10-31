/** Real console, network synthesis fixture, audio files and reading grants. */
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("chapter audio generates over real HTTP, reuses files, plays and shares only while enabled", async ({
  page,
  request,
}, info) => {
  const fixture = JSON.parse(
    readFileSync(resolve(".runtime/book-browser-input.json"), "utf8"),
  );
  test.skip(
    !fixture.audio_url,
    "Set BOOK_AUDIO_FIXTURE=1 for real synthesis protocol fixture",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#/media/books");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "整理设置", exact: true }).click();
  await page.getByLabel("书籍存档目录").fill(fixture.directory);
  await page.getByRole("button", { name: "保存整理设置" }).click();
  await page.getByRole("button", { name: "听书设置", exact: true }).click();
  await page
    .getByRole("combobox", { name: "语音服务", exact: true })
    .selectOption("local");
  await page.getByLabel("本地服务地址").fill(fixture.audio_url);
  await page.getByLabel("模型名称", { exact: true }).fill("synthetic-tone");
  await page.getByLabel("音色名称", { exact: true }).fill("fixture");
  await page.getByRole("button", { name: "保存听书设置" }).click();
  await expect(page.getByText(/听书配置已保存/)).toBeVisible();
  await page.getByRole("button", { name: "书源管理", exact: true }).click();
  await page.getByRole("button", { name: "导入书源", exact: true }).click();
  await page.getByLabel("规则 JSON").fill(JSON.stringify(fixture.source));
  await page.getByRole("button", { name: "导入规则", exact: true }).click();
  await page.getByRole("button", { name: "关闭导入阅读书源" }).click();
  await page
    .getByRole("button", { name: "聚合搜索", exact: true })
    .first()
    .click();
  await page.getByLabel("书名、作者或 ISBN", { exact: true }).fill("山海");
  await page
    .locator("form.book-search-form")
    .getByRole("button", { name: "聚合搜索" })
    .click();
  await page.getByRole("button", { name: "预览与资料" }).first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.locator(".book-prose")).toContainText("清晨的雾气");
  await drawer.getByRole("button", { name: "订阅并下载" }).click();
  await expect(page.locator(".book-subscription h3")).toHaveText("山海之间");
  await page.getByRole("button", { name: "本地书库", exact: true }).click();
  await expect(page.locator(".book-card h3")).toHaveText("山海之间", {
    timeout: 30000,
  });
  await page.getByRole("button", { name: "预览与资料" }).click();
  await drawer.locator(".book-audio-panel > summary").click();
  await drawer.getByLabel("生成服务").selectOption("local");
  await drawer.getByLabel("生成范围").selectOption("all");
  await drawer.getByLabel("追更后自动生成新增章节").check();
  await drawer.getByRole("button", { name: "计算待生成章节" }).click();
  await expect(drawer).toContainText("新增 2 章");
  await drawer.getByRole("button", { name: "开始生成并保存到 NAS" }).click();
  await expect(drawer.locator(".book-audio-panel > summary")).toContainText(
    "2 章已就绪",
    { timeout: 30000 },
  );
  const audio = drawer.locator("audio");
  await expect(audio).toBeVisible();
  await audio.evaluate((element: HTMLAudioElement) => {
    element.muted = true;
    return element.play();
  });
  await expect
    .poll(() =>
      audio.evaluate((element: HTMLAudioElement) => element.currentTime),
    )
    .toBeGreaterThan(0);
  await audio.evaluate((element: HTMLAudioElement) => element.pause());
  await drawer.getByLabel("开启陪伴阅读", { exact: true }).check();
  await expect(drawer.getByText(/正在共享阅读位置/)).toBeVisible();
  await expect(
    drawer.locator(".book-reading-paragraph.is-reading"),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("book-audio-reading-desktop.png"),
    fullPage: true,
  });
  await audio.evaluate((element: HTMLAudioElement) => {
    element.currentTime = element.duration - 0.1;
    return element.play();
  });
  await expect(drawer.locator(".book-player h3")).toHaveText(
    "第 2 章 山间来信",
  );
  await expect(
    drawer.getByLabel("开启陪伴阅读", { exact: true }),
  ).toBeChecked();
  await expect(drawer.getByText(/正在共享阅读位置/)).toBeVisible();
  await drawer
    .locator("audio")
    .evaluate((element: HTMLAudioElement) => element.pause());
  await drawer.getByLabel("开启陪伴阅读", { exact: true }).uncheck();
  await drawer.locator(".book-audio-panel > summary").click();
  await drawer.getByRole("button", { name: "计算待生成章节" }).click();
  await expect(drawer).toContainText("新增 0 章");
  const hits = await (
    await request.get(fixture.audio_url + "/__fixture/status")
  ).json();
  await drawer.getByRole("button", { name: "开始生成并保存到 NAS" }).click();
  expect(
    await (await request.get(fixture.audio_url + "/__fixture/status")).json(),
  ).toEqual(hits);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("book-audio-reading-mobile.png"),
    fullPage: true,
  });
  expect(
    await drawer.evaluate(
      (element) => element.scrollWidth <= element.clientWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "预览与资料" }).click();
  await expect(
    drawer.getByLabel("开启陪伴阅读", { exact: true }),
  ).not.toBeChecked();
  expect(errors).toEqual([]);
});
