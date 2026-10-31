import { expect, test } from "@playwright/test";

test("real local bot management: create, one-time token, enable, disable and rotate", async ({
  page,
  request,
}) => {
  await page.goto("/#/settings/channels");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();

  await page.getByText("旧连接管理（已配置的连接）").click();

  const section = page.getByRole("region", { name: "机器人连接管理" });
  await expect(
    section.getByRole("heading", { name: "机器人连接" }),
  ).toBeVisible();
  await expect(section.getByText("测试群 · NoneBot")).not.toBeVisible();
  await expect(section.getByRole("button", { name: "创建连接" })).toHaveCount(
    0,
  );

  await section.getByLabel("管理员密码").fill("synthetic-local-password-014");
  await section.getByRole("button", { name: "解锁连接管理" }).click();
  await expect(section.getByRole("button", { name: "创建连接" })).toBeVisible();
  await expect(section.getByText("已登记作者 2 人")).toBeVisible();
  await section.getByRole("button", { name: "创建连接" }).click();
  const token = await section.locator(".bot-secret code").textContent();
  expect(token?.length).toBeGreaterThan(32);
  const connectionId =
    (await section.locator(".bot-secret p").first().textContent())?.replace(
      "连接 ID：",
      "",
    ) ?? "";
  expect(connectionId).toMatch(/^bot:/);
  await expect(section.getByText("已停用")).toBeVisible();
  await section.getByRole("button", { name: "已保存，隐藏凭据" }).click();
  await expect(section.locator(".bot-secret code")).toHaveCount(0);

  await section.getByRole("button", { name: "启用" }).click();
  await expect(section.getByText("待配置或离线")).toBeVisible();
  const heartbeat = {
    connection_id: connectionId,
    instance_id: "synthetic-instance",
  };
  const headers = { Authorization: `Bearer ${token}` };
  expect(
    (
      await request.post("/internal/v1/bot/heartbeat", {
        data: heartbeat,
        headers,
      })
    ).status(),
  ).toBe(200);
  await section.getByRole("button", { name: "刷新状态" }).click();
  await expect(section.getByText("插件在线")).toBeVisible();

  await section.getByRole("button", { name: "停用" }).click();
  await expect(section.getByText("已停用")).toBeVisible();
  expect(
    (
      await request.post("/internal/v1/bot/heartbeat", {
        data: heartbeat,
        headers,
      })
    ).status(),
  ).toBe(403);
  await section.getByRole("button", { name: "轮换凭据" }).click();
  const rotated = await section.locator(".bot-secret code").textContent();
  expect(rotated).not.toBe(token);
  expect(
    (
      await request.post("/internal/v1/bot/heartbeat", {
        data: heartbeat,
        headers,
      })
    ).status(),
  ).toBe(401);
  await section.getByRole("button", { name: "启用" }).click();
  await expect(section.getByRole("button", { name: "停用" })).toBeVisible();
  expect(
    (
      await request.post("/internal/v1/bot/heartbeat", {
        data: heartbeat,
        headers: { Authorization: `Bearer ${rotated}` },
      })
    ).status(),
  ).toBe(200);
  await section.getByRole("button", { name: "已保存，隐藏凭据" }).click();
  await page.reload();
  await page.getByText("旧连接管理（已配置的连接）").click();
  await expect(section.locator(".bot-secret code")).toHaveCount(0);
  await expect(
    section.getByRole("heading", { name: "机器人连接" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
