/**
 * TS-019 只读资产页用例共用的装置：真实同源后台（4814）与合成资产对端（4819）。
 *
 * 合成对端说的是已发布的 AssetLink 只读信封，走真实 TLS；场景开关在 4818，用来让对端报告
 * 断线、拒权、索引离线或超大响应。它不是 AssetLibrary，真实资产服务的联合验证仍由 TS-064
 * 的独立驱动完成。
 */
import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const ADMIN = "synthetic-admin";
export const ADMIN_PASSWORD = "synthetic-local-password-014";
export const SYNTHETIC = "http://127.0.0.1:4818";
export const PAGE = "/#/resources/assets";

/** One long-lived synthetic peer: every case starts from the plain `ready` scenario. */
export async function scenario(request: APIRequestContext, name: string) {
  const response = await request.post(`${SYNTHETIC}/scenario`, {
    data: { scenario: name },
  });
  expect(response.ok()).toBe(true);
}

/** Let a request the `stall` scenario is holding open finish, and go back to `ready`. */
export async function released(request: APIRequestContext) {
  const response = await request.post(`${SYNTHETIC}/scenario`, {
    data: { scenario: "ready", release: true },
  });
  expect(response.ok()).toBe(true);
}

export async function ready(request: APIRequestContext) {
  await scenario(request, "ready");
}

/** 只做真实登录：资产页不需要任何管理解锁，登录本身就是全部授权。 */
export async function login(page: Page) {
  await page.goto("/#/system/tasks");
  await page.getByLabel("管理员账号").fill(ADMIN);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "重新读取" })).toBeVisible();
}

export function panel(page: Page) {
  return page.locator("section.asset-connection");
}

/** 选定一个连接：页面只有在明确选择之后才读取库列表。 */
export async function connect(page: Page, connection = "library-a") {
  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption(connection);
  await expect(page.locator(".asset-library-row").first()).toBeVisible();
}

export async function noOverflow(page: Page) {
  return page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  );
}
