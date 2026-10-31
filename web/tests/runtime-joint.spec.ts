import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

test("real identity image reference persists and exposes readable workspace and room projections", async ({
  page,
}, info) => {
  test.skip(
    !process.env.TS_C4_JOINT_FIXTURE,
    "Start the isolated C4 joint backend fixture first.",
  );
  const fixture = JSON.parse(
    readFileSync(process.env.TS_C4_JOINT_FIXTURE!, "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#/roles/life");
  await page.getByLabel("管理员账号").fill(fixture.username);
  await page.getByLabel("密码", { exact: true }).fill(fixture.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("选择角色")).toHaveValue(fixture.actor_id);
  await expect(page.locator(".life-runtime-grid")).toContainText("心情：平静");
  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  const identity = page
    .getByRole("heading", { name: "角色形象参考", exact: true })
    .locator("..");
  await identity.getByLabel("上传参考图片", { exact: true }).setInputFiles({
    name: `身份参考-${info.project.name}.png`,
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await identity.getByRole("button", { name: "取得原件", exact: true }).click();
  await expect(
    identity.getByRole("button", { name: "设为角色形象参考", exact: true }),
  ).toBeEnabled();
  await identity
    .getByRole("button", { name: "设为角色形象参考", exact: true })
    .click();
  await expect(identity).toContainText("当前参考：已配置");
  await page.reload();
  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  await expect(identity).toContainText("当前参考：已配置");
  await identity.getByRole("button", { name: "查看图片", exact: true }).click();
  await expect(
    identity.getByRole("img", { name: "原件图片", exact: true }),
  ).toBeVisible();
  await identity.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("identity-workspace-viewport.png"),
  });
  await page.getByRole("tab", { name: "作品与原稿", exact: true }).click();
  // Each freshly restarted owner fixture has its own empty writing database.
  // Capture its real current workspace without recreating the previously tested work.
  await page
    .getByRole("heading", { name: "开始新作品", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("writing-workspace-viewport.png"),
  });
  await page.goto("/#/roles/room");
  await expect(page.locator("canvas")).toHaveAttribute("data-ready", "true");
  await expect(
    page.getByRole("combobox", { name: "小屋角色", exact: true }),
  ).toHaveValue(fixture.actor_id);
  await expect(page.locator(".room-live-details")).toContainText("当前穿搭：");
  await expect(page.locator(".room-live-details strong")).not.toHaveText("");
  await page.screenshot({ path: info.outputPath("room-current-viewport.png") });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("real split owners persist activity, original reading and wardrobe on desktop and mobile", async ({
  page,
}, info) => {
  test.setTimeout(90000);
  test.skip(
    !process.env.TS_C4_JOINT_FIXTURE,
    "Start the isolated C4 joint backend fixture first.",
  );
  const fixture = JSON.parse(
    readFileSync(process.env.TS_C4_JOINT_FIXTURE!, "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined });
    Object.defineProperty(Crypto.prototype, "subtle", { get: () => undefined });
  });
  await page.goto("/#/roles/life");
  await page.getByLabel("管理员账号").fill(fixture.username);
  await page.getByLabel("密码", { exact: true }).fill(fixture.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("选择角色")).toHaveValue(fixture.actor_id);
  await expect(
    page.getByRole("button", { name: "编辑进度", exact: true }).first(),
  ).toBeVisible();
  const prior = page.getByRole("button", { name: "暂停", exact: true });
  if (await prior.count()) {
    await prior.first().click();
    await expect(prior).toHaveCount(0);
  }
  const title = `浏览器活动 ${info.project.name} ${Date.now()}`;
  await page.getByLabel("活动名称", { exact: true }).fill(title);
  await page
    .getByLabel("当前进度", { exact: true })
    .fill("已读到第二段，回来继续。");
  await page
    .getByRole("combobox", { name: "状态", exact: true })
    .selectOption("running");
  await page.getByRole("button", { name: "保存活动", exact: true }).click();
  const activity = page.locator(".life-record").filter({ hasText: title });
  await expect(activity).toBeVisible();
  await activity.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(activity).toContainText("已暂停");
  await activity.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(activity).toContainText("进行中");
  await page
    .getByLabel("事项", { exact: true })
    .fill(`明天续读 ${info.project.name}`);
  await page
    .getByLabel("目标或约定", { exact: true })
    .fill("保留开放话题，继续讨论第二段。");
  await page.getByRole("button", { name: "保存关注", exact: true }).click();
  await expect(
    page.getByText(`明天续读 ${info.project.name}`, { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("activity.png"),
    fullPage: true,
  });

  await page.getByRole("tab", { name: "原文与共读", exact: true }).click();
  const actual = `独立 Knowledge 正文 ${info.project.name}。\n这是上传的实际第二段，不是目录摘要。`;
  await page.getByLabel("上传原件", { exact: true }).setInputFiles({
    name: `原文-${info.project.name}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(actual),
  });
  await page.getByRole("button", { name: "取得原件", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "建立阅读进度", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "建立阅读进度", exact: true }).click();
  const reading = page
    .getByRole("heading", { name: "继续实读原件", exact: true })
    .locator("..");
  await reading
    .getByRole("button", { name: "读取此范围", exact: true })
    .click();
  await expect(reading).toContainText(actual);
  await page.screenshot({
    path: info.outputPath("reading.png"),
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("tab", { name: "原文与共读", exact: true }).click();
  await expect(
    page.locator(".life-record").filter({ hasText: "已实读：" }).first(),
  ).toContainText("字符");

  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  const wardrobe = page
    .getByRole("heading", { name: "衣柜与当前穿搭", exact: true })
    .locator("..");
  // A real PNG fixture; descriptor, raw bytes, owner acquisition and ref all travel over HTTP.
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
    "base64",
  );
  await wardrobe.getByLabel("上传参考图片", { exact: true }).setInputFiles({
    name: `参考-${info.project.name}.png`,
    mimeType: "image/png",
    buffer: png,
  });
  await wardrobe.getByRole("button", { name: "取得原件", exact: true }).click();
  await expect(
    wardrobe.getByRole("button", { name: "移除此服装参考", exact: true }),
  ).toBeVisible();
  await wardrobe
    .getByLabel("服装描述", { exact: true })
    .fill(`实际参考服装 ${info.project.name}`);
  await wardrobe
    .getByLabel("外观细节", { exact: true })
    .fill("淡色家居服，使用已上传的原件参考。");
  await wardrobe.getByRole("button", { name: "保存服装", exact: true }).click();
  const outfit = wardrobe
    .locator(".life-record")
    .filter({ hasText: `实际参考服装 ${info.project.name}` });
  await expect(outfit).toBeVisible();
  await outfit
    .getByRole("button", { name: "设为当前穿搭", exact: true })
    .click();
  await expect(outfit).toContainText("当前穿搭");
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("wardrobe.png"),
    fullPage: true,
  });
});

test("real current chapter preserves draft versions, review and publication with the current manager", async ({
  page,
}, info) => {
  test.setTimeout(90000);
  test.skip(
    !process.env.TS_C4_JOINT_FIXTURE,
    "Start the isolated C4 joint backend fixture first.",
  );
  const fixture = JSON.parse(
    readFileSync(process.env.TS_C4_JOINT_FIXTURE!, "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#/roles/life");
  await page.getByLabel("管理员账号").fill(fixture.username);
  await page.getByLabel("密码", { exact: true }).fill(fixture.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("选择角色")).toHaveValue(fixture.actor_id);
  const daily = page.getByRole("region", { name: "角色日常", exact: true });
  await expect(daily.locator(".life-plan >li")).not.toHaveCount(0);
  const backendRead = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/life/runtime/read") &&
      response.request().postDataJSON().resource === "image_backend",
  );
  await page.getByRole("tab", { name: "衣柜与相册", exact: true }).click();
  expect((await backendRead).status()).toBe(200);
  await page.screenshot({
    path: info.outputPath("wardrobe-manager.png"),
    fullPage: true,
  });
  await page.getByRole("tab", { name: "作品与原稿", exact: true }).click();
  const workTitle = `实际作品 ${info.project.name} ${Date.now()}`;
  await page.getByLabel("作品名称", { exact: true }).fill(workTitle);
  await page
    .getByLabel("大纲", { exact: true })
    .fill("阅读后在窗边留下短札，保持同一章节继续修订。");
  await page
    .getByRole("button", { name: "保存作品与大纲", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: `${workTitle} · 章节`, exact: true }),
  ).toBeVisible();
  await page.getByLabel("章节名称", { exact: true }).fill("窗边短札");
  await page
    .getByLabel("本章目标", { exact: true })
    .fill("保留今天阅读的实际心得。");
  await page.getByRole("button", { name: "保存章节目标", exact: true }).click();
  const draftText = `浏览器真实保存的原稿 ${info.project.name}，未调用模型。`;
  await page
    .getByRole("textbox", { name: "修订正文", exact: true })
    .fill(draftText);
  await page.getByLabel("修订说明", { exact: true }).fill("记录第一版原稿");
  await page
    .getByRole("button", { name: "保存新原稿版本", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "修订正文", exact: true }),
  ).toHaveValue(draftText);
  await expect(page.locator(".life-chapter details")).toContainText(draftText);
  await page.reload();
  await page.getByRole("tab", { name: "作品与原稿", exact: true }).click();
  await page
    .locator(".life-record")
    .filter({ hasText: workTitle })
    .getByRole("button", { name: "继续这部作品", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "修订正文", exact: true }),
  ).toHaveValue(draftText);
  await page
    .getByLabel("审阅意见", { exact: true })
    .fill("已实读原稿，认可本章。");
  await page.getByRole("button", { name: "通过审阅", exact: true }).click();
  await expect(page.locator(".life-chapter")).toContainText("审阅意见已记录");
  await page
    .getByRole("button", { name: "发布当前审阅版本", exact: true })
    .click();
  await expect(page.locator(".life-chapter")).toContainText("已发布");
  await page.screenshot({
    path: info.outputPath("writing.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
