import { test, expect, type Page } from "@playwright/test";
import { fixtureJob, fixtureView, installMediaFixture } from "./media.fixtures";
import type {
  MusicView,
  MusicPlaylist,
} from "../src/features/media/music/types";

test("music lyrics progress distinguishes synchronized translations and deferred retries", async ({
  page,
}) => {
  const state = await musicFixture(page);
  state.view.lyrics = {
    tracked: 12,
    synced: 8,
    translated: 3,
    counts: { queued: 2, retry_wait: 2 },
  };
  await page.goto("/#/media/music");
  const panel = page.getByRole("region", { name: "歌词自动补齐" });
  await expect(panel).toContainText("8 首滚动歌词");
  await expect(panel).toContainText("3 首同步翻译");
  await expect(panel).toContainText("2 首等待重试");
  await expect(panel).toContainText("不重新下载音频");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

async function musicFixture(page: Page, connected = true) {
  await installMediaFixture(page);
  const account = {
    account_id: "netease:123",
    provider: "netease" as const,
    user_id: "123",
    label: "合成网易云账号",
    state: "ready" as const,
    revision: 1,
    checked_at: 1792000000,
  };
  const source = {
    source_id: "900",
    name: "夜晚的收藏",
    description: "合成歌单资料",
    cover_url: null,
    track_count: 4,
    kind: "created" as const,
  };
  const state = {
    qrState: "waiting",
    failScan: false,
    calls: [] as { operation: string; body: Record<string, unknown> }[],
    view: {
      configured: true,
      audio_download_available: true,
      providers: [
        {
          provider: "netease",
          label: "网易云音乐",
          available: true,
          login_types: ["qr"],
        },
        {
          provider: "qq",
          label: "QQ 音乐",
          available: true,
          login_types: ["qq", "wx"],
        },
        {
          provider: "kugou",
          label: "酷狗音乐",
          available: true,
          login_types: ["qr"],
        },
      ],
      accounts: connected ? [account] : [],
      playlists: [],
      navidrome: { connection: null, playlists: [] },
      settings: {
        revision: 0,
        directory: "",
        layout: "artist_album",
        filename: "artist_title",
      },
    } as MusicView,
  };
  await page.route("**/api/v1/media/music/**", async (route) => {
    const operation = new URL(route.request().url()).pathname.split(
      "/music/",
    )[1];
    const body = route.request().postDataJSON() as Record<string, unknown>;
    state.calls.push({ operation, body });
    const reply = (value: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(value),
      });
    if (operation === "view") return reply(state.view);
    if (operation === "maintenance/save") {
      const settings = state.view.maintenance!.settings;
      if (body.expected_revision !== settings.revision)
        return reply({ code: "version_conflict" }, 409);
      settings.enabled = Boolean(body.enabled);
      settings.playlist_interval_seconds = Number(
        body.playlist_interval_seconds,
      );
      settings.revision += 1;
      return reply({
        settings,
        playlists_updated: state.view.playlists.length,
      });
    }
    if (operation === "navidrome/test")
      return reply({
        version: "synthetic",
        folders: [{ id: "1", name: "NAS 音乐库" }],
        can_scan: true,
      });
    if (operation === "navidrome/save") {
      const nav = state.view.navidrome!;
      nav.connection = {
        connection_id: "synthetic",
        revision: (nav.connection?.revision ?? 0) + 1,
        enabled: true,
        base_url: String(body.base_url),
        username: String(body.username),
        music_folder_id: String(body.music_folder_id),
        music_directory: String(body.music_directory),
        local_directory: "/synthetic/music",
        version: "synthetic",
        can_scan: true,
        folders: [{ id: "1", name: "NAS 音乐库" }],
        checked_at: 1792000000,
      };
      return reply({ connection: nav.connection });
    }
    if (operation === "navidrome/playlists/save") {
      const nav = state.view.navidrome!;
      const previous = nav.playlists[0];
      nav.playlists = [
        {
          link_id: "nav-link",
          playlist_id: String(body.playlist_id),
          revision: (previous?.revision ?? 0) + 1,
          name: String(body.name),
          remote_id: "remote-playlist",
          public: Boolean(body.public),
          auto_sync: Boolean(body.auto_sync),
          status: "partial",
          code: "partial",
          synced_count: 3,
          total: 4,
          problem_count: 1,
          problems: [
            { position: 4, title: "尚未入库的歌曲", code: "waiting_index" },
          ],
        },
      ];
      return reply({ playlist: nav.playlists[0] });
    }
    if (operation === "navidrome/playlists/control") {
      const nav = state.view.navidrome!;
      const linked = nav.playlists[0];
      expect(body.expected_revision).toBe(linked.revision);
      linked.revision++;
      if (body.action === "pause") {
        linked.auto_sync = false;
        linked.status = "paused";
      }
      if (body.action === "resume") {
        linked.auto_sync = true;
        linked.status = "partial";
      }
      if (body.action === "sync") {
        linked.status = "synced";
        linked.synced_count = 4;
        linked.problem_count = 0;
        linked.problems = [];
      }
      linked.code = linked.status;
      if (body.action === "delete") nav.playlists = [];
      return reply({ playlist: linked });
    }
    if (operation === "navidrome/disable") {
      state.view.navidrome!.connection!.enabled = false;
      state.view.navidrome!.connection!.revision++;
      return reply({ connection: state.view.navidrome!.connection });
    }
    if (operation === "library/scan")
      return reply({ job_id: "music-scan-fixture", state: "queued" });
    if (operation === "library/view")
      return reply({
        offset: 0,
        more: false,
        counts: { music_recording: 1, music_source: 3, music_library_file: 1 },
        items: [
          {
            recording_id: "one",
            metadata: {
              title: "跨平台合成歌曲",
              artists: ["合成艺术家"],
              album: "合成专辑",
            },
            file: {
              download_provider: "netease",
              quality: {
                level: "lossless",
                codec: "flac",
                sample_rate: 44100,
                bits_per_sample: 16,
                channels: 2,
                bit_rate: 900000,
                fallback: false,
              },
              files: [{ kind: "audio", path: "合成艺术家/合成专辑/共用.flac" }],
            },
          },
        ],
      });
    if (operation === "accounts/qr/start")
      return reply({
        qr_id: "synthetic-qr",
        url: "https://music.163.com/login?codekey=synthetic-key",
        expires_at: Date.now() / 1000 + 180,
      });
    if (operation === "accounts/qr/poll") {
      if (state.qrState === "ready") state.view.accounts = [account];
      return reply({
        state: state.qrState,
        account: state.qrState === "ready" ? account : null,
      });
    }
    if (operation === "accounts/revoke") {
      state.view.accounts[0] = { ...account, state: "revoked", revision: 2 };
      return reply({ account: state.view.accounts[0] });
    }
    if (operation === "playlists/list") return reply({ playlists: [source] });
    if (operation === "playlists/subscribe") {
      const playlist = {
        ...source,
        playlist_id: "netease:123:playlist:900",
        account_id: "netease:123",
        revision: 1,
        member_count: 4,
        interval_seconds: Number(body.interval_seconds),
        state: "active",
        synced_at: 1792000000,
        code: "ready",
      } as MusicPlaylist;
      state.view.playlists.push(playlist);
      return reply({ playlist });
    }
    if (operation === "playlists/control") {
      const playlist = state.view.playlists[0];
      expect(body.expected_revision).toBe(playlist.revision);
      if (body.action === "remove") {
        state.view.playlists = [];
        return reply({ removed: true });
      }
      playlist.state = body.action === "pause" ? "paused" : "active";
      playlist.revision++;
      return reply({ playlist });
    }
    if (operation === "playlists/downloads") {
      const playlist = state.view.playlists[0];
      expect(body.expected_revision).toBe(playlist.revision);
      playlist.quality = body.quality as MusicPlaylist["quality"];
      playlist.auto_download = Boolean(body.auto_download);
      playlist.revision++;
      return reply({ playlist });
    }
    if (operation === "playlists/download") {
      expect(body.expected_revision).toBe(state.view.playlists[0].revision);
      return reply({ queued: 3, unavailable: 1 });
    }
    if (operation === "playlists/export")
      return reply({
        directory: "/synthetic/music/歌单/example",
        exported: 3,
        total: 4,
      });
    if (operation === "playlists/scan") {
      if (state.failScan) return reply({ code: "music_scan_incomplete" }, 502);
      return reply({ playlist: state.view.playlists[0] });
    }
    if (operation === "playlists/detail")
      return reply({
        playlist: state.view.playlists[0],
        offset: 0,
        more: false,
        tracks: ["清晨", "夜色", "清晨", ""].map((title, index) => ({
          source_id: String(100 + index),
          position: index + 1,
          title,
          artists: ["合成艺术家"],
          album: "合成专辑",
          availability: title ? "known" : "unavailable",
          audio_state: "not_downloaded",
        })),
      });
    if (operation === "settings/inspect")
      return reply({
        state: "readable",
        samples: [
          {
            artist: "合成艺术家",
            album: "合成专辑",
            files: ["合成艺术家 - 清晨.flac", "合成艺术家 - 清晨.lrc"],
          },
        ],
      });
    if (operation === "settings/save") {
      state.view.settings = {
        directory: String(body.directory),
        layout: body.layout as "artist_album",
        filename: "artist_title",
        revision: 1,
      };
      return reply({ settings: state.view.settings });
    }
    return reply({ code: "not_found" }, 404);
  });
  return state;
}

async function openCard(page: Page, name: string) {
  if (!(await page.getByRole("dialog", { name, exact: true }).isVisible()))
    await page.getByRole("button", { name, exact: true }).click();
}

async function subscribe(page: Page) {
  await openCard(page, "添加音乐订阅");
  await page.getByRole("button", { name: "读取账号歌单", exact: true }).click();
  await page.getByRole("button", { name: "订阅歌单", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "已订阅", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page
      .getByRole("region", { name: "本地歌单", exact: true })
      .getByText("夜晚的收藏", { exact: true }),
  ).toBeVisible();
}

test("delete music subscription closes its detail and permits resubscription", async ({
  page,
}) => {
  const state = await musicFixture(page);
  await page.goto("/#/media/music");
  await subscribe(page);
  await page.getByRole("button", { name: "查看歌曲", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "歌单歌曲", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "删除订阅", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "删除订阅", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "歌单歌曲", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "订阅已删除" }),
  ).toBeVisible();
  expect(state.view.accounts).toHaveLength(1);
  await subscribe(page);
  expect(state.view.playlists).toHaveLength(1);
});

test("Navidrome connection creates and controls one shared-file playlist", async ({
  page,
}, info) => {
  const state = await musicFixture(page);
  await page.goto("/#/media/music");
  await openCard(page, "连接 Navidrome");
  await page
    .getByLabel("Navidrome 地址", { exact: true })
    .fill("http://127.0.0.1:4533");
  await page.getByLabel("Navidrome 用户名", { exact: true }).fill("synthetic");
  await page
    .getByLabel("Navidrome 密码", { exact: true })
    .fill("synthetic-password");
  await page
    .getByLabel("Navidrome 中的音乐存档路径", { exact: true })
    .fill("/music/音乐存档");
  await page
    .getByRole("button", { name: "测试连接并读取音乐库", exact: true })
    .click();
  await expect(
    page.getByText("连接成功 · synthetic", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "保存 Navidrome 连接", exact: true })
    .click();
  const connection = page.getByRole("region", {
    name: "Navidrome 连接",
    exact: true,
  });
  await expect(connection).toContainText("已连接 · synthetic");
  await connection.getByText("编辑连接", { exact: true }).click();
  await expect(page.getByLabel("Navidrome 密码", { exact: true })).toHaveValue(
    "",
  );
  await page.keyboard.press("Escape");
  await subscribe(page);
  const playlist = page.getByRole("region", {
    name: "夜晚的收藏的 Navidrome 歌单",
    exact: true,
  });
  await playlist.getByText("创建 Navidrome 歌单", { exact: true }).click();
  await playlist
    .getByLabel("Navidrome 歌单名称", { exact: true })
    .fill("本地夜晚");
  await playlist
    .getByRole("button", { name: "创建并同步歌单", exact: true })
    .click();
  await expect(playlist).toContainText("3/4 首");
  await playlist.getByText("查看待补齐歌曲（1 首）", { exact: true }).click();
  await expect(playlist).toContainText("等待 Navidrome 入库");
  state.view.navidrome!.playlists[0].problems[0].code = "verification_pending";
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "刷新音乐状态", exact: true }).click();
  await playlist
    .getByRole("button", { name: "查看待补齐歌曲（1 首）", exact: true })
    .click();
  await expect(playlist).toContainText("暂时无法复核，保留上次确认的歌曲");
  expect(state.view.navidrome!.playlists[0].public).toBe(false);
  await page.keyboard.press("Escape");
  await playlist
    .getByRole("button", { name: "暂停 Navidrome 自动同步", exact: true })
    .click();
  await expect(
    playlist.getByRole("button", {
      name: "开启 Navidrome 自动同步",
      exact: true,
    }),
  ).toBeVisible();
  await playlist
    .getByRole("button", { name: "同步至 Navidrome", exact: true })
    .click();
  await expect(playlist).toContainText("4/4 首");
  await playlist.getByText("Navidrome 歌单设置", { exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath(`navidrome-${info.project.name}.png`),
    fullPage: true,
  });
  await playlist
    .getByRole("button", { name: "删除 Navidrome 歌单", exact: true })
    .click();
  await expect(
    playlist.getByText("创建 Navidrome 歌单", { exact: true }),
  ).toBeVisible();
  expect(state.view.playlists).toHaveLength(1);
  expect(state.calls.some((c) => c.operation === "playlists/download")).toBe(
    false,
  );
});

