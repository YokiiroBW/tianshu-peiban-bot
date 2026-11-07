import { expect, test } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

const mediaView = () => ({
  configured: true,
  capabilities: { provider: "bilibili", source_kinds: [] },
  engine: { state: "not_verified", version: null, code: "" },
  accounts: [],
  targets: [],
  subscriptions: [],
  jobs: [],
  overview: {
    jobs: {
      total: 1,
      published: 0,
      processing: 1,
      queued: 0,
      attention: 0,
      cancelled: 0,
    },
    storage: { state: "not_configured", volumes: [] },
    music: {
      total: 0,
      active: 0,
      paused: 0,
      attention: 0,
      auto_download: 0,
      members: 0,
      files: 236,
      scan: {
        state: "scanning",
        total: 1702,
        indexed: 231,
        unchanged: 0,
        queued: 0,
        failed: 0,
      },
    },
    books: { total: 0, active: 0, paused: 0, attention: 0, files: 2 },
    recent: {
      video: [],
      audio: [
        {
          job_id: "original-scan",
          title: "扫描本地音乐曲库",
          state: "downloading",
          media_kind: "audio",
          updated_at: 1794070000,
        },
      ],
      book: [],
    },
  },
});

test.beforeEach(async ({ page }) => nativeSession(page));

test("zero playlists still show local files, scan progress and the active audio task", async ({
  page,
}) => {
  const view = mediaView();
  await page.route(apiPath("media/view"), (route) => answer(route, view));
  await page.goto("/#/media");
  const music = page.getByRole("region", { name: "音乐订阅状态", exact: true });
  await expect(music.getByText("本地歌曲 236", { exact: true })).toBeVisible();
  await expect(music.getByText("尚未订阅歌单", { exact: true })).toBeVisible();
  await expect(
    music.getByText("曲库扫描 · 正在扫描", { exact: true }),
  ).toBeVisible();
  await expect(music.getByText(/发现 1,702 个文件 · 新登记 231/)).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: "处理中，1 个任务，打开下载中心",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: "在下载中心查看任务：扫描本地音乐曲库",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "书籍订阅状态" })
      .getByText("2", { exact: true }),
  ).toBeVisible();

  // A fresh snapshot updates counts without overriding the reader's chosen tab.
  await page
    .getByRole("group", { name: "最近下载类型" })
    .getByRole("button", { name: "书籍", exact: true })
    .click();
  view.overview.music.files = 246;
  view.overview.music.scan.indexed = 241;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(music.getByText("本地歌曲 246", { exact: true })).toBeVisible();
  await expect(music.getByText(/新登记 241/)).toBeVisible();
  await expect(
    page
      .getByRole("group", { name: "最近下载类型" })
      .getByRole("button", { name: "书籍", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("missing summaries remain unknown and scan scheduling failures remain visible", async ({
  page,
}) => {
  const view = mediaView();
  const { music: _music, ...overview } = view.overview;
  let snapshot: object = { ...view, overview };
  await page.route(apiPath("media/view"), (route) => answer(route, snapshot));
  await page.goto("/#/media");
  const music = page.getByRole("region", { name: "音乐订阅状态", exact: true });
  await expect(music.getByText("本地歌曲 —", { exact: true })).toBeVisible();
  await expect(
    music.getByText("尚未取得曲库扫描状态", { exact: true }),
  ).toBeVisible();
  await expect(music.getByText("尚未订阅歌单", { exact: true })).toHaveCount(0);

  snapshot = {
    ...view,
    overview: {
      ...view.overview,
      music: {
        ...view.overview.music,
        scan: {
          state: "not_scanned",
          schedule: { state: "blocked", code: "subscription_context_required" },
        },
      },
    },
  };
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    music.getByText("曲库扫描 · 尚未扫描", { exact: true }),
  ).toBeVisible();
  await expect(music.getByText(/自动扫描暂时受阻：/)).toBeVisible();
  await expect(music.getByText(/发现 .* 个文件/)).toHaveCount(0);
});
