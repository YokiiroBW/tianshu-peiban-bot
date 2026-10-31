/**
 * TS-018 任务中心：真实浏览器会话，真实同源后台，合成 HA。
 *
 * 后台是 `tests/backend/run_web_fixture.py`（网页 4814，合成 HA 4817），它登记的正是本平台
 * 已持久化的两个操作来源：模型配置发布（`platform.models`）与家庭设备控制（`platform.home`）。
 * 这里断言的是网页真的把这些记录投影出来，而不是把未发生的操作显示成成功。
 *
 * 这条套件跑构建产物（生产构建不启用 StrictMode）。开发模式下 StrictMode 的 effect 复演是另一条
 * 路径，由 `task-center-dev.spec.ts` 用 `playwright.tasks-dev.config.ts` 覆盖。
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  control,
  controlLight,
  filter,
  homeServices,
  item,
  login,
  panel,
  publishVersion,
  resetHome,
  sourceCard,
} from "./task-center.fixtures";

test("任务中心：真实空状态、未接入来源、键盘、移动端与减少动效", async ({
  page,
}, testInfo) => {
  await login(page);
  // 全新后台确实没有任何操作：这里必须是真空状态，而不是占位记录。
  await expect(page.getByText("还没有任何平台操作记录")).toBeVisible();
  await expect(page.locator("article.tasks-item")).toHaveCount(0);
  // 两个已接入来源各自报告自己的台账状态，未接入的产品明确写出来由。
  await expect(sourceCard(page, "家庭设备控制")).toContainText("已记录");
  await expect(sourceCard(page, "模型配置发布")).toContainText("暂无记录");
  await expect(
    page.locator("article.tasks-source").filter({ hasText: "Core 对话与写作" }),
  ).toContainText("未接入");
  const sources = page.getByLabel("操作来源");
  await expect(sources).toContainText("还没有正式的任务合同");
  await expect(sources).toContainText("没有可投影的操作台账");
  // 空状态下的筛选是可用的，并且如实说明筛不掉任何已记录的操作。
  await filter(page, "来源").selectOption("companion.core");
  await expect(page.getByText("这个来源还没有接入")).toBeVisible();
  await expect(
    page.locator(".state-copy p").filter({ hasText: "任务合同" }),
  ).toHaveCount(1);
  // 已接入但没有记录的来源 + 一个没有记录的状态：这是筛选空了，不是平台没有操作。
  await filter(page, "来源").selectOption("platform.models");
  await filter(page, "状态").selectOption("observed");
  await expect(page.getByText("当前筛选没有记录")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(filter(page, "状态")).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.getByText("还没有任何平台操作记录")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-empty.png"),
    fullPage: true,
  });
  // 减少动效下所有内容仍然可达可读。
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(panel(page)).toBeVisible();
  await expect(page.getByLabel("操作来源")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // 移动端不横向溢出，筛选和来源块仍然可用。
  await page.setViewportSize({ width: 390, height: 844 });
  await filter(page, "状态").selectOption("");
  await expect(page.getByLabel("操作来源")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("task-center-mobile.png"),
    fullPage: true,
  });
});

test("任务中心：真实发布与真实控制被如实投影，详情可键盘开合", async ({
  page,
  request,
}, testInfo) => {
  await resetHome(request);
  await login(page);
  await publishVersion(page);
  await controlLight(page);
  await page.goto("/#/system/tasks");
  await expect(page.getByRole("button", { name: "重新读取" })).toBeVisible();
  await expect(sourceCard(page, "模型配置发布")).toContainText("已记录");
  // 两个来源各一条真实记录，状态、阶段与依据都不合并成同一个绿点。
  const published = item(page, "Chat 兼容配置");
  await expect(published).toHaveCount(1);
  await expect(published.locator(".rail-label")).toHaveText("已观测");
  await expect(published).toContainText("权威版本已核对");
  await expect(published).toContainText("有后续观测或权威记录作为依据。");
  await expect(published).toContainText("模型发布");
  const controlled = item(page, "打开书房灯");
  await expect(controlled).toHaveCount(1);
  await expect(controlled.locator(".rail-label")).toHaveText("已受理");
  await expect(controlled).toContainText(
    "只表示平台已被受理，执行结果还没有依据。",
  );
  await expect(controlled).toContainText("不可取消");
  // 设备控制真的发生过一次，而不是网页上的一个假状态。
  expect(await homeServices(request)).toHaveLength(1);
  // 键盘可达：Tab 到“查看详情”并按 Enter 展开。
  const toggle = page.locator("[data-task-toggle]").first();
  await toggle.focus();
  await expect(toggle).toBeFocused();
  await toggle.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const detail = page.locator(".tasks-detail");
  await expect(detail).toContainText("详情是这一刻的重新读取");
  // 执行方没有声明取消能力：这里只说明，不提供取消按钮，也不自动重发。
  await expect(detail).toContainText("取消：不可用");
  await expect(
    page.getByRole("button", { name: /取消(操作|任务)/ }),
  ).toHaveCount(0);
  await expect(detail).toContainText("时间线");
  await expect(detail).toContainText("结果依据");
  await expect(detail).toContainText("请求内容");
  await expect(detail).toContainText("原始责任模块");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-records.png"),
    fullPage: true,
  });
  // Esc 收起并把焦点还给触发它的按钮。
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  // 状态筛选是精确的：只筛“已受理”时模型发布记录不出现。
  await filter(page, "状态").selectOption("accepted");
  await expect(item(page, "打开书房灯")).toHaveCount(1);
  await expect(item(page, "Chat 兼容配置")).toHaveCount(0);
  // 来源筛选同理。
  await filter(page, "状态").selectOption("");
  await filter(page, "来源").selectOption("platform.models");
  await expect(item(page, "Chat 兼容配置")).toHaveCount(1);
  await expect(item(page, "打开书房灯")).toHaveCount(0);
  // 深色外观沿用既有令牌，不改动样式基线。
  await page.getByRole("button", { name: "外观设置" }).click();
  await page.getByLabel("深色", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: testInfo.outputPath("task-center-records-dark.png"),
    fullPage: true,
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("任务中心：退出登录后不再显示任何操作记录", async ({ page }, testInfo) => {
  await login(page);
  await expect(page.getByLabel("操作来源")).toBeVisible();
  await page.goto("/#/roles/chat");
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await page.goto("/#/system/tasks");
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.getByLabel("操作来源")).toHaveCount(0);
  await expect(page.locator("article.tasks-item")).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("管理员账号")).toBeEnabled();
  await expect(page.getByLabel("操作来源")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-logged-out.png"),
    fullPage: true,
  });
});

/**
 * 另一标签页退出登录：统一会话边界卸载任务面板，已经在途的旧轮询不能恢复过期视图。
 */
