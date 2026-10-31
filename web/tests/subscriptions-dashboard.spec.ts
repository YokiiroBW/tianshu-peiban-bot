import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  fixtureJob,
  fixtureSubscription,
  fixtureView,
  installMediaFixture,
  mediaNavigation,
} from "./media.fixtures";

function dashboard() {
  const view = fixtureView();
  view.overview!.jobs = {
    total: 1200,
    published: 930,
    processing: 12,
    queued: 230,
    attention: 18,
    cancelled: 10,
  };
  view.subscriptions = ["active", "paused", "rule_error", "auth_required"].map(
    (state, index) => ({
      ...fixtureSubscription(),
      subscription_id: `dashboard-sub-${index}`,
      label: [
        "城市与山海 · 收藏夹",
        "旅行纪录片 · 合集",
        "技术分享 · UP 主",
        "音乐现场 · 系列",
      ][index],
      state: state as "active" | "paused" | "rule_error" | "auth_required",
      source: {
        kind: ["favorite", "collection", "uploader", "series"][index] as
          "favorite" | "collection" | "uploader" | "series",
        id: `123456:${index + 1}`,
      },
      baseline_ready: index !== 3,
      counts: { members: 12 + index, queued: 3 + index },
    }),
  );
  view.jobs = [
    "downloading",
    "completed",
    "failed",
    "queued",
    "library_verifying",
    "published",
  ].map((state, index) =>
    fixtureJob({
      job_id: `dashboard-job-${index}`,
      title: [
        "城市与山海 · 第一季",
        "一场音乐现场",
        "旅行纪录片 · 海岛",
        "技术分享 · 新一期",
        "城市延时摄影",
        "更早的发布任务",
      ][index],
      creator: "合成视频作者",
      state,
      stage: state,
      progress: state === "downloading" ? 0.5 : null,
      code: state === "failed" ? "engine_unavailable" : "",
    }),
  );
  return view;
}
const kpis = (page: import("@playwright/test").Page) =>
  page.getByLabel("全部下载任务统计");

