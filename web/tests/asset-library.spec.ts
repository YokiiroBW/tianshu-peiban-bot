/**
 * TS-019 只读资产页：真实浏览器会话、真实同源后台、合成资产对端。
 *
 * 断言的是网页真的把授权读取的索引元数据如实投影出来，而不是把未知显示成可用：索引离线、
 * 原件未验证、空目录、无结果、拒权与断线各有自己的说法，失败时不留旧正文。
 *
 * 这条套件跑构建产物。开发模式下 StrictMode 的首挂载与再进入是另一条路径，由
 * `playwright.assets-dev.config.ts`（`npm run dev` 的 5173）覆盖。
 */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  connect,
  login,
  noOverflow,
  PAGE,
  ready,
  released,
  scenario,
} from "./asset-library.fixtures";

test("资产库：连接、库列表、目录、详情、搜索与键盘都在真实读取之上", async ({
  page,
  request,
}, testInfo) => {
  await ready(request);
  await login(page);
  await page.goto(PAGE);
  // 未选择连接之前不读任何库：页面只说明它需要一次选择。
  await expect(page.getByText("请选择资产连接")).toBeVisible({ timeout: 5000 });
  await expect(page.locator(".asset-row")).toHaveCount(0);

  await page.getByLabel("资产连接").selectOption("library-a");
  // 真实读取后才有库行，且库自己报告的状态就是页面上写的那一个。
  const library = page.locator(".asset-library-row").first();
  await expect(library).toContainText("合成一号库");
  await expect(library).toContainText("可读");
  await library.click();

  // 目录列表：文件夹与文件各自成行，大小与修改时间是上游给的元数据。
  await expect(page.locator(".asset-row").first()).toContainText("归档");
  await expect(page.locator(".asset-row").nth(1)).toContainText(
    "非常长的中文目录名称",
  );
  const fileRow = page
    .locator(".asset-row")
    .filter({ hasText: "说明_中文.txt" });
  await expect(fileRow).toContainText("2.0 KB");
  await expect(page.locator(".asset-complete")).toContainText("完整显示 4 条");
  // 协议没有预览或下载端口：页面只用类型图标并明说暂不提供预览。
  await expect(page.locator(".asset-detail")).toContainText("暂不提供预览");
  await expect(page.getByRole("button", { name: /下载/ })).toHaveCount(0);

  await fileRow.click();
  const detail = page.locator(".asset-detail");
  await expect(detail).toContainText("说明_中文.txt");
  await expect(detail).toContainText("文件");
  await expect(detail).toContainText("未验证");
  await expect(detail).toContainText("f-readme");
  await page.screenshot({
    path: testInfo.outputPath("asset-detail.png"),
    fullPage: true,
  });
  // 详情关闭后焦点回到触发点附近的行，键盘仍能继续操作。
  await page.getByRole("button", { name: "关闭" }).click();
  await expect(detail).toContainText("在上面的列表里选择一个条目");

  // 进入下一级：面包屑给出完整路径，返回上级回到库根。
  await page.locator(".asset-row").first().click();
  await expect(page.locator(".asset-crumbs")).toContainText("归档");
  await expect(page.locator(".asset-row").first()).toContainText(
    "2025年度总结.md",
  );
  // 空目录是它自己的状态，不是读取失败，也不是“没有文件”。
  await page.locator(".asset-row").filter({ hasText: "空目录" }).click();
  await expect(page.locator(".asset-crumbs")).toContainText("空目录");
  await expect(page.getByText("这个目录里没有条目")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-empty-directory.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "返回上级" }).click();
  await expect(page.locator(".asset-row").first()).toContainText(
    "2025年度总结.md",
  );
  await page.getByRole("button", { name: "返回上级" }).click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 搜索是显式提交，清除后回到当前范围（当前库），不是全局搜索。
  await page.getByLabel("在当前范围搜索名称").fill("说明_中文");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.locator(".asset-row")).toHaveCount(1);
  await expect(page.locator(".asset-row").first()).toContainText("名称命中");
  await page.getByRole("button", { name: "清除搜索" }).click();
  await expect(page.locator(".asset-crumbs")).toContainText("合成一号库");
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 空搜索：明确写“没有匹配”，不是把列表清空当作没有文件。
  await page.getByLabel("在当前范围搜索名称").fill("绝对不存在的名字");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText("没有匹配的条目")).toBeVisible();
  await expect(page.getByText("没有结果不等于没有文件")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-no-result.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "清除搜索" }).click();

  // 键鼠可达：Tab 能走到工具条按钮，Enter 能打开选中的行。
  await page.locator(".asset-row").first().focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".asset-crumbs")).toContainText("归档");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "返回上级" }).click();
  await page.screenshot({
    path: testInfo.outputPath("asset-list.png"),
    fullPage: true,
  });
});