test("任务中心：另一标签页退出登录后，在途轮询不再留下过期视图", async ({
  page,
  context,
  request,
}, testInfo) => {
  await resetHome(request);
  await login(page);
  await controlLight(page);
  await page.goto("/#/system/tasks");
  const controls = item(page, "打开书房灯");
  await expect(controls.first()).toBeVisible();
  const before = await controls.count();
  expect(before).toBeGreaterThan(0);
  // 已打开的详情也属于这个会话：会话没了，它不能留下来。
  await page.locator("[data-task-toggle]").first().press("Enter");
  await expect(page.locator(".tasks-detail")).toContainText("时间线");

  // 扣住第一次轮询的回答：它会在会话失效之后才回来，视图不能被它带回旧样子。
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started: () => void = () => {};
  const holding = new Promise<void>((resolve) => {
    started = resolve;
  });
  let first = true;
  await page.route("**/api/v1/tasks/view", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    if (first) {
      first = false;
      started();
      await held;
      // 面板已经取消或清理了这次读取：发不出去才是预期，不是测试失败。
      await route.fulfill({ response, json: payload }).catch(() => {});
      return;
    }
    await route.fulfill({ response, json: payload });
  });

  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await holding;
  const second = await context.newPage();
  await second.goto("/#/roles/chat");
  await second.getByRole("button", { name: "退出登录" }).click();
  await expect(second.getByLabel("管理员账号")).toBeVisible();
  await page.bringToFront();

  // 记录、详情、来源与分页都不属于已经失效的会话。
  await expect(page.locator("article.tasks-item")).toHaveCount(0);
  await expect(page.locator(".tasks-detail")).toHaveCount(0);
  await expect(page.getByLabel("操作来源")).toHaveCount(0);
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(panel(page)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-session-revoked.png"),
    fullPage: true,
  });

  // 迟到的旧回答现在才回来：它属于已经失效的会话，不能把记录写回页面。
  release();
  await page.waitForTimeout(500);
  await expect(page.locator("article.tasks-item")).toHaveCount(0);
  await expect(page.getByLabel("管理员账号")).toBeVisible();

  // 会话失效后不再轮询：可见性再触发一次也不会发出新的视图请求。
  let polls = 0;
  page.on("request", (row) => {
    if (row.url().endsWith("/api/v1/tasks/view")) polls += 1;
  });
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await page.waitForTimeout(600);
  expect(polls).toBe(0);

  // 重新登录后真实记录仍在：清空的是会话的视图，不是记录本身。
  await login(page);
  await expect(item(page, "打开书房灯").first()).toBeVisible();
});

/**
 * 会话过期/权限撤销的合成状态替身：真实后台无法在不改设置的前提下让已登录会话失效，
 * 所以这里只替换这两个响应体；页面、请求路径、Cookie/CSRF 与清理逻辑仍然是真的。
 */