test("overview uses full counters independently of its hundred recent jobs", async ({
  page,
}) => {
  const view = dashboard();
  view.jobs = Array.from({ length: 100 }, (_, index) =>
    fixtureJob({
      job_id: `recent-${index}`,
      title: `近期视频 ${index}`,
      state: "completed",
    }),
  );
  await installMediaFixture(page, view);
  await page.goto("/#/media");
  await expect(kpis(page).locator(".overview-kpi-value")).toHaveText([
    "930",
    "12",
    "230",
    "18",
  ]);
  await expect(page.locator(".overview-total")).toHaveText(
    "累计 1,200 个任务 · 其中 10 个已取消",
  );
  await expect(page.locator(".overview-recent-row")).toHaveCount(5);
  await expect(
    page.getByRole("img", {
      name: "视频订阅状态：运行中 1 个，已暂停 1 个，需要处理 2 个",
    }),
  ).toBeVisible();
  await expect(page.locator(".overview-subscription-card")).toHaveCount(0);
  for (const link of await kpis(page).getByRole("link").all()) {
    if ((await link.getAttribute("href"))?.includes("media-filter")) continue;
    await expect(link).toHaveAttribute("href", "#/media/jobs");
  }
  await kpis(page)
    .getByRole("link", { name: /^需要处理，18 个任务/ })
    .click();
  await expect(page).toHaveURL(/#\/subscriptions\/2$/);
  await expect(page.getByLabel("筛选任务")).toHaveValue("attention");
});

test("overview preserves real zeros and concise empty sections", async ({
  page,
}) => {
  const view = fixtureView();
  view.jobs = [];
  view.subscriptions = [];
  view.accounts = [];
  view.targets = [];
  view.overview!.music = {
    total: 0,
    active: 0,
    paused: 0,
    attention: 0,
    auto_download: 0,
    members: 0,
    files: 0,
  };
  view.overview!.jobs = {
    total: 0,
    published: 0,
    processing: 0,
    queued: 0,
    attention: 0,
    cancelled: 0,
  };
  await installMediaFixture(page, view);
  await page.goto("/#/media");
  await expect(kpis(page).locator(".overview-kpi-value")).toHaveText([
    "0",
    "0",
    "0",
    "0",
  ]);
  await expect(page.getByText("还没有视频订阅", { exact: true })).toBeVisible();
  await expect(page.getByText("还没有音乐订阅", { exact: true })).toBeVisible();
  await expect(
    page.getByText("近期暂无视频任务", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".overview-ring-segment")).toHaveCount(0);
  await expect(page.locator(".overview-ring-center strong")).toHaveText([
    "0",
    "0",
  ]);
  await expect(page.getByText("隔离 B 站账号", { exact: true })).toHaveCount(0);
});

test("older servers without overview never turn a recent snapshot into totals", async ({
  page,
}) => {
  const view = dashboard();
  delete view.overview;
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media");
  await expect(kpis(page).locator(".overview-kpi-value")).toHaveText([
    "—",
    "—",
    "—",
    "—",
  ]);
  await expect(
    page.getByText("任务总量暂不可用。仍可到下载中心查看已读取的任务。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("暂未取得磁盘容量", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".overview-total")).toHaveCount(0);
  await expect(page.locator(".overview-recent-row")).toHaveCount(5);
  state.view.overview = null;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(kpis(page).locator(".overview-kpi-value")).toHaveText([
    "—",
    "—",
    "—",
    "—",
  ]);
});

test("media storage shows distinct disks and unknown readings without fabricated zeros", async ({
  page,
}, info) => {
  const view = dashboard();
  view.overview!.storage = {
    state: "unavailable",
    volumes: [],
  };
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(
    disk.getByText("暂未取得磁盘容量", { exact: true }),
  ).toBeVisible();
  await expect(disk.getByRole("progressbar")).toHaveCount(0);
  await expect(disk).not.toContainText("0 B");
  state.view.overview!.storage = {
    state: "available",
    volumes: [
      {
        storage_id: "media-one",
        label: "视频存储盘",
        target_ids: ["test-library", "another-library"],
        directories: ["/media/Bilibili", "/media/纪录片/较长目录名"],
        state: "available",
        total_bytes: 1024,
        used_bytes: null,
        free_bytes: 1024,
      },
    ],
  };
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    disk.getByText("暂未取得磁盘容量", { exact: true }),
  ).toBeVisible();
  state.view.overview!.storage = {
    state: "available",
    volumes: [
      {
        ...state.view.overview!.storage.volumes[0],
        total_bytes: 1024 ** 4,
        used_bytes: 1024 ** 3 * 640,
        free_bytes: 1024 ** 3 * 384,
      },
      {
        storage_id: "media-two",
        label: "外接媒体盘",
        target_ids: ["second-library"],
        directories: ["/media/USB/音乐现场"],
        state: "available",
        total_bytes: 1024 ** 4 * 2,
        used_bytes: 1024 ** 4,
        free_bytes: 1024 ** 4,
      },
      {
        storage_id: "media-missing",
        label: "离线媒体盘",
        target_ids: ["offline-library"],
        directories: ["/media/offline"],
        state: "unavailable",
        total_bytes: null,
        used_bytes: null,
        free_bytes: null,
      },
    ],
  };
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(disk.getByText("384 GiB", { exact: true })).toBeVisible();
  await expect(disk.locator(".overview-storage-bar")).toHaveCount(3);
  await expect(disk.locator(".overview-storage-capacity strong")).toHaveText([
    "1 TiB",
    "2 TiB",
  ]);
  await expect(disk).toContainText(
    "媒体目录所在的存储磁盘，容量包含同盘其他文件。同盘目录合并显示。",
  );
  await expect(disk.locator(".overview-storage-volume")).toHaveCount(3);
  await expect(disk).toContainText("/media/Bilibili");
  await expect(disk).toContainText("/media/纪录片/较长目录名");
  const missing = disk.getByRole("region", { name: "离线媒体盘", exact: true });
  await expect(missing).toContainText("暂未取得磁盘容量");
  await expect(missing).not.toContainText("0 B");
  await expect(page.getByText("下载暂存磁盘", { exact: true })).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath(`storage-volumes-${info.project.name}.png`),
    fullPage: true,
  });
});

