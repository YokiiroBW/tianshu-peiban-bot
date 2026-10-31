import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("shared provider models survive reload, route by function and supply retrieval", async ({
  page,
}, info) => {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await page.getByRole("button", { name: "添加供应商", exact: true }).click();
  const name = `共享中转站-${info.project.name}`;
  await page.getByLabel("供应商名称").fill(name);
  await page
    .getByLabel("API 基础地址", { exact: true })
    .fill("https://example.invalid/v1");
  await page.getByLabel("API Key", { exact: true }).fill("synthetic-test-key");
  await page.getByRole("button", { name: "保存供应商", exact: true }).click();
  const detail = page.getByRole("region", { name: `${name}供应商详情` });
  await expect(detail).toContainText("尚未添加模型");
  await detail.getByRole("button", { name: "添加模型", exact: true }).click();
  const addModel = page.getByRole("dialog", { name: "添加模型", exact: true });
  await addModel.getByRole("button", { name: "获取模型列表" }).click();
  await addModel.getByLabel("fixture-chat", { exact: true }).check();
  await addModel.getByLabel("fixture-writing", { exact: true }).check();
  await addModel
    .getByRole("button", { name: "添加所选模型（2）", exact: true })
    .click();
  await expect(detail.locator(".provider-model-row")).toHaveCount(2);
  await page.keyboard.press("Escape");
  const chat = detail.getByRole("article", {
    name: "fixture-chat模型",
    exact: true,
  });
  const writing = detail.getByRole("article", {
    name: "fixture-writing模型",
    exact: true,
  });
  await expect(chat).toContainText("尚未测试");
  await chat.getByRole("button", { name: "测试连接" }).click();
  await expect(chat).toContainText("测试通过");
  await expect(writing).toContainText("尚未测试");
  await chat.getByRole("button", { name: "设为默认" }).click();
  await writing.getByRole("button", { name: "测试连接" }).click();
  await page
    .getByRole("button", { name: "日常与日记模型设置", exact: true })
    .click();
  await page
    .getByLabel("日常与日记模型", { exact: true })
    .selectOption({ label: `${name} / fixture-writing` });
  await page.getByRole("button", { name: "保存日常与日记模型" }).click();
  await expect(
    page.getByRole("region", { name: "模型功能分工" }),
  ).toContainText(`当前：${name} / fixture-writing`);
  await expect(chat).toContainText("默认对话模型");
  await page.keyboard.press("Escape");
  await page.reload();
  await page
    .getByRole("navigation", { name: "供应商列表" })
    .getByRole("button", { name: new RegExp(name) })
    .click();
  await expect(chat).toContainText("默认对话模型");
  await expect(writing).toContainText("测试通过");
  for (const [kind, model] of [
    ["embed", "fixture-embed"],
    ["rerank", "fixture-rerank"],
  ]) {
    await detail.getByRole("button", { name: "添加模型", exact: true }).click();
    await addModel.getByLabel("添加模型类型").selectOption(kind);
    await addModel.getByLabel("手动填写模型 ID").fill(model);
    await addModel
      .getByRole("button", { name: "添加模型", exact: true })
      .click();
    const row = detail.getByRole("article", {
      name: `${model}模型`,
      exact: true,
    });
    await expect(row).toBeVisible();
    await page.keyboard.press("Escape");
    await row.getByRole("button", { name: "测试连接" }).click();
    await expect(row).toContainText("测试通过");
    await expect(row.getByRole("button", { name: "设为默认" })).toHaveCount(0);
  }
  await expect(detail.locator(".provider-model-row")).toHaveCount(4);
  await expect(
    page
      .getByLabel("主对话模型", { exact: true })
      .locator("option")
      .filter({ hasText: "fixture-embed" }),
  ).toHaveCount(0);
  await detail.getByRole("button", { name: "编辑连接" }).click();
  await expect(page.getByLabel("API Key", { exact: true })).toHaveValue("");
  await page
    .getByRole("form", { name: "供应商连接设置" })
    .getByRole("button", { name: "取消", exact: true })
    .click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  const accessibility = await new AxeBuilder({ page })
    .include('[aria-label="供应商与模型"]')
    .withTags(["wcag2a", "wcag2aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({
    path: info.outputPath("provider-multiple-models.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "记忆检索设置" }).click();
  for (const [label, model] of [
    ["嵌入", "fixture-embed"],
    ["重排序", "fixture-rerank"],
  ]) {
    await page
      .getByRole("button", { name: `${label}模型设置`, exact: true })
      .click();
    await page
      .getByLabel(`${label}模型选择`)
      .selectOption({ label: `${name} / ${model}` });
    await page
      .getByRole("button", { name: `保存${label}模型`, exact: true })
      .click();
    const section = page.getByRole("region", { name: `${label}模型连接` });
    await expect(section).toContainText(`当前：${name} / ${model}`);
    await section.getByRole("button", { name: "测试已保存模型" }).click();
    await expect(page.getByRole("dialog").getByRole("status")).toContainText(
      "测试通过",
    );
    await page.keyboard.press("Escape");
    await expect(section.getByLabel(`${label} API Key`)).toHaveCount(0);
  }
  await page.getByRole("button", { name: "检索模式设置", exact: true }).click();
  await page.getByLabel("检索模式", { exact: true }).selectOption("hybrid");
  await page.getByLabel("启用重排序").check();
  await page.getByRole("button", { name: "保存检索模式", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "检索模式设置", exact: true }).click();
  await expect(page.getByLabel("检索模式", { exact: true })).toHaveValue(
    "hybrid",
  );
  await expect(page.getByLabel("启用重排序")).toBeChecked();
  await page.screenshot({
    path: info.outputPath("retrieval-shared-models.png"),
    fullPage: true,
  });
  await page.goto("/#/settings/models");
  await page
    .getByRole("navigation", { name: "供应商列表" })
    .getByRole("button", { name: new RegExp(name) })
    .click();
  await writing.getByRole("button", { name: "停用", exact: true }).click();
  await expect(chat).toContainText("默认对话模型");
  await expect(writing).toContainText("已停用");
  await chat.getByRole("button", { name: "模型设置" }).click();
  await page.getByLabel("显示名称").fill("日常聊天");
  await page.getByRole("button", { name: "保存模型设置" }).click();
  await expect(chat).toContainText("默认对话模型");
  await writing.getByRole("button", { name: "删除模型", exact: true }).click();
  await writing.getByRole("button", { name: "确认删除模型" }).click();
  await expect(writing).toHaveCount(0);
  await expect(chat).toContainText("测试通过");
});
