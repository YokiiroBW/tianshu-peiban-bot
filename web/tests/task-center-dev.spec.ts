/**
 * TS-018 任务中心在开发模式（`npm run dev`）下的回归。
 *
 * 开发模式走的是真实 `main.tsx`，那里用的就是 React `StrictMode`：开发模式下 React 会把每个
 * effect 故意跑成 setup → cleanup → setup，生产构建不跑这一遍，所以只有这条用例会经过它。
 * 这里不移除 StrictMode、不改产品代码、也不绕过开发模式：页面就是 Vite 开发服务器上的真实模块，
 * 只有“会话”和“记录”两个回答换成合成体，而记录内容仍来自真实后台刚才的一次真实控制。
 *
 * 配置见 `playwright.tasks-dev.config.ts`（合成后台 4814/4817 + 开发服务器 5173）。
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { TasksView } from "../src/features/settings/api";
import {
  DEV,
  controlLight,
  item,
  login,
  panel,
  resetHome,
} from "./task-center.fixtures";

test("任务中心：开发模式 StrictMode 首次挂载与切走再回来都能读出来", async ({
  page,
  request,
}) => {
  await resetHome(request);
  await login(page);
  await controlLight(page);
  // 真实后台对这一页的真实回答：开发服务器上的页面拿到的就是这份内容。
  const real = (await page.evaluate(async () => {
    const state = await (await fetch("/api/v1/session")).json();
    const response = await fetch("/api/v1/tasks/view", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.csrf,
      },
      body: JSON.stringify({ status: null, source: null, page_size: 20 }),
    });
    return { csrf: state.csrf, view: await response.json() };
  })) as { csrf: string; view: TasksView };
  expect(real.view.items.some((row) => row.title === "打开书房灯")).toBe(true);

  // 会话替身：开发服务器只提供页面模块，没有同源后台；先让它回答“未登录”。
  let sessions = 0;
  await page.route("**/api/v1/session", (route) => {
    sessions += 1;
    return route.fulfill({
      json: { authenticated: false, csrf: "synthetic-dev-csrf" },
    });
  });
  await page.goto(`${DEV}/#/system/tasks`);
  // StrictMode 之下也必须读出来：修好之前这里会永远停在“正在连接任务中心”。
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(panel(page)).toHaveAttribute("aria-busy", "false");
  await expect(page.getByText("正在连接任务中心")).toHaveCount(0);
  // 开发模式的复演确实发生了（同一棵树上读了两次会话），也就是这条用例真的走在 StrictMode 上。
  expect(sessions).toBeGreaterThan(1);

  // 再看有记录时的首次挂载：会话与记录都换成真实后台刚才给出的回答。
  await page.unroute("**/api/v1/session");
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({ json: { authenticated: true, csrf: real.csrf } }),
  );
  await page.route("**/api/v1/tasks/view", (route) =>
    route.fulfill({ json: real.view }),
  );
  await page.reload();
  await expect(item(page, "打开书房灯").first()).toBeVisible();
  await expect(panel(page)).toHaveAttribute("aria-busy", "false");

  // 切走再回来：任务中心被卸载后重新挂载，记录必须重新读出来，而不是留下一个空壳。
  await page.goto(`${DEV}/#/settings/connections`);
  await expect(page.getByRole("heading", { name: "接入准备" })).toBeVisible();
  await page.goto(`${DEV}/#/system/tasks`);
  await expect(item(page, "打开书房灯").first()).toBeVisible();
  await expect(panel(page)).toHaveAttribute("aria-busy", "false");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
