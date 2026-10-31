import { expect, test } from "@playwright/test";

test("HTTPS QQ profiles and administrator grant then revoke", async ({
  page,
}, testInfo) => {
  await page.goto("/#/people");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const directory = page.getByRole("complementary", { name: "用户目录" });
  await expect(directory).toBeVisible();
  await expect(directory.getByText("QQ 1001", { exact: true })).toBeVisible();
  await expect(directory.getByText("QQ 1002", { exact: true })).toBeVisible();
  // The real Memory fixture has identities but no portrait or observation setup.
  // Both same-named people remain visible, and a group alias finds the right one.
  await page
    .getByRole("textbox", { name: "搜索已加载用户" })
    .fill("群里的小雨");
  await expect(directory.getByText("QQ 1002", { exact: true })).toBeVisible();
  await expect(directory.getByText("QQ 1001", { exact: true })).toHaveCount(0);
  await page.getByRole("textbox", { name: "搜索已加载用户" }).clear();
  await expect(directory.getByText(/person:/)).toHaveCount(0);
  await directory.getByRole("button").filter({ hasText: "QQ 1002" }).click();
  await expect(page.getByText("群里的小雨", { exact: false })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("profiles.png"),
    fullPage: true,
  });
  await directory.getByRole("button").filter({ hasText: "QQ 1001" }).click();
  const toggle = page.getByRole("switch", { name: "设为管理员" });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await page.reload();
  await directory.getByRole("button").filter({ hasText: "QQ 1001" }).click();
  await expect(toggle).toBeChecked();
  await page.screenshot({
    path: testInfo.outputPath("granted.png"),
    fullPage: true,
  });
  await toggle.click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  await page.reload();
  await directory.getByRole("button").filter({ hasText: "QQ 1001" }).click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
});
