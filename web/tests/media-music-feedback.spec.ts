import { expect, test, type Page } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";
import type { MusicView } from "../src/features/media/music/types";

const coolingMessage = "平台请求正在冷却，请稍后重试；本地曲库仍可使用。";

async function musicPage(page: Page) {
  await nativeSession(page);
  await page.route(apiPath("media/view"), (route) =>
    answer(route, {
      configured: true,
      accounts: [],
      targets: [],
      subscriptions: [],
      jobs: [],
    }),
  );
  const view: MusicView = {
    configured: true,
    audio_download_available: true,
    accounts: [
      {
        account_id: "qq:author",
        provider: "qq",
        user_id: "author",
        label: "QQ 音乐测试账号",
        state: "ready",
        revision: 1,
        checked_at: 1,
      },
      {
        account_id: "netease:author",
        provider: "netease",
        user_id: "author",
        label: "网易云测试账号",
        state: "ready",
        revision: 1,
        checked_at: 1,
      },
    ],
    playlists: [],
    settings: {
      directory: "/data/media/music",
      layout: "artist_album",
      filename: "artist_title",
      revision: 2,
    },
    maintenance: {
      settings: {
        enabled: true,
        interval_seconds: 604800,
        playlist_interval_seconds: 43200,
        revision: 1,
      },
      counts: { tracked: 0, checked: 0, due: 0, upgrades: 0 },
      requests: {
        interval_seconds: 3,
        hourly_limit: 600,
        daily_limit: 3000,
        scopes: [],
      },
      recent: [],
    },
  };
  await page.route(apiPath("music/view"), (route) => answer(route, view));
  await page.goto("/#/media/music");
  await page.getByRole("button", { name: "添加音乐订阅", exact: true }).click();
  return view;
}

async function cooling(route: import("@playwright/test").Route) {
  await route.fulfill({
    status: 429,
    json: { error: { code: "music_account_cooling", message: "cooling" } },
  });
}

test("platform cooling stays in playlist context and local settings remain usable", async ({
  page,
}) => {
  const view = await musicPage(page);
  let playlistReads = 0;
  await page.route(apiPath("music/playlists/list"), async (route) => {
    playlistReads += 1;
    view.maintenance!.requests.scopes = [
      {
        provider: "qq",
        account_id: null,
        hour_used: 1,
        day_used: 1,
        cooldown_until: Math.floor(Date.now() / 1000) + 1800,
        auth_required: false,
        code: "music_rate_limited",
      },
    ];
    await cooling(route);
  });
  await page.route(apiPath("music/settings/inspect"), (route) =>
    answer(route, { state: "readable", samples: [] }),
  );
  await page.route(apiPath("music/settings/save"), (route) =>
    answer(
      route,
      command(route.request().postDataJSON(), { settings: view.settings }),
    ),
  );
  const subscription = page.getByRole("dialog", {
    name: "添加音乐订阅",
    exact: true,
  });
  await subscription.getByRole("button", { name: "读取账号歌单" }).click();
  await expect(page.getByRole("alert")).toHaveCount(1);
  await expect(page.getByRole("alert")).toContainText(coolingMessage);
  await expect(subscription.getByText(/QQ 音乐请求冷却至/)).toBeVisible();
  await expect(subscription.locator(".media-message")).toHaveCount(0);
  await subscription.getByRole("button", { name: "关闭添加音乐订阅" }).click();
  await page.getByRole("button", { name: "音乐存档设置", exact: true }).click();
  const storage = page.getByRole("dialog", {
    name: "音乐存档设置",
    exact: true,
  });
  await expect(storage.getByLabel("音乐目录")).toHaveValue("/data/media/music");
  await expect(page.getByRole("alert")).toHaveCount(0);
  const inspected = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/music/settings/inspect",
  );
  await storage.getByRole("button", { name: "查看目录结构" }).click();
  expect((await inspected).ok()).toBe(true);
  await storage.getByRole("button", { name: "保存存储设置" }).click();
  await expect(
    page
      .locator(".notification-card")
      .filter({ hasText: "音乐目录和整理方式已保存。" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(playlistReads).toBe(1);
  await storage.getByRole("button", { name: "关闭音乐存档设置" }).click();
  await page.getByRole("button", { name: "添加音乐订阅", exact: true }).click();
  await expect(
    page
      .locator(".notification-card")
      .filter({ hasText: "音乐目录和整理方式已保存。" }),
  ).toHaveCount(0);
  await subscription.getByLabel("音乐账号").selectOption("netease:author");
  await expect(subscription.getByText(/请求冷却至/)).toHaveCount(0);
});

test("a successful retry clears the previous playlist error", async ({
  page,
}) => {
  await musicPage(page);
  let reads = 0;
  await page.route(apiPath("music/playlists/list"), async (route) => {
    reads += 1;
    if (reads === 1) await cooling(route);
    else await answer(route, { playlists: [] });
  });
  const read = page.getByRole("button", { name: "读取账号歌单" });
  await read.click();
  await expect(page.getByRole("alert")).toContainText(coolingMessage);
  await expect(read).toBeEnabled();
  await read.click();
  await expect(
    page.getByText("此账号暂未返回可访问的歌单。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(reads).toBe(2);
});

test("switching accounts ignores a late result from the previous account", async ({
  page,
}) => {
  await musicPage(page);
  let finishOld: (() => void) | undefined;
  const oldResponse = new Promise<void>((resolve) => {
    finishOld = resolve;
  });
  await page.route(apiPath("music/playlists/list"), async (route) => {
    const body = route.request().postDataJSON();
    if (body.account_id === "qq:author") {
      await oldResponse;
      await cooling(route);
    } else await answer(route, { playlists: [] });
  });
  const oldRequest = page.waitForRequest(
    (request) =>
      new URL(request.url()).pathname === "/api/v1/music/playlists/list",
  );
  await page.getByRole("button", { name: "读取账号歌单" }).click();
  await oldRequest;
  const subscription = page.getByRole("dialog", {
    name: "添加音乐订阅",
    exact: true,
  });
  await subscription.getByLabel("音乐账号").selectOption("netease:author");
  finishOld!();
  await subscription.getByRole("button", { name: "读取账号歌单" }).click();
  await expect(
    subscription.getByText("此账号暂未返回可访问的歌单。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
