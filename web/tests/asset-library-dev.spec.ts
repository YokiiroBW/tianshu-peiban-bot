/**
 * TS-019 只读资产页的 StrictMode 路径（开发模式）。
 *
 * 构建产物里没有 StrictMode，首挂载的 effect 只跑一次；开发模式下它复演一次。这条套件专门验证
 * 那种复演不会让页面把自己的会话范围清掉，也不会让两代读取互相回填：这正是只在浏览器里才出现
 * 的失败模式 —— 页面看起来永远停在“请选择资产连接”，而对端其实一直有授权。
 *
 * 断言的是可观察事实：选择连接后库行留下；离开页面再回来，范围仍在；重复读取期间上一份正文
 * 不会在屏幕上停留。请求本身也计数，确认没有多余的、会清掉范围的“选择连接”请求。
 */
import { expect, test } from "@playwright/test";
import {
  connect,
  login,
  PAGE,
  ready,
  scenario,
} from "./asset-library.fixtures";

const API = "/api/v1/assets/";

/** 页面自己发出的资产请求：只记录路径与请求体，用来核对调用次数。 */
function record(page: import("@playwright/test").Page) {
  const calls: { path: string; body: string }[] = [];
  page.on("request", (sent) => {
    if (sent.url().includes(API)) {
      calls.push({
        path: sent.url().slice(sent.url().indexOf(API) + API.length),
        body: sent.postData() ?? "",
      });
    }
  });
  return calls;
}

test("开发模式 StrictMode：首挂载复演不会清掉已选连接", async ({
  page,
  request,
}) => {
  await ready(request);
  await login(page);
  const calls = record(page);

  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption("library-a");
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成一号库",
  );

  // 首挂载复演之后，范围仍然在：库行不会因为一次多余的读取而消失。
  await page.waitForTimeout(600);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成一号库",
  );

  // 只有一次“选择连接”，其余都是只读的状态读取与库列表读取。
  const choosing = calls.filter(
    (call) => call.path === "connection" && call.body.includes("library-a"),
  );
  expect(choosing).toHaveLength(1);
  const clearing = calls.filter(
    (call) => call.path === "connection" && call.body === "{}",
  );
  expect(clearing).toHaveLength(0);
  expect(calls.filter((call) => call.path === "state").length).toBeGreaterThan(
    0,
  );
});

test("开发模式 StrictMode：离开再回到资产页，范围与正文都重新读取", async ({
  page,
  request,
}) => {
  await ready(request);
  await login(page);
  await connect(page);
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");

  // 离开资产页再回来：这是真实的重挂载，不是同一实例的第二次 effect。
  await page.goto("/#/home/devices");
  await page.goto(PAGE);
  await expect(page.locator(".asset-library-row").first()).toContainText(
    "合成一号库",
  );

  // 再进入一个库：目录照常读出来，说明会话里的范围在重挂载后仍然有效。
  await page.locator(".asset-library-row").first().click();
  await expect(page.locator(".asset-row").first()).toContainText("归档");
  await expect(page.locator(".asset-row")).toHaveCount(4);
});

test("开发模式 StrictMode：库列表续读只读一次，不会把同一页接两遍", async ({
  page,
  request,
}) => {
  await ready(request);
  await login(page);
  await scenario(request, "paged_libraries");
  const calls = record(page);

  await page.goto(PAGE);
  await page.getByLabel("资产连接").selectOption("library-a");
  await expect(page.locator(".asset-library-row")).toHaveCount(50);

  // 续读是一次显式动作，并且只发一次带续读标记的请求：StrictMode 的复演不会让它读两遍，
  // 也不会把第二页接在列表里两次。
  await page.getByRole("button", { name: "继续读取下一页" }).click();
  await expect(page.locator(".asset-library-row")).toHaveCount(100);
  await page.waitForTimeout(400);
  await expect(page.locator(".asset-library-row")).toHaveCount(100);
  await expect(page.locator(".asset-library-row").nth(50)).toContainText(
    "合成多页库051",
  );

  const continuations = calls.filter(
    (call) => call.path === "libraries" && call.body.includes("cursor"),
  );
  expect(continuations).toHaveLength(1);
  // 第一页只读一次：没有续读标记的那次读取不该被复演成两次。
  const firstPages = calls.filter(
    (call) => call.path === "libraries" && !call.body.includes("cursor"),
  );
  expect(firstPages).toHaveLength(1);
});