test("media storage distinguishes no target directory from actual zero free space", async ({
  page,
}) => {
  const view = fixtureView();
  view.overview!.storage = { state: "not_configured", volumes: [] };
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk).toContainText("尚未配置媒体存储目录");
  await expect(disk.getByRole("progressbar")).toHaveCount(0);
  state.view.overview!.storage = fixtureView().overview!.storage;
  state.view.overview!.storage.volumes[0].free_bytes = 0;
  state.view.overview!.storage.volumes[0].used_bytes = 1024 ** 4;
  state.view.overview!.storage.volumes[0].breakdown!.unused_bytes = 0;
  state.view.overview!.storage.volumes[0].breakdown!.other_bytes =
    1024 ** 3 * 640;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(disk.getByText("0 B", { exact: true })).toBeVisible();
  await expect(
    disk.locator(".overview-storage-segment[data-category='unused']"),
  ).toHaveCount(0);
  await expect(disk.locator(".overview-storage-capacity strong")).toHaveText(
    "1 TiB",
  );
});

test("overview read failures show an error then retain an explicitly stale snapshot", async ({
  page,
}) => {
  const state = await installMediaFixture(page, dashboard());
  state.unavailable = true;
  await page.goto("/#/media");
  await expect(
    page.getByText("暂时无法读取视频订阅", { exact: true }),
  ).toBeVisible();
  await expect(kpis(page)).toHaveCount(0);
  state.unavailable = false;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(kpis(page).locator(".overview-kpi-value").first()).toHaveText(
    "930",
  );
  state.unavailable = true;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    page.getByText("下面仍是上次成功读取的快照。连接恢复后会自动更新。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(kpis(page).locator(".overview-kpi-value").first()).toHaveText(
    "930",
  );
  state.unavailable = false;
  state.view.engine = {
    state: "unavailable",
    version: null,
    code: "engine_unavailable",
  };
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    page.getByText("下面仍是上次成功读取的快照。连接恢复后会自动更新。", {
      exact: true,
    }),
  ).toHaveCount(0);
  await page.goto("/#/media/runtime");
  await expect(
    page.getByRole("region", { name: "下载引擎详情" }),
  ).toContainText("下载引擎暂不可用。");
});

