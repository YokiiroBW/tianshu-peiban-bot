import { test, expect } from "@playwright/test";

// Browser projection tests use a declared API stand-in; backend transport tests exercise real HTTP.
test("access settings distinguish active and pending and require certificates", async ({
  page,
}) => {
  const active = {
    mode: "http",
    origin: "http://192.168.31.210:19443",
    certificate: null,
  };
  const view = {
    available: true,
    active,
    saved: active,
    revision: 0,
    restart_required: false,
    certificates: [],
    listener_port: 8080,
  };
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: { authenticated: true, csrf: "synthetic" } }),
  );
  await page.route("**/api/v1/access/view", (route) =>
    route.fulfill({ json: view }),
  );
  await page.route("**/api/v1/access/save", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.password).toBe("synthetic-password-only");
    expect(body.revision).toBe(0);
    await route.fulfill({
      json: { ...view, saved: body.value, revision: 1, restart_required: true },
    });
  });
  await page.goto("/#/settings/access");
  await expect(
    page.getByRole("heading", { name: "访问地址与 HTTPS" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "修改访问地址与 HTTPS", exact: true })
    .click();
  await page.getByLabel("连接方式", { exact: true }).selectOption("https");
  await expect(
    page.getByRole("button", { name: "保存，重启后生效" }),
  ).toBeDisabled();
  await expect(page.getByText("尚未安装证书", { exact: false })).toBeVisible();
  await page.getByLabel("连接方式", { exact: true }).selectOption("proxy");
  await page
    .getByLabel("完整访问地址（IP 或域名）")
    .fill("https://bot.example.test");
  await page
    .getByLabel("管理员密码", { exact: true })
    .fill("synthetic-password-only");
  await page.getByRole("button", { name: "保存，重启后生效" }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("已保存。重启平台后生效；当前入口继续可用。", {
        exact: true,
      }),
  ).toBeVisible();
  await expect(page.getByText("当前生效：", { exact: false })).toContainText(
    active.origin,
  );
  await expect(page.getByText("待重启：", { exact: false })).toContainText(
    "https://bot.example.test",
  );
  await expect(page.getByLabel("管理员密码", { exact: true })).toHaveValue("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