test("music entry offers account login and audio archiving", async ({
  page,
}) => {
  const state = await musicFixture(page, false);
  await page.goto("/#/media/music");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("音乐订阅");
  await expect(
    page.getByText("尚未连接音乐账号。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/整理标签、封面和歌词/)).toBeVisible();
  await openCard(page, "添加音乐订阅");
  await expect(
    page.getByRole("button", { name: "读取账号歌单", exact: true }),
  ).toBeDisabled();
  expect(
    state.calls.some((call) => call.operation.startsWith("accounts/qr")),
  ).toBe(false);
});

test("runtime details expose recovering download workers", async ({ page }) => {
  const view = fixtureView();
  view.background = [
    { name: "worker_0", state: "running", restarts: 0, code: null },
    {
      name: "worker_1",
      state: "recovering",
      restarts: 1,
      code: "media_background_restarting",
    },
  ];
  await installMediaFixture(page, view);
  await page.goto("/#/media/runtime");
  const panel = page.getByRole("region", { name: "下载引擎详情" });
  await expect(panel).toContainText("后台执行：1/2 项运行");
  await expect(panel).toContainText("部分后台任务正在恢复");
});

test("music unified providers and library use automatic routing without priority settings", async ({
  page,
}, info) => {
  const state = await musicFixture(page, false);
  await page.goto("/#/media/music");
  await openCard(page, "连接音乐账号");
  await page
    .getByRole("combobox", { name: "音乐平台", exact: true })
    .selectOption("qq");
  await page
    .getByRole("combobox", { name: "登录方式", exact: true })
    .selectOption("wx");
  await page
    .getByRole("button", { name: "QQ 音乐扫码登录", exact: true })
    .click();
  await expect(page.getByAltText("QQ 音乐登录二维码")).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "accounts/qr/start")?.body,
  ).toEqual({ provider: "qq", login_type: "wx" });
  await openCard(page, "连接音乐账号");
  await page
    .getByRole("combobox", { name: "音乐平台", exact: true })
    .selectOption("kugou");
  await expect(page.getByAltText("QQ 音乐登录二维码")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "酷狗音乐扫码登录", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "查看统一曲库", exact: true }).click();
  await expect(
    page.getByText("1 首歌曲 · 3 个来源 · 1 份已登记音频", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("合成艺术家/合成专辑/共用.flac", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "扫描音乐存档", exact: true }).click();
  expect(state.calls.some((call) => call.operation === "library/scan")).toBe(
    true,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("music-unified.png"),
    fullPage: true,
  });
});