test("overview quick links reuse the existing media panels", async ({
  page,
}) => {
  await installMediaFixture(page, dashboard());
  for (const [name, index, heading] of [
    ["视频订阅", 0, "订阅你关注的来源"],
    ["链接下载", 1, "从视频链接开始"],
    ["下载中心", 2, "下载与入库进度"],
    ["账号与媒体库", 3, "扫码登录"],
  ] as const) {
    await page.goto("/#/media");
    await (
      await mediaNavigation(page)
    )
      .getByRole("link", { name, exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`#/subscriptions/${index}$`));
    await expect(
      page.locator(".page-heading, main > .section-nav"),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
  }
});

test("overview stays compact when media is unconfigured", async ({
  page,
}, info) => {
  const view = fixtureView();
  view.configured = false;
  view.overview = null;
  view.accounts = [];
  view.jobs = [];
  view.subscriptions = [];
  view.targets = [];
  await installMediaFixture(page, view);
  await page.goto("/#/media");
  await expect(
    page.getByText("视频订阅服务尚未配置", { exact: true }),
  ).toBeVisible();
  const bounds = await page
    .locator(".media-overview-page > .state-panel")
    .boundingBox();
  expect(bounds!.height).toBeLessThan(260);
  await expect(kpis(page)).toHaveCount(0);
  await page.screenshot({
    path: info.outputPath(`overview-unconfigured-${info.project.name}.png`),
    fullPage: true,
  });
});

test("overview matches the shared light and dark design without a duplicate page banner", async ({
  page,
}, info) => {
  await installMediaFixture(page, dashboard());
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/#/media");
    await expect(
      page.getByRole("region", { name: "订阅概览", exact: true }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await expect(
      page.locator(".page-heading, main > .section-nav"),
    ).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1 })).toHaveClass(
      "sr-only",
    );
    await expect(
      page.getByText("关注视频来源，管理下载与媒体入库", { exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const ring = await page.locator(".overview-ring svg").first().boundingBox();
    expect(ring!.height).toBeGreaterThan(100);
    expect(Math.abs(ring!.width - ring!.height)).toBeLessThan(1);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({
      path: info.outputPath(`overview-${theme}-${info.project.name}.png`),
      fullPage: true,
    });
  }
  await mediaNavigation(page);
  if (info.project.name === "mobile")
    await page.screenshot({
      path: info.outputPath("overview-mobile-navigation.png"),
      fullPage: true,
    });
});

test("three columns keep details on child pages and separate recent media across large queues", async ({
  page,
}, info) => {
  const view = dashboard();
  view.jobs = Array.from({ length: 100 }, (_, i) =>
    fixtureJob({
      job_id: `audio-${i}`,
      media_kind: "audio",
      title: `歌曲 ${i}`,
    }),
  );
  view.overview!.recent = {
    video: [fixtureJob({ title: "最新视频" })],
    audio: [fixtureJob({ title: "最新音乐", media_kind: "audio" })],
  };
  view.overview!.music_storage = structuredClone(view.overview!.storage);
  view.overview!.music_storage.volumes[0].directories = ["/media/Music"];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media");
  await expect(
    page
      .getByRole("region", { name: "音乐订阅状态" })
      .locator(".overview-ring-center strong"),
  ).toHaveText("43");
  await expect(page.locator(".overview-capacity-volume")).toHaveCount(1);
  await expect(page.locator(".overview-storage-directories")).toHaveCount(0);
  await expect(page.getByText("隔离 B 站账号", { exact: true })).toHaveCount(0);
  await expect(page.locator(".overview-subscription-card")).toHaveCount(0);
  const recent = page.getByRole("region", { name: "最近下载", exact: true });
  await expect(recent).toContainText("最新视频");
  await recent.getByRole("button", { name: "音乐", exact: true }).click();
  await expect(recent).toContainText("最新音乐");
  await expect(recent).not.toContainText("最新视频");
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    recent.getByRole("button", { name: "音乐", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  if (info.project.name === "desktop") {
    const left = await page.locator(".overview-system-column").boundingBox();
    const center = await page.locator(".overview-main").boundingBox();
    const right = await page.locator(".overview-side").boundingBox();
    expect(left!.x + left!.width).toBeLessThan(center!.x);
    expect(center!.x + center!.width).toBeLessThan(right!.x);
  }
  for (const [url, label] of [
    ["6", "运行状态详情"],
    ["7", "存储空间详情"],
  ]) {
    await page.goto(`/#/subscriptions/${url}`);
    await page.reload();
    await expect(
      page.getByRole("region", { name: label, exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "返回概览", exact: true }).click();
    await expect(page).toHaveURL(/#\/subscriptions$/);
    await page.goBack();
    await expect(
      page.getByRole("region", { name: label, exact: true }),
    ).toBeVisible();
  }
  await expect(page.getByLabel("媒体目录")).toContainText(
    ["/media/Bilibili", "/media/Music"].join(""),
  );
  await page.goto("/#/media");
  await expect(
    page.getByRole("region", { name: "网络连接延迟" }),
  ).toBeVisible();
  state.unavailable = true;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "网络连接延迟" }),
  ).toContainText("连接中断");
});
