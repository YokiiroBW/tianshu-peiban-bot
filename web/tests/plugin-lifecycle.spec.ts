import { test, expect } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";

const action = {
  id: "value",
  title: "取值",
  effect: "read",
  input_schema: { type: "object", properties: {} },
  output_schema: {},
  resumable: false,
  availability: { available: true, missing_capabilities: [] },
};

function instance(overrides: Record<string, unknown> = {}) {
  return {
    id: "instance",
    name: "实例",
    package_id: "example",
    version: "1.0.0",
    revision: 3,
    desired_state: "enabled",
    state: "ready",
    problem: null,
    config: {},
    config_schema: { properties: {} },
    secret_configured: {},
    bindings: [],
    binding_overrides: [],
    missing_capabilities: [],
    plugin_runtime: {
      owner_id: "worker",
      generation: "generation-alpha",
      pid: 4242,
      instance_revision: 3,
      observed_at: "2026-11-08T08:00:00Z",
    },
    contributions: { actions: [action], views: [] },
    ...overrides,
  };
}

function overview(value: Record<string, unknown>) {
  return {
    packages: [
      {
        id: "example@1.0.0",
        manifest: {
          id: "example",
          name: "插件",
          version: "1.0.0",
          description: "",
          author: "",
          capabilities: { required: [], optional: [] },
        },
        digest: "x",
        installed: true,
        source: {},
      },
      {
        id: "example@2.0.0",
        manifest: {
          id: "example",
          name: "插件",
          version: "2.0.0",
          description: "",
          author: "",
          capabilities: { required: [], optional: [] },
        },
        digest: "y",
        installed: true,
        source: {},
      },
    ],
    instances: [value],
    catalog: [],
    installations: [],
    capabilities: [],
    notifications: [],
  };
}

async function openPanel(page: import("@playwright/test").Page, value = instance()) {
  await nativeSession(page);
  await page.route(apiPath("plugins/view"), (route) => answer(route, overview(value)));
  for (const path of ["plugins/invocations", "plugins/logs", "plugins/schedules"])
    await page.route(apiPath(path), (route) => answer(route, { items: [] }));
  await page.goto("/#/plugins");
  await page.getByRole("button", { name: "实例 运行中", exact: true }).click();
}

test("reload reports the new serving process and never claims success on failure", async ({ page }) => {
  await openPanel(page);
  let reloaded = false;
  await page.route(apiPath("plugins/reload"), async (route) => {
    const body = route.request().postDataJSON();
    reloaded = true;
    if (!reloaded) return;
    return answer(route, command(body, instance({
      revision: 4,
      plugin_runtime: { owner_id: "worker", generation: "generation-beta", pid: 5252, instance_revision: 4 },
    })));
  });
  const reloadButton = page.getByRole("button", { name: "重新加载" });
  await expect(reloadButton).toBeVisible();
  await reloadButton.click();
  await expect(page.getByText(/已重建该实例的运行进程/)).toBeVisible();
  await expect(page.getByText(/PID 5252/)).toBeVisible();
  expect(reloaded).toBe(true);

  // A refused reload must surface its own error, not a success notice.
  await page.route(apiPath("plugins/reload"), (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "plugin_start_failed",
          message: "启动失败",
          details: { error: { code: "plugin_start_failed", rolled_back: false } },
          request_id: "r",
        },
      }),
    }),
  );
  await page.getByRole("button", { name: "重新加载" }).click();
  await expect(page.getByText(/启动失败/)).toBeVisible();
  await expect(page.getByText(/已重建该实例的运行进程/)).toHaveCount(0);
});

test("upgrade shows the version only after the serving process took over", async ({ page }) => {
  await openPanel(page);
  let failed = false;
  await page.route(apiPath("plugins/view"), (route) =>
    answer(route, overview(instance(failed ? { version: "1.0.0", revision: 3 } : {}))),
  );
  await page.route(apiPath("plugins/upgrade"), async (route) => {
    const body = route.request().postDataJSON();
    if (failed)
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "plugin_start_failed",
            message: "启动失败",
            details: {
              operation_id: "upgrade:one",
              execution: "failed",
              error: { code: "plugin_start_failed", rolled_back: false, failed_version: "2.0.0" },
            },
            request_id: "r",
          },
        }),
      });
    const upgraded = instance({
      version: "2.0.0",
      revision: 4,
      plugin_runtime: { owner_id: "worker", generation: "generation-two", pid: 6262, instance_revision: 4 },
    });
    return answer(route, command(body, upgraded));
  });
  await page.getByRole("button", { name: "版本", exact: true }).click();
  await page.getByRole("combobox", { name: "已安装版本" }).selectOption("2.0.0");
  await page.getByRole("button", { name: "升级到此版本" }).click();
  await expect(page.getByText(/已切换到 2\.0\.0/)).toBeVisible();
  failed = true;
  await page.getByRole("combobox", { name: "已安装版本" }).selectOption("2.0.0");
  await page.getByRole("button", { name: "升级到此版本" }).click();
  await expect(page.getByText(/启动失败/)).toBeVisible();
  await expect(page.getByText(/已切换到 2\.0\.0/)).toHaveCount(0);
});

test("uninstall keeps data by default and states the real purge scope", async ({ page }) => {
  await openPanel(page);
  const sent: Record<string, unknown>[] = [];
  await page.route(apiPath("plugins/uninstall"), async (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    return answer(route, command(body, { uninstalled: true, instance_data: body.purge_data ? "purged" : "retained" }));
  });
  await page.getByRole("button", { name: "版本", exact: true }).click();
  await expect(page.getByText(/该插件包下所有实例/)).toBeVisible();
  await page.getByRole("button", { name: "卸载并清除此插件数据" }).click();
  await expect.poll(() => sent.length).toBeGreaterThan(0);
  expect(sent[0]).toMatchObject({ package_id: "example", purge_data: true });
  await expect(page.getByText(/已卸载并清除该插件自有数据/)).toBeVisible();
});
