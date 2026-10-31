/**
 * TS-018 任务中心用例共用的装置：真实同源后台（4814）、合成 HA（4817）、开发服务器（5173）。
 *
 * `task-center.spec.ts` 走构建产物，`task-center-dev.spec.ts` 走 Vite 开发模块（StrictMode），
 * 两者的登录与真实操作步骤是同一套，这里只留一份实现。
 */
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

export const ADMIN = "synthetic-admin";
export const ADMIN_PASSWORD = "synthetic-local-password-014";
export const HOME = "http://127.0.0.1:4817";
/** `npm run dev` 的地址：开发模块输出，`main.tsx` 在那里用 React StrictMode。 */
export const DEV = "http://127.0.0.1:5173";

export type HomeLog = {
  mode: string;
  service_mode: string | null;
  requests: { method: string; path: string }[];
  states: Record<string, { state: string }>;
};

/** The synthetic HA is one long-lived process: every case starts from a known state. */
export async function resetHome(request: APIRequestContext) {
  await request.post(`${HOME}/fixture/mode`, {
    data: { mode: "normal", service_mode: null },
  });
  for (const [entity_id, state] of [
    ["light.study", "off"],
    ["switch.kettle", "off"],
    ["sensor.living_temperature", "23.5"],
  ]) {
    await request.post(`${HOME}/fixture/state`, { data: { entity_id, state } });
  }
  await request.post(`${HOME}/fixture/log/clear`, { data: {} });
}

export async function homeLog(request: APIRequestContext): Promise<HomeLog> {
  return (await (await request.get(`${HOME}/fixture/log`)).json()) as HomeLog;
}

export async function homeServices(request: APIRequestContext) {
  const log = await homeLog(request);
  return log.requests.filter(
    (row) => row.method === "POST" && row.path.startsWith("/api/services/"),
  );
}

/** 只做真实登录：任务中心不需要解锁任何写权限，因此登录本身就是全部授权。 */
export async function login(page: Page) {
  await page.goto("/#/system/tasks");
  await page.getByLabel("管理员账号").fill(ADMIN);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "重新读取" })).toBeVisible();
}

export function panel(page: Page) {
  return page.locator('section.panel.tasks[aria-label="任务中心"]');
}

export function sourceCard(page: Page, label: string) {
  return page.locator("article.tasks-source").filter({ hasText: label });
}

export function item(page: Page, title: string) {
  return page.locator("article.tasks-item").filter({ hasText: title });
}

/** 两个下拉在同一个筛选区里：0 是状态，1 是来源。 */
export function filter(page: Page, name: "状态" | "来源") {
  return page.locator(".tasks-filters select").nth(name === "状态" ? 0 : 1);
}

/** 真的发布一个版本：任务中心只能投影已经落库的权威版本。 */
export async function publishVersion(page: Page) {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员密码").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await expect(page.locator(".models-notice")).toContainText("模型管理已解锁");
  // ProviderModelsPanel is the current management surface. Task projection still
  // consumes the durable model-publication ledger, whose reviewed-template API
  // is exercised here with the browser's actual authenticated cookie and CSRF.
  const session = await (await page.request.get("/api/v1/session")).json();
  const headers = {
    Origin: new URL(page.url()).origin,
    "X-CSRF-Token": session.csrf,
  };
  const preview = await page.request.post("/api/v1/models/preview", {
    headers,
    data: { template_id: "chat-local-text" },
  });
  expect(preview.status()).toBe(200);
  const prepared = await preview.json();
  const published = await page.request.post("/api/v1/models/publish", {
    headers,
    data: {
      template_id: "chat-local-text",
      expected_revision: prepared.expected_revision,
      client_request_id: randomUUID(),
    },
  });
  expect(published.status()).toBe(200);
  expect((await published.json()).state).toBe("published");
}

/** 真的执行一次设备控制：回执只是受理，任务中心要如实分开显示。 */
export async function control(page: Page, action: string) {
  await page.goto("/#/home/devices");
  const unlock = page.getByRole("button", { name: "解锁设备控制" });
  const target = page.getByRole("button", { name: action });
  await expect(unlock.or(target).first()).toBeVisible();
  // 解锁是会话级的：另一个标签页可能已经解锁过这个会话。
  if (await unlock.isVisible()) {
    await page.getByLabel("管理员密码").fill(ADMIN_PASSWORD);
    await unlock.click();
  }
  await expect(target).toBeVisible();
  await target.click();
  await expect(page.locator(".home-notice")).toContainText("已受理");
}

export const controlLight = (page: Page) => control(page, "打开书房灯");
