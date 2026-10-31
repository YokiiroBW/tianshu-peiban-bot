import { test, expect } from "@playwright/test";

test("workbench renders actual host readings and the shell stays within the viewport", async ({
  page,
}, info) => {
  let configured = true;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/session"))
      return route.fulfill({
        json: {
          authenticated: true,
          username: "ui-review",
          csrf: "synthetic-ui-review",
          conversations: [],
          dialogue: { available: false, code: "not_configured", model: "" },
        },
      });
    if (path.endsWith("/infrastructure/host"))
      return route.fulfill({
        json: {
          configured,
          node: configured
            ? {
                id: "explicit-host",
                label: "天枢运行主机",
                snapshot: {
                  freshness: "current",
                  observed_at: "2026-10-29T00:00:00Z",
                  data: {
                    identity: { hostname: "synthetic-host" },
                    system: { uptime_seconds: 7200 },
                    cpu: { usage_ratio: 0.25 },
                    memory: { used_bytes: 4294967296, total_bytes: 8589934592 },
                    filesystems: [
                      {
                        id: "root",
                        mountpoint: "/",
                        used_bytes: 100,
                        total_bytes: 1000,
                        available_bytes: 900,
                      },
                    ],
                  },
                },
              }
            : null,
        },
      });
    return route.fulfill({
      status: 503,
      json: { code: "dependency_unavailable" },
    });
  });
  await page.goto("/#/workbench");
  const resources = page.getByRole("region", { name: "天枢主机资源" });
  await expect(resources).toContainText("synthetic-host");
  await expect(
    resources.getByRole("meter", { name: "CPU 使用率" }),
  ).toHaveAttribute("aria-valuenow", "25");
  await expect(
    resources.getByRole("meter", { name: "内存", exact: true }),
  ).toHaveAttribute("aria-valuenow", "50");
  await expect(page.getByRole("heading", { name: "开始使用天枢" })).toHaveCount(
    0,
  );
  if (info.project.name === "desktop") {
    await page.getByRole("button", { name: "收起侧栏", exact: true }).click();
    await expect(page.locator(".app-shell")).toHaveClass(
      /is-sidebar-collapsed/,
    );
    await page.reload();
    await expect(
      page.getByRole("button", { name: "展开侧栏", exact: true }),
    ).toBeVisible();
  }
  configured = false;
  await page.getByRole("button", { name: "刷新读数", exact: true }).click();
  await expect(resources).toContainText("待绑定主机");
  await expect(resources.getByRole("meter")).toHaveCount(0);
  for (const route of [
    "workbench",
    "companion",
    "resources",
    "subscriptions",
    "home",
    "settings",
    "memory/0",
  ]) {
    await page.goto(`/#/${route}`);
    await expect(page.locator(".page-content")).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => ({
          width: document.documentElement.scrollWidth <= innerWidth + 1,
          height: document.documentElement.scrollHeight <= innerHeight + 1,
        })),
      )
      .toEqual({ width: true, height: true });
  }
  expect(errors).toEqual([]);
});