test("music download policy, manual queue and ordered exports", async ({
  page,
}, info) => {
  const state = await musicFixture(page);
  state.view.settings.directory = "/synthetic/music";
  await page.goto("/#/media/music");
  await subscribe(page);
  await page.getByText("下载设置", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "期望音质", exact: true })
    .selectOption("hires");
  await page.getByLabel("允许使用当前账号可下载的较低音质").uncheck();
  await page.getByLabel("同步后自动下载新增歌曲", { exact: true }).check();
  await page.getByRole("button", { name: "保存下载设置", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("下载设置已保存，歌单已加入队列。", { exact: true }),
  ).toBeVisible();
  expect(state.view.playlists[0].quality).toEqual({
    level: "hires",
    allow_fallback: false,
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "下载歌单", exact: true }).click();
  await expect(page.getByText(/已加入下载队列 3 首/)).toBeVisible();
  await page
    .getByRole("button", { name: "导出 M3U8 / JSON", exact: true })
    .click();
  await expect(page.getByText(/已导出 3\/4 首/)).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("music-download-settings.png"),
    fullPage: true,
  });
});

test("mixed download list renders audio evidence without video editing controls", async ({
  page,
}, info) => {
  const view = fixtureView();
  const audio = fixtureJob({
    job_id: "audio-one",
    media_kind: "audio",
    provider: "netease",
    source_id: "101",
    title: "合成歌曲",
    creator: "合成艺术家",
    state: "completed",
    stage: "completed",
    code: "music_archived",
    target_id: "music-library",
    actual_quality: "lossless",
    destination_directory: "/synthetic/music",
    can_cancel: false,
    can_retry: false,
    can_delete: true,
    audio_quality: {
      level: "lossless",
      codec: "flac",
      sample_rate: 44100,
      channels: 2,
      bits_per_sample: 16,
      bit_rate: 900000,
      fallback: false,
    },
    audio_receipt: {
      files: [{ path: "合成艺术家/合成专辑/合成歌曲.flac", kind: "audio" }],
      warnings: ["music_lyrics_unavailable"],
    },
    audio_reused: true,
    audio_routing: {
      policy: "highest_available",
      providers: [
        {
          provider: "kugou",
          matches: 1,
          available_levels: ["observed"],
          failures: [],
        },
        {
          provider: "netease",
          matches: 1,
          available_levels: ["lossless"],
          failures: [],
        },
        {
          provider: "qq",
          matches: 1,
          available_levels: ["standard"],
          failures: [],
        },
      ],
      selected: {
        provider: "netease",
        quality: {
          level: "lossless",
          codec: "flac",
          sample_rate: 44100,
          channels: 2,
          bits_per_sample: 16,
          bit_rate: 900000,
          fallback: false,
        },
        reused: true,
      },
    },
    audio_unlock: {
      state: "decoded",
      format: "ncm",
      tool: "unlock-music/cli",
      version: "v0.2.12",
    },
    audio_attempts: [
      {
        provider: "kugou",
        source_id: "synthetic",
        code: "music_decode_key_required",
        unlock: { state: "key_required", format: "kgm" },
      },
    ],
  });
  // Real audio API documents have no BVID or selected CIDs.
  Object.assign(audio, { bvid: null, selected_cids: null, cid: null });
  view.jobs.unshift(audio);
  const state = await installMediaFixture(page, view);
  state.detailHandler = async (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ job: audio, events: [], metadata: null }),
    });
  await page.goto("/#/media/jobs");
  await expect(page.getByText("合成歌曲", { exact: true })).toBeVisible();
  await expect(
    page.getByText("网易云音乐 101", { exact: false }),
  ).toBeVisible();
  await page
    .locator('[data-job-id="audio-one"]')
    .getByRole("button", { name: "查看任务详情", exact: true })
    .click();
  const detail = page.getByRole("complementary", {
    name: "任务详情",
    exact: true,
  });
  await expect(
    detail.getByRole("heading", { name: "音乐归档", exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("已自动解锁 · NCM", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("聚合比较：已检查 3 个账号，优先最高可用音质。", {
      exact: true,
    }),
  ).toBeVisible();
  await detail.getByText("各平台查询结果", { exact: true }).click();
  await expect(
    detail.getByText("QQ 音乐：标准", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await detail.getByText("其他来源的处理结果", { exact: true }).click();
  await expect(
    detail.getByText(/酷狗音乐：此加密版本需要官方客户端的文件密钥/),
  ).toBeVisible();
  await expect(
    detail.getByText("合成艺术家/合成专辑/合成歌曲.flac", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("歌词未取得，音频已保留。", { exact: true }),
  ).toBeVisible();
  await expect(detail.getByText("媒体服务器核对", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    detail.getByRole("button", { name: "重新解析来源", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  Object.assign(audio.audio_routing!, {
    complete: false,
    deferred: [
      { provider: "kugou", code: "music_request_budget", retry_at: 1900000000 },
    ],
  });
  Object.assign(audio.audio_routing!.providers[0], {
    code: "music_request_budget",
    retry_at: 1900000000,
  });
  await page.reload();
  await page
    .locator('[data-job-id="audio-one"]')
    .getByRole("button", { name: "查看任务详情", exact: true })
    .click();
  await expect(
    detail.getByText("聚合比较：已检查 2 个账号，优先最高可用音质。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    detail.getByText(
      "部分渠道待补查，当前选择可用渠道中的最高音质；补查后仅替换更高音质版本。",
      { exact: true },
    ),
  ).toBeVisible();
  await detail.getByText("各平台查询结果", { exact: true }).click();
  await expect(
    detail.getByText("酷狗音乐：暂不可查询，待补查", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("music-job.png"),
    fullPage: true,
  });
});

test("NetEase QR shows waiting then connected and survives reload", async ({
  page,
}) => {
  const state = await musicFixture(page, false);
  await page.goto("/#/media/music");
  await openCard(page, "连接音乐账号");
  await page
    .getByRole("button", { name: "网易云音乐扫码登录", exact: true })
    .click();
  await expect(page.getByAltText("网易云音乐登录二维码")).toBeVisible();
  await expect(page.getByText("等待扫码", { exact: true })).toBeVisible();
  state.qrState = "ready";
  await expect(
    page
      .getByRole("region", { name: "音乐账号", exact: true })
      .getByText("合成网易云账号", { exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(
    page
      .getByRole("region", { name: "音乐账号", exact: true })
      .getByText("合成网易云账号", { exact: true }),
  ).toBeVisible();
});

test("expired NetEase QR can be regenerated", async ({ page }) => {
  const state = await musicFixture(page, false);
  state.qrState = "expired";
  await page.goto("/#/media/music");
  await openCard(page, "连接音乐账号");
  await page
    .getByRole("button", { name: "网易云音乐扫码登录", exact: true })
    .click();
  await expect(page.getByText("二维码已过期", { exact: true })).toBeVisible();
  await expect(page.getByAltText("网易云音乐登录二维码")).toHaveCount(0);
  state.qrState = "waiting";
  await page
    .getByRole("button", { name: "重新生成二维码", exact: true })
    .click();
  await expect(page.getByAltText("网易云音乐登录二维码")).toBeVisible();
});

test("playlist selection keeps source order and supports pause and resume", async ({
  page,
}, info) => {
  const state = await musicFixture(page);
  await page.goto("/#/media/music");
  await openCard(page, "添加音乐订阅");
  await page
    .getByRole("combobox", { name: "同步周期", exact: true })
    .selectOption("21600");
  await subscribe(page);
  expect(
    state.calls.find((call) => call.operation === "playlists/subscribe")?.body
      .interval_seconds,
  ).toBe(21600);
  await page.getByRole("button", { name: "查看歌曲", exact: true }).click();
  const table = page.getByRole("region", { name: "歌单歌曲", exact: true });
  await expect(
    table.getByRole("cell", { name: "清晨", exact: true }),
  ).toHaveCount(2);
  await expect(
    table.getByRole("cell", { name: /暂不可用的歌曲/ }),
  ).toBeVisible();
  await expect(
    table.getByRole("cell", { name: "未下载", exact: true }),
  ).toHaveCount(4);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "暂停同步", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "立即同步", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "恢复同步", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "立即同步", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath(`music-${info.project.name}.png`),
    fullPage: true,
  });
});

test("music directory settings inspect a sample and preserve configured layout", async ({
  page,
}) => {
  const state = await musicFixture(page);
  await page.goto("/#/media/music");
  await openCard(page, "音乐存档设置");
  await page.getByLabel("音乐目录", { exact: true }).fill("/synthetic/music");
  await page.getByRole("button", { name: "查看目录结构", exact: true }).click();
  await expect(
    page.getByText("目录可读取，以下为抽样结构。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存存储设置", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("音乐目录和整理方式已保存。", { exact: true }),
  ).toBeVisible();
  expect(state.view.settings.directory).toBe("/synthetic/music");
  await page.reload();
  await openCard(page, "音乐存档设置");
  await expect(page.getByLabel("音乐目录", { exact: true })).toHaveValue(
    "/synthetic/music",
  );
});

test("incomplete sync and account disconnect retain the local playlist", async ({
  page,
}) => {
  const state = await musicFixture(page);
  await page.goto("/#/media/music");
  await subscribe(page);
  state.failScan = true;
  await page.getByRole("button", { name: "立即同步", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("已保留上次同步结果");
  await page.getByRole("button", { name: "断开账号", exact: true }).click();
  await page.getByRole("button", { name: "查看歌曲", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "歌单歌曲", exact: true })
      .getByRole("cell", { name: "清晨", exact: true }),
  ).toHaveCount(2);
});

test("music maintenance keeps 12 hour sync separate from weekly checks and exposes shared cooldown", async ({
  page,
}) => {
  const state = await musicFixture(page);
  state.view.maintenance = {
    settings: {
      enabled: true,
      interval_seconds: 604800,
      playlist_interval_seconds: 43200,
      revision: 1,
    },
    counts: { tracked: 100, checked: 80, due: 20, upgrades: 5 },
    requests: {
      interval_seconds: 3,
      hourly_limit: 600,
      daily_limit: 3000,
      scopes: [
        {
          provider: "netease",
          account_id: null,
          hour_used: 100,
          day_used: 300,
          cooldown_until: Date.now() / 1000 + 1800,
          auth_required: false,
          code: "music_rate_limited",
        },
      ],
    },
    recent: [
      {
        recording_id: "song-1",
        title: "合成升级歌曲",
        outcome: "upgraded",
        checked_at: 1792079887,
      },
    ],
  };
  await page.goto("/#/media/music");
  const panel = page.getByRole("region", { name: "音乐自动维护" });
  await expect(panel).toContainText("每 7 天");
  await openCard(page, "音乐自动维护设置");
  await expect(page.getByLabel("歌单自动同步周期")).toHaveValue("43200");
  await page.keyboard.press("Escape");
  await openCard(page, "添加音乐订阅");
  await expect(
    page.getByRole("combobox", { name: "同步周期", exact: true }),
  ).toHaveValue("43200");
  await page.keyboard.press("Escape");
  await panel.getByText("请求节流与最近检查", { exact: true }).click();
  await expect(panel).toContainText("冷却至");
  await expect(panel).toContainText("合成升级歌曲");
  await page.keyboard.press("Escape");
  await openCard(page, "音乐自动维护设置");
  await panel.getByRole("checkbox").uncheck();
  await panel.getByRole("button", { name: "保存维护设置" }).click();
  await expect(panel).toContainText("已暂停");
  const saved = state.calls.find(
    (call) => call.operation === "maintenance/save",
  )!;
  expect(saved.body).toMatchObject({
    enabled: false,
    playlist_interval_seconds: 43200,
    expected_revision: 1,
  });
  expect(saved.body.client_request_id).toMatch(/^[0-9a-f-]{36}$/);
  await page.reload();
  await openCard(page, "音乐自动维护设置");
  await expect(panel.getByRole("checkbox")).not.toBeChecked();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
