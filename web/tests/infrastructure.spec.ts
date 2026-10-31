import { test, expect, type Locator, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { randomUUID } from "node:crypto";
import type {
  Container,
  ContainerDetail,
  ContainerList,
  ContainerStats,
  InfrastructureView,
} from "../src/features/infrastructure/types";

async function login(page: Page) {
  await page.goto("/#/settings/nodes");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("已配置的 SSH 节点")).toBeVisible();
}

async function register(page: Page, id: string, label: string) {
  await page.goto("/#/settings/nodes");
  await page.getByLabel("已配置的 SSH 节点").selectOption(id);
  // The fixture is shared by this serial suite. Bind setup to a fresh read;
  // a prior save's notice can arrive before its background view reload.
  await page.getByRole("button", { name: "重新读取设置", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "已重新读取节点设置" }),
  ).toBeVisible();
  await page.getByLabel("显示名称").fill(label);
  await page.getByLabel("启用后台采集").check();
  await page.getByRole("button", { name: "保存节点", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "节点已保存" }),
  ).toBeVisible();
}

test("real platform HTTP: node registration, 130 containers, separate health, details, logs and OpenWrt", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  await login(page);
  await expect(page.locator('option[value="fixture-nas"]')).toContainText(
    "容器清单",
  );
  await register(page, "fixture-nas", "合成 NAS");
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(
    page.getByText("已安排连接测试", { exact: false }),
  ).toBeVisible();
  await page.goto("/#/system/containers");
  await page.getByRole("button", { name: /^合成 NAS/ }).click();
  const list = page.getByRole("region", { name: "容器列表", exact: true });
  await expect(
    list.getByRole("heading", { name: "容器清单 130 个" }),
  ).toBeVisible();
  await expect(list.locator(".infra-container-row")).toHaveCount(100);
  await expect(
    list
      .locator(".infra-container-row .infra-health")
      .filter({ hasText: "未配置检查" })
      .first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查看容器 synthetic-app-003", exact: true })
    .click();
  const detail = page.getByRole("complementary", {
    name: "容器详情",
    exact: true,
  });
  await expect(
    detail.getByRole("heading", { name: "synthetic-app-003", exact: true }),
  ).toBeVisible();
  await expect(detail.locator(".status-rail.tone-red")).toContainText("运行中");
  await expect(
    detail.getByText("健康检查 · 检查异常", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("挂载与持久数据", { exact: true }),
  ).toBeVisible();
  await expect(detail).toContainText("/srv/synthetic-stacks/synthetic/data");
  await page.getByRole("button", { name: "读取最近日志", exact: true }).click();
  await expect(page.getByLabel("容器日志预览")).toContainText("合成容器日志");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(list.locator(".infra-container-row")).toHaveCount(30);
  const last = page.getByRole("button", {
    name: "查看容器 synthetic-app-130",
    exact: true,
  });
  await last.focus();
  await page.keyboard.press("Enter");
  await expect(
    detail.getByRole("heading", { name: "synthetic-app-130", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /utilities.*65 个容器/ }).click();
  await expect(
    list.getByRole("heading", { name: "容器清单 65 个" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "全部容器", exact: true }).click();
  await page.getByLabel("搜索容器", { exact: true }).fill("synthetic-app-003");
  await expect(list.locator(".infra-container-row")).toHaveCount(1);
  await page
    .getByRole("combobox", { name: "健康检查", exact: true })
    .selectOption("unhealthy");
  await expect(
    list.getByRole("heading", { name: "容器清单 1 个" }),
  ).toBeVisible();
  await expect(page.locator(".infra-workspace")).toBeVisible();
  const violations = await new AxeBuilder({ page })
    .include(".infra-workspace")
    .analyze();
  expect(violations.violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("docker-light.png"),
    fullPage: true,
  });
  await page.evaluate(() =>
    document.documentElement.setAttribute("data-theme", "dark"),
  );
  await page.screenshot({
    path: testInfo.outputPath("docker-dark.png"),
    fullPage: true,
  });
  await register(page, "fixture-router", "合成路由器");
  await page.goto("/#/system/nodes");
  await page.getByRole("button", { name: /^合成路由器/ }).click();
  await expect(
    page.getByRole("heading", { name: "OpenWrt 与网络", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Synthetic Router", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("192.0.2.10/24", { exact: true })).toBeVisible();
  await expect(
    page.getByText("逐终端流量尚未接入", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "游戏服", exact: true }),
  ).toBeVisible();
  await page.evaluate(() =>
    document.documentElement.setAttribute("data-theme", "dark"),
  );
  expect(
    (await new AxeBuilder({ page }).include(".infra-workspace").analyze())
      .violations,
  ).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("openwrt-dark.png"),
    fullPage: true,
  });
});

test("switching nodes discards a late real HTTP response and stale projections label old values", async ({
  page,
}) => {
  test.setTimeout(45000);
  await login(page);
  await register(page, "fixture-nas", "合成 NAS");
  await register(page, "fixture-router", "合成路由器");
  await page.route("**/api/v1/infrastructure/node", async (route) => {
    const request = route.request().postDataJSON();
    const response = await route.fetch();
    if (request.node_id === "fixture-nas")
      await new Promise((resolve) => setTimeout(resolve, 1200));
    await route.fulfill({ response }).catch(() => {});
  });
  await page.goto("/#/system/nodes");
  await page.getByRole("button", { name: /^合成路由器/ }).click();
  await expect(
    page.getByRole("heading", { name: "OpenWrt 与网络", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(
    page.getByRole("heading", { name: "合成 NAS", exact: true }),
  ).toHaveCount(0);
  await page.unroute("**/api/v1/infrastructure/node");
  await page.route("**/api/v1/infrastructure/node", async (route) => {
    const response = await route.fetch();
    const value = await response.json();
    value.connection_state = "offline";
    value.snapshot.freshness = "stale";
    value.snapshot.reason_codes = ["ssh_unavailable"];
    await route.fulfill({ json: value });
  });
  await page.getByRole("button", { name: /^合成 NAS/ }).click();
  await expect(
    page.getByText("上次读数", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("SSH 连接不可用", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("12 GiB", { exact: true })).toBeVisible();
});

test("polling keeps a draft bound to its read revision and a concurrent save conflicts", async ({
  page,
}) => {
  await login(page);
  await register(page, "fixture-nas", "合成 NAS");
  await page.getByLabel("显示名称").fill("未保存的名称");
  const session = await (await page.request.get("/api/v1/session")).json();
  const headers = {
    Origin: "http://127.0.0.1:4827",
    "X-CSRF-Token": session.csrf,
  };
  const view = await (
    await page.request.post("/api/v1/infrastructure/view", {
      data: {},
      headers,
    })
  ).json();
  const response = await page.request.post("/api/v1/infrastructure/save", {
    data: {
      node_id: "fixture-nas",
      label: "其他页面的名称",
      enabled: true,
      expected_revision: view.revision,
      client_request_id: randomUUID(),
    },
    headers,
  });
  expect(response.status()).toBe(200);
  await expect(
    page.getByText("连接设置已在其他页面改变", { exact: false }),
  ).toBeVisible({ timeout: 8000 });
  await page.getByRole("button", { name: "保存节点", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("revision_conflict");
  const saved = await (
    await page.request.post("/api/v1/infrastructure/view", {
      data: {},
      headers,
    })
  ).json();
  expect(
    saved.nodes.find((node: { id: string }) => node.id === "fixture-nas").label,
  ).toBe("其他页面的名称");
  await page.getByRole("button", { name: "重新读取设置", exact: true }).click();
  await expect(page.getByLabel("显示名称")).toHaveValue("其他页面的名称");
});

const MiB = 1024 ** 2;
const denseNodeLabel = `合成 NAS · ${"long-node-label-".repeat(6)}`;
const denseId = (index: number) => index.toString(16).padStart(64, "0");
const denseName = (index: number) =>
  `synthetic-dense-${String(index).padStart(3, "0")}-${"long-container-name-".repeat(7)}`;

function statsFor(
  index: number,
  cpu: number | null = 0.25,
  memory: ContainerStats["memory"] = {
    raw_usage_bytes: 768 * MiB,
    cache_adjusted_usage_bytes: 256 * MiB,
    cache_source: "total_inactive_file",
    limit_bytes: 1024 * MiB,
  },
): ContainerStats {
  return {
    id: denseId(index),
    availability: "ok",
    reason_codes: [],
    sampled_at: "2026-10-17T12:00:00Z",
    cpu: { usage_ratio: cpu, basis: "single_core", state: "ok" },
    memory,
    block_io: { read_bytes: 1024, write_bytes: 2048 },
    network: {
      receive_bytes: 4096,
      transmit_bytes: 8192,
      attribution: "container",
    },
    rates: {
      receive_bytes_per_second: 1024,
      transmit_bytes_per_second: 2048,
      read_bytes_per_second: 256,
      write_bytes_per_second: 512,
      state: "ok",
    },
  };
}

// Keep the existing real login, registration, HTTP transport and observation
// metadata. Only the browser projection is enlarged; no new server or real node
// is involved, and the original tests still exercise backend filtering.
async function installContainerProjection(
  page: Page,
  options: {
    count?: number;
    nodes?: number;
    mounts?: number;
    statistics?: (ContainerStats | null)[];
  } = {},
) {
  const count = options.count ?? 397;
  const containers: Container[] = Array.from({ length: count }, (_, offset) => {
    const index = offset + 1;
    return {
      id: denseId(index),
      name: denseName(index),
      image_reference: `registry.example.invalid/${"long-image-path/".repeat(8)}app@sha256:${denseId(index)}`,
      state: index % 7 === 0 ? "exited" : "running",
      health: index % 9 === 0 ? "unhealthy" : "none",
      availability: "ok",
      compose: {
        project: `project-${String(offset % 40).padStart(2, "0")}-${"long-application-".repeat(6)}`,
        service: `service-${index}`,
      },
      stats: options.statistics ? options.statistics[offset] : statsFor(index),
    };
  });
  const applications = [
    ...new Set(containers.map((row) => row.compose!.project!)),
  ]
    .sort()
    .map((name) => ({
      name,
      containers: containers.filter((row) => row.compose?.project === name)
        .length,
    }));

  await page.route("**/api/v1/infrastructure/view", async (route) => {
    const response = await route.fetch();
    const value: InfrastructureView = await response.json();
    const node = value.nodes.find((item) => item.id === "fixture-nas");
    if (node)
      value.nodes = Array.from({ length: options.nodes ?? 20 }, (_, index) => ({
        ...node,
        id: index === 0 ? node.id : `fixture-layout-${index}`,
        label:
          index === 0 ? denseNodeLabel : `节点 ${index} · ${denseNodeLabel}`,
        refresh: null,
      }));
    await route.fulfill({ response, json: value });
  });
  await page.route("**/api/v1/infrastructure/containers", async (route) => {
    const request = route.request().postDataJSON();
    const response = await route.fetch({
      postData: JSON.stringify({
        ...request,
        query: "",
        application: null,
        state: null,
        health: null,
        page: 0,
      }),
    });
    const value: ContainerList = await response.json();
    if (!response.ok() || !value.containers.length) {
      await route.fulfill({ response });
      return;
    }
    const filtered = containers.filter(
      (row) =>
        (!request.query ||
          `${row.name} ${row.image_reference} ${row.id}`.includes(
            request.query,
          )) &&
        (!request.state || row.state === request.state) &&
        (!request.health || row.health === request.health) &&
        (request.application === null ||
          row.compose?.project === request.application),
    );
    const pageSize = value.page_size;
    value.node = { ...value.node, label: denseNodeLabel, refresh: null };
    value.containers = filtered.slice(
      request.page * pageSize,
      (request.page + 1) * pageSize,
    );
    value.total = filtered.length;
    value.inventory_total = count;
    value.page = request.page;
    value.has_more = (request.page + 1) * pageSize < filtered.length;
    value.applications = applications;
    value.inventory = {
      ...value.inventory,
      freshness: "current",
      reason_codes: [],
      completeness: {
        state: "complete",
        returned: count,
        total: count,
        next_cursor: null,
      },
    };
    await route.fulfill({ response, json: value });
  });
  await page.route("**/api/v1/infrastructure/container", async (route) => {
    const request = route.request().postDataJSON();
    // All 397 synthetic identities use an actual readable fixture detail as
    // their metadata source, including identities beyond its original 130.
    const response = await route.fetch({
      postData: JSON.stringify({ ...request, container_id: denseId(1) }),
    });
    if (!response.ok()) {
      await route.fulfill({ response });
      return;
    }
    const value: ContainerDetail = await response.json();
    const selected = containers.find((row) => row.id === request.container_id)!;
    value.node = { ...value.node, label: denseNodeLabel, refresh: null };
    value.container = {
      ...value.container,
      ...selected,
      mounts: Array.from({ length: options.mounts ?? 48 }, (_, index) => ({
        kind: "bind",
        source: `/srv/synthetic-stacks/${"long-persistent-path/".repeat(5)}mount-${index}`,
        target: `/container/mount-${index}`,
        read_only: index % 2 === 0,
      })),
    };
    value.stats = selected.stats;
    await route.fulfill({ response, json: value });
  });
  return { containers, applications };
}

async function assertScrollable(group: Locator) {
  const dimensions = await group.evaluate((element) => ({
    client: element.clientHeight,
    content: element.scrollHeight,
    overflow: getComputedStyle(element).overflowY,
    tabIndex: (element as HTMLElement).tabIndex,
  }));
  expect(dimensions.client).toBeGreaterThan(0);
  expect(dimensions.content).toBeGreaterThan(dimensions.client);
  expect(["auto", "scroll"]).toContain(dimensions.overflow);
  expect(dimensions.tabIndex).toBeGreaterThanOrEqual(0);
  await group.focus();
  await group.press("End");
  await expect
    .poll(() => group.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await group.press("Home");
  await expect
    .poll(() => group.evaluate((element) => element.scrollTop))
    .toBe(0);
}

async function assertControlsInside(card: Locator, controls: Locator) {
  const bounds = await card.boundingBox();
  expect(bounds).not.toBeNull();
  const boxes = await controls.evaluateAll((elements) =>
    elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
      };
    }),
  );
  for (const box of boxes) {
    expect(box.left).toBeGreaterThanOrEqual(bounds!.x - 1);
    expect(box.right).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
    expect(box.top).toBeGreaterThanOrEqual(bounds!.y - 1);
    expect(box.bottom).toBeLessThanOrEqual(bounds!.y + bounds!.height + 1);
  }
}

test("397-container workspace keeps scrolling inside cards and controls reachable across viewport sizes", async ({
  page,
}, testInfo) => {
  test.setTimeout(90000);
  await login(page);
  await register(page, "fixture-nas", "合成 NAS");
  const projection = await installContainerProjection(page);
  await page.goto("/#/system/containers");
  const list = page.getByRole("region", { name: "容器列表", exact: true });
  const detail = page.getByRole("complementary", {
    name: "容器详情",
    exact: true,
  });
  const selector = page.locator(".infra-docker-layout > .infra-selector");
  const nodes = page.getByRole("group", { name: "运行节点", exact: true });
  const apps = page.getByRole("group", { name: "Compose 应用", exact: true });
  const rows = list.locator(".infra-container-row");
  await expect(
    list.getByRole("heading", { name: "容器清单 397 个" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(rows).toHaveCount(100);
  await expect(
    detail.getByRole("heading", { name: denseName(1), exact: true }),
  ).toBeVisible();

  const viewports =
    testInfo.project.name === "mobile"
      ? [{ width: 390, height: 844 }]
      : [
          { width: 1440, height: 1000 },
          { width: 1280, height: 900 },
          { width: 1057, height: 1026 },
          { width: 3426, height: 1876 },
        ];
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      )
      .toBeTruthy();
    const cards = await Promise.all([
      selector.boundingBox(),
      list.boundingBox(),
      detail.boundingBox(),
    ]);
    for (const card of cards) {
      expect(card).not.toBeNull();
      expect(card!.height).toBeLessThanOrEqual(viewport.height);
    }
    for (let first = 0; first < cards.length; first++) {
      for (let second = first + 1; second < cards.length; second++) {
        const a = cards[first]!,
          b = cards[second]!;
        expect(
          a.x + a.width <= b.x + 1 ||
            b.x + b.width <= a.x + 1 ||
            a.y + a.height <= b.y + 1 ||
            b.y + b.height <= a.y + 1,
        ).toBeTruthy();
      }
    }
    if (viewport.width >= 1440) {
      expect(
        Math.max(...cards.map((card) => card!.y)) -
          Math.min(...cards.map((card) => card!.y)),
      ).toBeLessThanOrEqual(1);
      expect(
        Math.max(...cards.map((card) => card!.height)) -
          Math.min(...cards.map((card) => card!.height)),
      ).toBeLessThanOrEqual(1);
    }
    const denseHeight = await page.evaluate(
      () => document.documentElement.scrollHeight,
    );
    expect(denseHeight).toBeLessThanOrEqual(
      viewport.height * (viewport.width < 1200 ? 4 : 2),
    );

    // The sidebar's buttons can be vertically scrolled, but their horizontal
    // borders must remain within their owning card even for long labels.
    const sidebarWidths = await selector
      .getByRole("button")
      .evaluateAll((buttons) =>
        buttons.map((button) => {
          const card = button
            .closest(".infra-selector")!
            .getBoundingClientRect();
          const rect = button.getBoundingClientRect();
          return rect.left >= card.left && rect.right <= card.right;
        }),
      );
    expect(sidebarWidths.every(Boolean)).toBeTruthy();
    await assertScrollable(nodes);
    await assertScrollable(apps);
    const listContent = page.getByRole("group", {
      name: "容器清单内容",
      exact: true,
    });
    await assertScrollable(listContent);
    const rowCapacity = await listContent.evaluate(
      (element) =>
        element.clientHeight /
        element.querySelector(".infra-container-row")!.getBoundingClientRect()
          .height,
    );
    expect(rowCapacity).toBeGreaterThanOrEqual(viewport.width >= 1200 ? 3 : 2);
    await assertScrollable(
      page.getByRole("group", { name: "容器详情内容", exact: true }),
    );
    await assertControlsInside(list, list.getByRole("combobox"));
    await assertControlsInside(
      list,
      list.getByRole("textbox", { name: "搜索容器", exact: true }),
    );
    await assertControlsInside(
      list,
      list.getByRole("navigation", { name: "容器分页", exact: true }),
    );

    await page
      .getByLabel("搜索容器", { exact: true })
      .fill("synthetic-dense-001");
    await expect(rows).toHaveCount(1);
    const sparseHeight = await page.evaluate(
      () => document.documentElement.scrollHeight,
    );
    expect(Math.abs(denseHeight - sparseHeight)).toBeLessThanOrEqual(
      viewport.height * 0.15,
    );
    await page.getByLabel("搜索容器", { exact: true }).clear();
    await expect(rows).toHaveCount(100);
    await page.screenshot({
      path: testInfo.outputPath(`dense-${viewport.width}-light.png`),
      fullPage: true,
    });
  }

  for (const [pageNumber, rowCount] of [
    [2, 100],
    [3, 100],
    [4, 97],
  ]) {
    await list.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(
      list.getByRole("navigation", { name: "容器分页" }),
    ).toContainText(`第 ${pageNumber} 页`);
    await expect(rows).toHaveCount(rowCount);
  }
  await expect(
    list.getByRole("button", { name: "下一页", exact: true }),
  ).toBeDisabled();
  const last = list.getByRole("button", {
    name: `查看容器 ${denseName(397)}`,
    exact: true,
  });
  await last.focus();
  await last.press("Enter");
  await expect(
    detail.getByRole("heading", { name: denseName(397), exact: true }),
  ).toBeVisible();
  await detail
    .getByText("/container/mount-47", { exact: false })
    .scrollIntoViewIfNeeded();
  await detail
    .getByRole("button", { name: "读取最近日志", exact: true })
    .scrollIntoViewIfNeeded();
  const finalApp = projection.applications.at(-1)!;
  await apps
    .getByRole("button", {
      name: `${finalApp.name} ${finalApp.containers} 个容器`,
      exact: true,
    })
    .click();
  await expect(rows).toHaveCount(finalApp.containers);
  await apps.getByRole("button", { name: "全部容器", exact: true }).click();
  await expect(rows).toHaveCount(100);
  await page
    .getByRole("combobox", { name: "运行状态", exact: true })
    .selectOption("exited");
  await expect(rows).toHaveCount(56);
  await page
    .getByRole("combobox", { name: "健康检查", exact: true })
    .selectOption("unhealthy");
  await expect(rows).toHaveCount(6);
  await page
    .getByRole("combobox", { name: "运行状态", exact: true })
    .selectOption("");
  await page
    .getByRole("combobox", { name: "健康检查", exact: true })
    .selectOption("");
  await expect(rows).toHaveCount(100);
  await page.evaluate(() =>
    document.documentElement.setAttribute("data-theme", "dark"),
  );
  await page.screenshot({
    path: testInfo.outputPath("dense-dark.png"),
    fullPage: true,
  });
});

test("container resource bars preserve CPU basis, memory denominator, zero and unknown readings", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  await login(page);
  await register(page, "fixture-nas", "合成 NAS");
  const samples: (ContainerStats | null)[] = [
    statsFor(1),
    statsFor(2, 2.5, {
      raw_usage_bytes: 768 * MiB,
      cache_adjusted_usage_bytes: null,
      cache_source: null,
      limit_bytes: 2048 * MiB,
    }),
    statsFor(3, 0, {
      raw_usage_bytes: 768 * MiB,
      cache_adjusted_usage_bytes: 0,
      cache_source: "total_inactive_file",
      limit_bytes: 1024 * MiB,
    }),
    null,
    statsFor(5, null, {
      raw_usage_bytes: 128 * MiB,
      cache_adjusted_usage_bytes: null,
      cache_source: null,
      limit_bytes: null,
    }),
    statsFor(6, 0.5, {
      raw_usage_bytes: 128 * MiB,
      cache_adjusted_usage_bytes: null,
      cache_source: null,
      limit_bytes: 0,
    }),
    {
      ...statsFor(7),
      availability: "unavailable",
      reason_codes: ["container_not_running"],
    },
    statsFor(8, null, {
      raw_usage_bytes: null,
      cache_adjusted_usage_bytes: null,
      cache_source: null,
      limit_bytes: 1024 * MiB,
    }),
  ];
  await installContainerProjection(page, {
    count: samples.length,
    nodes: 1,
    mounts: 1,
    statistics: samples,
  });
  await page.goto("/#/system/containers");
  const list = page.getByRole("region", { name: "容器列表", exact: true });
  const detail = page.getByRole("complementary", {
    name: "容器详情",
    exact: true,
  });
  await expect(list.locator(".infra-container-row")).toHaveCount(
    samples.length,
    { timeout: 15000 },
  );
  const expectations = [
    {
      cpu: 25,
      memory: 25,
      bytes: "256 MiB",
      limit: "1 GiB",
      basis: "扣除缓存",
    },
    {
      cpu: 250,
      memory: 37.5,
      bytes: "768 MiB",
      limit: "2 GiB",
      basis: "原始用量",
    },
    { cpu: 0, memory: 0, bytes: "0 B", limit: "1 GiB", basis: "扣除缓存" },
    { cpu: null, memory: null, bytes: "—", limit: "—", basis: "原始用量" },
    {
      cpu: null,
      memory: null,
      bytes: "128 MiB",
      limit: "—",
      basis: "原始用量",
    },
    {
      cpu: 50,
      memory: null,
      bytes: "128 MiB",
      limit: "0 B",
      basis: "原始用量",
    },
    { cpu: null, memory: null, bytes: "—", limit: "—", basis: "原始用量" },
    { cpu: null, memory: null, bytes: "—", limit: "1 GiB", basis: "原始用量" },
  ];
  for (const [offset, expected] of expectations.entries()) {
    const row = list.getByRole("button", {
      name: `查看容器 ${denseName(offset + 1)}`,
      exact: true,
    });
    await row.click();
    await expect(
      detail.getByRole("heading", { name: denseName(offset + 1), exact: true }),
    ).toBeVisible();
    for (const owner of [row, detail]) {
      for (const resource of ["cpu", "memory"] as const) {
        const wrapper = owner.locator(
          `.infra-resource-meter[data-resource="${resource}"]`,
        );
        const meter = wrapper.getByRole("meter", {
          name: resource === "cpu" ? "CPU用量" : "内存用量",
          exact: true,
        });
        const percent = expected[resource];
        if (percent === null) {
          await expect(meter).toHaveCount(0);
          await expect(wrapper).toContainText("—");
          await expect(wrapper).not.toContainText("0.0%");
          await expect(
            wrapper.locator(".infra-resource-track > span"),
          ).toHaveCount(0);
        } else {
          await expect(meter).toHaveAttribute("aria-valuenow", String(percent));
          await expect(meter).toHaveAttribute("aria-valuemin", "0");
          await expect(wrapper).toContainText(`${percent.toFixed(1)}%`);
          const maximum = Number(await meter.getAttribute("aria-valuemax"));
          expect(maximum).toBeGreaterThanOrEqual(percent);
          await expect
            .poll(() =>
              meter.evaluate((element) => {
                const fill = element.firstElementChild!.getBoundingClientRect();
                return fill.width / element.clientWidth;
              }),
            )
            .toBeCloseTo(Math.min(percent, 100) / 100, 2);
        }
      }
    }
    const memory = detail.locator(
      '.infra-resource-meter[data-resource="memory"]',
    );
    await expect(memory).toContainText(expected.bytes);
    await expect(memory).toContainText(expected.basis);
    await expect(detail).toContainText(`限额 ${expected.limit}`);
    await expect(detail).toContainText("单核");
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("resource-unknown.png"),
    fullPage: true,
  });
});