test("任务中心：会话过期后清空记录、详情与分页并给回登录入口", async ({
  page,
  request,
}, testInfo) => {
  await resetHome(request);
  await login(page);
  await controlLight(page);
  await page.goto("/#/system/tasks");
  await expect(item(page, "打开书房灯").first()).toBeVisible();
  await page.locator("[data-task-toggle]").first().press("Enter");
  await expect(page.locator(".tasks-detail")).toContainText("时间线");

  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: { authenticated: false, csrf: "synthetic-expired-session" },
    }),
  );
  await page.route("**/api/v1/tasks/view", (route) =>
    route.fulfill({ status: 401, json: { code: "session_expired" } }),
  );
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );

  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator("article.tasks-item")).toHaveCount(0);
  await expect(page.locator(".tasks-detail")).toHaveCount(0);
  await expect(page.getByLabel("操作来源")).toHaveCount(0);
  await expect(panel(page)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-session-expired.png"),
    fullPage: true,
  });

  await page.unroute("**/api/v1/session");
  await page.unroute("**/api/v1/tasks/view");
  // 替身只替换了后台的回答，真实会话没有销毁；回到任务页读取仍能恢复。
  await page.reload();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.goto("/#/system/tasks");
  await expect(page.getByRole("button", { name: "重新读取" })).toBeVisible();
  await expect(item(page, "打开书房灯").first()).toBeVisible();
});

/** 轮询带回的新记录属于最新位置，并且如实提示“有新的操作记录”。 */
test("任务中心：轮询带回的新记录出现在最新位置并如实提示", async ({
  page,
  context,
  request,
}) => {
  await resetHome(request);
  await login(page);
  // 先在这个页面上真实操作一次，列表里于是有一条已经显示出来的旧记录。
  await control(page, "打开书房灯");
  await page.goto("/#/system/tasks");
  await expect(page.locator("article.tasks-item").first()).toBeVisible();
  // 记下这一刻列表里已有的记录 id 与顺序：轮询带回的新记录必须排在它们前面。
  const before = await page
    .locator("[data-task-toggle]")
    .evaluateAll((rows) =>
      rows.map((row) => row.getAttribute("data-task-toggle")),
    );
  expect(before.length).toBeGreaterThan(0);

  // 另一个标签页真的操作了另一个设备：这个标签页留在旧快照上，等待下一次轮询。
  const second = await context.newPage();
  await control(second, "打开热水壶");
  await second.close();
  await page.bringToFront();
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );

  await expect(page.locator(".tasks-notice")).toContainText(
    /有 \d+ 条新的操作记录/,
  );
  const after = await page
    .locator("[data-task-toggle]")
    .evaluateAll((rows) =>
      rows.map((row) => row.getAttribute("data-task-toggle")),
    );
  // A poll opens a fresh first page and cursor. Older expanded pages may leave
  // the view, but records retained on this page preserve the server's order.
  expect(new Set(after).size).toBe(after.length);
  expect(before).not.toContain(after[0]);
  expect(after.filter((task) => before.includes(task))).toEqual(
    before.filter((task) => after.includes(task)),
  );
  await expect(page.locator("article.tasks-item").first()).toContainText(
    "已受理",
  );
});

/** 断线不是退出登录：旧快照留下，并且明说它是旧快照，不装作刷新成功。 */
test("任务中心：断线时保留旧快照并说明，恢复后自己变新", async ({
  page,
  request,
}, testInfo) => {
  await resetHome(request);
  await login(page);
  await controlLight(page);
  await page.goto("/#/system/tasks");
  await expect(item(page, "打开书房灯").first()).toBeVisible();
  await expect(page.locator(".tasks-stale")).toHaveCount(0);

  await page.route("**/api/v1/tasks/view", (route) => route.abort("failed"));
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator(".tasks-stale")).toContainText(
    "上次成功读取的快照",
  );
  await expect(item(page, "打开书房灯").first()).toBeVisible();
  await expect(page.getByLabel("管理员账号")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("task-center-offline.png"),
    fullPage: true,
  });

  await page.unroute("**/api/v1/tasks/view");
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator(".tasks-stale")).toHaveCount(0);
  await expect(item(page, "打开书房灯").first()).toBeVisible();
});

/** 换了筛选就是换了问题：上一个问题的回答迟到回来，不能写进现在的列表。 */
test("任务中心：迟到的旧筛选回答不会覆盖当前筛选", async ({
  page,
  request,
}) => {
  await resetHome(request);
  await login(page);
  await publishVersion(page);
  await controlLight(page);
  await page.goto("/#/system/tasks");
  await expect(item(page, "打开书房灯").first()).toBeVisible();

  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started: () => void = () => {};
  const holding = new Promise<void>((resolve) => {
    started = resolve;
  });
  let first = true;
  await page.route("**/api/v1/tasks/view", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    if (first) {
      first = false;
      started();
      await held;
      await route.fulfill({ response, json: payload }).catch(() => {});
      return;
    }
    await route.fulfill({ response, json: payload });
  });

  // 一次轮询被挂住：它问的是“全部来源”。
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await holding;
  // 期间操作者换了筛选：面板现在问的是“只看模型发布”，答案里不该有设备记录。
  await filter(page, "来源").selectOption("platform.models");
  await expect(item(page, "Chat 兼容配置").first()).toBeVisible();
  await expect(item(page, "打开书房灯")).toHaveCount(0);

  // 旧回答现在才回来：它属于上一个问题。
  release();
  await page.waitForTimeout(500);
  await expect(item(page, "打开书房灯")).toHaveCount(0);
  await expect(item(page, "Chat 兼容配置").first()).toBeVisible();
  await expect(filter(page, "来源")).toHaveValue("platform.models");
});
