import { expect, test, type Page, type Route } from "@playwright/test";

async function answer(route: Route, value: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

async function setup(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    answer(route, {
      authenticated: true,
      csrf: "synthetic-external-session",
      username: "fixture-manager",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
  await page.route("**/api/v1/access/view", (route) =>
    answer(route, {
      available: false,
      active: { mode: "http", origin: "", certificate: null },
      saved: { mode: "http", origin: "", certificate: null },
      revision: 1,
      restart_required: false,
      certificates: [],
      listener_port: 80,
    }),
  );
  await page.route("**/api/v1/connections/view", (route) =>
    answer(route, { connections: [] }),
  );
}

function view(revision: number, lastTest: object | null = null) {
  return {
    revision,
    unlocked: true,
    assets: {
      configured: true,
      enabled: true,
      url: "https://asset.example.test/assetlink/v1/control",
      credential_configured: true,
      ca_configured: false,
      last_test: lastTest,
    },
    home: {
      configured: false,
      enabled: false,
      url: null,
      credential_configured: false,
      ca_configured: false,
      last_test: null,
      allow_private_http: false,
      entities: [],
    },
  };
}

test("a concurrent configuration change expires an in-flight connection test", async ({
  page,
}) => {
  await setup(page);
  let revision = 4;
  let lastTest: object | null = null;
  let releaseTest!: () => void;
  let markStarted!: () => void;
  const started = new Promise<void>((resolve) => (markStarted = resolve));
  const held = new Promise<void>((resolve) => (releaseTest = resolve));
  await page.route("**/api/v1/external/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/view")) return answer(route, view(revision, lastTest));
    if (path.endsWith("/test")) {
      markStarted();
      await held;
      lastTest = {
        state: "connected",
        code: "read_observed",
        checked_at: "2026-09-27T00:00:00Z",
        revision: 4,
      };
      return answer(route, { kind: "assets", ...lastTest });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/settings/assets");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await page.getByRole("button", { name: "检测连接（只读一次）" }).click();
  await started;
  revision = 5; // A second manager saved a new global revision while the old test ran.
  releaseTest();
  await expect(page.getByText("检测已过期", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("检测结果对应旧配置", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("一次真实只读检测已通过", { exact: false }),
  ).toHaveCount(0);
});

test("a failed refresh cannot confirm a successful test against current settings", async ({
  page,
}) => {
  await setup(page);
  let views = 0;
  await page.route("**/api/v1/external/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/view")) {
      views++;
      return views === 1
        ? answer(route, view(4))
        : answer(route, { code: "dependency_unavailable" }, 503);
    }
    if (path.endsWith("/test"))
      return answer(route, {
        kind: "assets",
        state: "connected",
        code: "read_observed",
        checked_at: "2026-09-27T00:00:00Z",
        revision: 4,
      });
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/settings/assets");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await page.getByRole("button", { name: "检测连接（只读一次）" }).click();
  await expect(
    page.getByText("上游暂时无法读取", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("一次真实只读检测已通过", { exact: false }),
  ).toHaveCount(0);
});
