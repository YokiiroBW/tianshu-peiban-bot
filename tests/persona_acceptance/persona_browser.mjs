import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(
  pathToFileURL(process.env.PERSONA_NODE_MODULE_ANCHOR),
);
const { chromium } = require("@playwright/test");

const origin = process.env.PERSONA_ORIGIN;
const forbiddenValues = [
  process.env.PERSONA_TOKEN,
  "Bearer",
  process.env.PERSONA_CORE_URL,
  process.env.PERSONA_PACKAGE_PATH,
  process.env.PERSONA_CA_PATH,
  process.env.PERSONA_CONNECTION,
  "core.sqlite",
  "actor:hidden",
];
const apiBodies = [];
const apiRequests = [];
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on("request", (request) => {
    if (request.url().includes("/api/web/personas/"))
      apiRequests.push({ url: request.url(), body: request.postData() ?? "" });
  });
  page.on("response", async (response) => {
    if (!response.url().includes("/api/web/personas/")) return;
    try {
      apiBodies.push(await response.text());
    } catch {
      // A response body that cannot be read still fails through the page assertions below.
    }
  });

  await page.goto(`${origin}/#/companion/2`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "退出登录" }).waitFor({ state: "visible" });
  await page.locator(".persona-state").waitFor({ state: "visible" });
  await page.locator(".persona-subject").first().waitFor({ state: "visible" });

  const subjects = page.locator(".persona-subject");
  assert.equal(await subjects.count(), 2, "the deployment allowlist is the entire browser catalog");
  const renderedCatalog = await page.locator(".persona-page").innerText();
  assert.match(renderedCatalog, /actor:alpha/);
  assert.match(renderedCatalog, /actor:beta/);
  assert.doesNotMatch(renderedCatalog, /actor:hidden/);
  assert.match(await page.locator(".persona-state").innerText(), /已授权角色 2 个/);

  await subjects.filter({ hasText: "actor:alpha" }).click();
  const rows = page.locator(".persona-rows > li");
  await rows.first().waitFor({ state: "visible" });
  assert.ok((await rows.count()) >= 3, "the real producer supplied revision history");
  await rows.first().getByRole("button", { name: "查看这一版" }).click();
  const selected = page.locator('[aria-label="选中的版本"]');
  await selected.waitFor({ state: "visible" });
  assert.equal(await selected.locator(".persona-field").count(), 4);
  assert.ok(
    (await selected.locator(".persona-field-body").allTextContents()).some(
      (value) => value.trim().length > 0,
    ),
    "the page rendered persona content from a real revision",
  );

  await rows.first().getByRole("button", { name: "设为对比基线" }).click();
  await rows.nth(1).getByRole("button", { name: "与基线比较" }).click();
  const comparison = page.locator('[aria-label="版本比较"]');
  await comparison.waitFor({ state: "visible" });
  assert.equal(await comparison.locator(".persona-change").count(), 4);
  assert.match(await comparison.innerText(), /字段内容不同/);

  await page.waitForTimeout(100);
  const bodies = [apiBodies.join("\n"), ...apiRequests.map((request) => request.body)].join(
    "\n",
  );
  for (const value of forbiddenValues)
    assert.ok(!bodies.includes(value), `browser API traffic exposed a server-only value: ${value}`);
  const urls = apiRequests.map((request) => new URL(request.url));
  assert.ok(urls.length >= 4, "the page made its own Platform persona reads");
  for (const url of urls) {
    assert.equal(url.origin, origin, "the browser must use same-origin Platform routes");
    assert.match(url.pathname, /^\/api\/web\/personas\/(catalog|history|revision|compare)$/);
  }
  const routes = [...new Set(urls.map((url) => url.pathname.split("/").at(-1)))].sort();
  assert.deepEqual(routes, ["catalog", "compare", "history", "revision"]);

  console.log(
    "PERSONA_BROWSER_RESULT=" +
      JSON.stringify({
        catalogSubjects: await subjects.count(),
        historyRows: await rows.count(),
        revisionFields: 4,
        comparisonFields: await comparison.locator(".persona-change").count(),
        routes,
        mockedApiRoutes: 0,
      }),
  );
  await context.close();
} finally {
  await browser.close();
}