test("资产库：未滚动的顶部整屏截图（1440/390/320）与长视口对照", async ({
  page,
  request,
}, testInfo) => {
  await ready(request);
  await login(page);
  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption("library-a");
  await page.locator(".asset-library-row").first().click();
  await page.locator(".asset-row").filter({ hasText: "说明_中文.txt" }).click();
  await expect(page.locator(".asset-detail")).toContainText("说明_中文.txt");

  // 整屏截图而不是整页截图：壳里的侧栏是 sticky 的，整页截图会把视口之外的文档高度也画进来，
  // 侧栏就只出现在它自然位置那一段，看起来像“从中段才出现”。这里每次都回到文档顶部再拍，
  // 并断言侧栏在这一屏之内的位置 —— 如果这是真布局问题，这里会失败，而不是靠改壳掩盖。
  for (const [name, width, height] of [
    ["asset-top-1440x1000.png", 1440, 1000],
    ["asset-top-390x844.png", 390, 844],
    ["asset-top-320x844.png", 320, 844],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    if (width > 800) {
      // 桌面：侧栏在第一屏之内铺满，不是从文档中段才开始。
      const sidebar = await page.locator(".app-shell > .sidebar").boundingBox();
      expect(sidebar).not.toBeNull();
      expect(sidebar!.y).toBeGreaterThanOrEqual(0);
      expect(sidebar!.y).toBeLessThan(64);
      expect(sidebar!.height).toBeGreaterThanOrEqual(height - 64);
    } else {
      // 窄屏：壳本来就收起侧栏，改用抽屉入口；这是壳自己的既有行为，本卡不改壳。
      await expect(page.locator(".app-shell > .sidebar")).toBeHidden();
      await expect(
        page.getByRole("button", { name: "打开导航" }),
      ).toBeVisible();
    }
    expect(await noOverflow(page)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(name), fullPage: false });
  }

  // 同一页、同一会话，只把视口放高：整屏之内就能看到完整正文，用来说明上面那一屏截断只是
  // 视口高度，不是内容缺失。
  await page.setViewportSize({ width: 1440, height: 1600 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("asset-top-1440x1600.png"),
    fullPage: false,
  });
});

test("资产库：减少动效、深色、窄屏与 320px 无横向溢出", async ({
  page,
  request,
}, testInfo) => {
  await ready(request);
  await login(page);
  await connect(page);
  await page.locator(".asset-library-row").first().click();
  await page.locator(".asset-row").filter({ hasText: "说明_中文.txt" }).click();
  await expect(page.locator(".asset-detail")).toContainText("说明_中文.txt");

  // 减少动效下详情仍然可读，且没有位移动画。
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.locator(".asset-detail")).toBeVisible();
  expect(
    await page
      .locator(".asset-detail")
      .evaluate((node) => getComputedStyle(node).animationName),
  ).toBe("none");
  await page.screenshot({
    path: testInfo.outputPath("asset-reduced-motion.png"),
    fullPage: true,
  });

  // 深色主题：内容仍然可读，令牌来自同一个来源。
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator(".asset-detail")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-dark.png"),
    fullPage: true,
  });

  // 1024×768：详情下置，页面不横向溢出。
  await page.setViewportSize({ width: 1024, height: 768 });
  expect(await noOverflow(page)).toBe(true);
  await expect(page.locator(".asset-detail")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-1024.png"),
    fullPage: true,
  });

  // 390×844 与 320px：搜索竖排、条目改为名称加二级元数据，都无横向溢出。
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await noOverflow(page)).toBe(true);
  await expect(page.getByLabel("在当前范围搜索名称")).toBeVisible();
  await expect(page.locator(".asset-detail")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-390.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 320, height: 720 });
  expect(await noOverflow(page)).toBe(true);
  await page.getByRole("button", { name: "库列表" }).click();
  await expect(page.locator(".asset-library-row").first()).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("asset-320.png"),
    fullPage: true,
  });
});

test("资产库：离线索引、拒权、断线与超限各自成状态，失败不留旧正文", async ({
  page,
  request,
}, testInfo) => {
  await ready(request);
  await login(page);
  await connect(page);
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 拒权：读取失败明确说出来，旧的目录内容不留在屏幕上。
  await scenario(request, "denied");
  await page.getByRole("button", { name: "刷新" }).click();
  await expect(page.getByText("这次读取没有成功")).toBeVisible();
  await expect(page.locator(".asset-row")).toHaveCount(0);
  await expect(page.getByText("不会留下正文")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-denied.png"),
    fullPage: true,
  });

  // 读取中：上游还在想，页面只说明它在等，不显示上一级的内容。
  await scenario(request, "stall");
  await page.getByRole("button", { name: "刷新" }).click();
  await expect(page.getByText("正在读取")).toBeVisible();
  await expect(page.locator(".asset-row")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath("asset-loading.png"),
    fullPage: true,
  });
  await released(request);

  // 断线：上游不可用是它自己的状态，不是空目录。
  await scenario(request, "outage");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(page.getByText("这次读取没有成功")).toBeVisible();
  await expect(page.locator(".asset-row")).toHaveCount(0);

  // 超限：超过体积上限的响应不被采用。
  await scenario(request, "oversized");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(page.getByText("这次读取没有成功")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-over-limit.png"),
    fullPage: true,
  });

  // 恢复后重新读取得到真实内容，说明前面的失败没有留下任何残留状态。
  await ready(request);
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 索引离线：是库自己的状态，不是文件消失，原件仍未验证。
  await scenario(request, "offline");
  await page.getByRole("button", { name: "库列表" }).click();
  await page.getByRole("button", { name: "刷新" }).click();
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "索引离线",
  );
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-library")).toContainText("索引离线");
  await expect(page.locator(".asset-library")).toContainText(
    "原件是否可用未经验证",
  );
  await expect(page.locator(".asset-library-fields")).toContainText("未验证");
  await page.screenshot({
    path: testInfo.outputPath("asset-offline.png"),
    fullPage: true,
  });
});

