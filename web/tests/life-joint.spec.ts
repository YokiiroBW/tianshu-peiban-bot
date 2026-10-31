import { expect, test } from "@playwright/test";

test("real Platform and Companion persist and display today's plan and paged experiences", async ({
  page,
}, info) => {
  test.skip(
    !process.env.TS_LIFE_JOINT_URL,
    "Run through tests/backend/test_life_joint.py",
  );
  await page.goto("/#/roles/life");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const daily = page.getByRole("region", { name: "角色日常", exact: true });
  await expect(page.getByLabel("选择角色")).toHaveValue("actor:a");
  await expect(daily).toContainText("生活进行中");
  await expect(daily.locator(".life-plan li")).toHaveCount(2);
  await expect(daily.locator(".life-plan li[data-current]")).toHaveCount(1);
  await expect(daily).toContainText("模型尚未可用");
  await expect(daily.locator(".life-timeline li")).toHaveCount(20);
  await daily.getByRole("button", { name: "继续读取经历" }).click();
  await expect(daily.locator(".life-timeline li")).toHaveCount(23);
  await expect(daily.getByRole("button", { name: "继续读取经历" })).toHaveCount(
    0,
  );
  expect(await daily.locator(".life-timeline li p").allTextContents()).toEqual(
    expect.arrayContaining([
      "Synthetic persisted experience 00",
      "Synthetic persisted experience 22",
    ]),
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("life-joint.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(daily.locator(".life-plan li")).toHaveCount(2);
  await expect(daily.locator(".life-timeline li")).toHaveCount(20);
  await expect(page.getByLabel("选择角色")).toHaveValue("actor:a");
});
