import { test, expect, type Page } from "@playwright/test";

async function choose(page: Page, label: string, value: string) {
  await page
    .getByRole("button", { name: `${label}模型设置`, exact: true })
    .click();
  const card = page.getByRole("dialog", {
    name: `${label}模型设置`,
    exact: true,
  });
  await card
    .getByLabel(`${label}模型`, { exact: true })
    .selectOption(value ? { label: value } : "");
  await card
    .getByRole("button", { name: `保存${label}模型`, exact: true })
    .click();
  await expect(card.getByRole("status")).toHaveText("已保存");
  await page.keyboard.press("Escape");
}

test("function assignments save, reload, inherit and keep chat independent", async ({
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
  const panel = page.getByRole("region", { name: "模型功能分工" });
  await expect(panel.getByRole("button", { name: /模型设置$/ })).toHaveCount(6);
  await expect(panel.getByRole("combobox")).toHaveCount(0);
  const item = (label: string) =>
    panel
      .getByRole("article")
      .filter({ has: page.getByRole("heading", { name: label, exact: true }) });
  await choose(page, "代码", "辅助模型 / fixture-specialist");
  await expect(item("代码")).toContainText(
    "当前：辅助模型 / fixture-specialist",
  );
  await expect(item("代码")).toContainText("自动调用尚未接入");
  await page.reload();
  await page.getByRole("button", { name: "代码模型设置", exact: true }).click();
  await expect(
    page.getByLabel("代码模型", { exact: true }).locator("option:checked"),
  ).toHaveText("辅助模型 / fixture-specialist");
  await page.keyboard.press("Escape");
  await expect(item("主对话")).toContainText("当前：聊天模型 / fixture-chat");
  await choose(page, "日常与日记", "辅助模型 / fixture-specialist");
  await expect(item("日常与日记")).toContainText(
    "当前：辅助模型 / fixture-specialist",
  );
  await choose(page, "主对话", "辅助模型 / fixture-specialist");
  await page
    .getByRole("navigation", { name: "供应商列表" })
    .getByRole("button", { name: /辅助模型/ })
    .click();
  await expect(
    page.getByRole("article", { name: "fixture-specialist模型", exact: true }),
  ).toContainText("默认对话模型");
  await choose(page, "主对话", "聊天模型 / fixture-chat");
  await expect(item("主对话")).toContainText("当前：聊天模型 / fixture-chat");
  for (const label of ["代码", "日常与日记"]) {
    await choose(page, label, "");
    await expect(item(label)).toContainText("当前：聊天模型 / fixture-chat");
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("model-functions.png"),
    fullPage: true,
  });
});