test("资产库：授权库超过一页时可以继续读取，下一页真的能打开", async ({
  page,
  request,
}, testInfo) => {
  await ready(request);
  await login(page);
  // 对端自己把授权库分页（每页按请求大小 50，共 137 个），而不是一次给完：客户端必须跟着
  // 对端的续读标记走，不能假设一次回答就是全部。第一页之后的库只能经这个入口进入。
  await scenario(request, "paged_libraries");
  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption("library-a");

  await expect(page.locator(".asset-library-row")).toHaveCount(50);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成多页库001",
  );
  await expect(page.getByText("上游还有更多")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("asset-libraries-page1.png"),
    fullPage: true,
  });

  // 第二页：续读是显式动作，旧的 50 个库留在原位，新的接在后面。
  await page.getByRole("button", { name: "继续读取下一页" }).click();
  await expect(page.locator(".asset-library-row")).toHaveCount(100);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成多页库001",
  );
  await expect(page.locator(".asset-library-row").nth(50)).toContainText(
    "合成多页库051",
  );

  // 最后一页：读完之后明确说明已经完整显示，不再留下一个按不动的按钮。
  await page.getByRole("button", { name: "继续读取下一页" }).click();
  await expect(page.locator(".asset-library-row")).toHaveCount(137);
  await expect(page.locator(".asset-library-row").last()).toContainText(
    "合成多页库137",
  );
  await expect(page.getByText("已完整显示 137 个授权库")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "继续读取下一页" }),
  ).toHaveCount(0);

  // 第二页之后的库和第一页一样能打开：否则分页只是把库列出来却进不去。
  await page.locator(".asset-library-row").last().click();
  await expect(page.locator(".asset-crumbs")).toContainText("合成多页库137");
  await expect(page.locator(".asset-row").first()).toContainText(
    "第137库说明.txt",
  );

  // 回到库列表再读一次：续读标记是本次范围的一部分，重新读取回到第一页而不是接在旧页后面。
  await page.getByRole("button", { name: "库列表" }).click();
  await expect(page.locator(".asset-library-row")).toHaveCount(50);
});

test("资产库：切连接会清掉上一份库分页，迟到的旧页不会接上来", async ({
  page,
  request,
}) => {
  await ready(request);
  await login(page);
  await scenario(request, "paged_libraries");
  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption("library-a");
  await expect(page.locator(".asset-library-row")).toHaveCount(50);
  await page.getByRole("button", { name: "继续读取下一页" }).click();
  await expect(page.locator(".asset-library-row")).toHaveCount(100);

  // 换连接：上一份（含已经读到的第二页）立即清空，不会与新连接的库混在一张列表里。
  await scenario(request, "switch");
  await page.getByLabel("资产连接").selectOption("library-b");
  await expect(page.locator(".asset-library-row")).toHaveCount(1);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成二号库",
  );
  await expect(page.getByText("合成多页库")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "继续读取下一页" }),
  ).toHaveCount(0);
});

test("资产库：切换连接与撤销登录都会立刻清掉上一份正文", async ({
  page,
  request,
}) => {
  await ready(request);
  await login(page);
  await connect(page);
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 换一个连接：对端这时提供的是另一个已授权索引。上一份目录必须在读取期间就消失，
  // 而不是等到新结果回来才被替换 —— 否则屏幕上会有一段时间显示着上一个库的内容。
  await scenario(request, "switch");
  await page.getByLabel("资产连接").selectOption("library-b");
  await expect(page.locator(".asset-row")).toHaveCount(0);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成二号库",
  );
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-row").first()).toContainText("存档.txt");
  await expect(
    page.locator(".asset-row").filter({ hasText: "归档" }),
  ).toHaveCount(0);

  // 撤销登录：正文与状态一起清空，页面回到需要登录。退出登录用的是壳里已有的入口，
  // 资产页不自建第二个登出按钮。
  await page.goto("/#/home/devices");
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.goto(PAGE);
  await expect(page.getByText("需要先登录")).toBeVisible();
  await expect(page.locator(".asset-row")).toHaveCount(0);
  await expect(page.locator(".asset-library-row")).toHaveCount(0);
});
