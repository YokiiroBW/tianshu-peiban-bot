import { test, expect } from "@playwright/test";
import {
  fixtureJob,
  fixturePreview,
  fixtureSubscription,
  fixtureView,
  installMediaFixture,
  selectMediaSection,
  expectMediaSection,
} from "./media.fixtures";

test("media link parsing preserves selected parts and only available quality", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await page
    .getByLabel("B 站视频链接")
    .fill("https://www.bilibili.com/video/BV1xx411c7mD");
  await page.getByRole("button", { name: "解析链接", exact: true }).click();
  await expect(
    page.getByText("隔离样例 · 城市与山海", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect(
    page
      .getByLabel("质量策略")
      .getByRole("option", { name: "1080P", exact: true }),
  ).toHaveCount(0);
  await page.getByText("P2 · 山海", { exact: true }).click();
  await page.getByLabel("质量策略").selectOption("80");
  await page.getByRole("button", { name: "创建下载任务（1 个分 P）" }).click();
  await expectMediaSection(page, "下载中心");
  const enqueue = state.calls.find((call) => call.operation === "enqueue")!;
  expect(enqueue.body.part_cids).toEqual(["100"]);
  expect(enqueue.body.quality).toEqual({
    mode: "exact",
    quality_id: "80",
    allow_fallback: false,
  });
  expect(enqueue.body.client_request_id).toMatch(/^[\da-f-]{36}$/);
  expect(enqueue.body).not.toHaveProperty("path");
});

test("media subscription wizard supports four sources and explains rule errors", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "视频订阅");
  const sourceUrls: Record<string, string> = {
    favorite: "https://space.bilibili.com/123456/favlist?fid=7890",
    collection: "https://space.bilibili.com/123456/lists/7890?type=season",
    series: "https://space.bilibili.com/123456/lists/7890?type=series",
    uploader: "https://space.bilibili.com/123456/video",
  };
  for (const kind of ["favorite", "collection", "series", "uploader"]) {
    await page.getByRole("button", { name: "新建订阅", exact: true }).click();
    await page.getByLabel("订阅名称").fill(`合成订阅 ${kind}`);
    await page.getByLabel("来源类型").selectOption(kind);
    await page
      .getByLabel("来源链接或 ID", { exact: true })
      .fill(sourceUrls[kind]);
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    if (kind === "favorite") {
      await page.getByRole("button", { name: "添加黑名单组" }).click();
      await page.getByLabel("匹配值", { exact: true }).fill("广告");
      await page
        .getByLabel("试算视频链接")
        .fill("https://www.bilibili.com/video/BV1xx411c7mD");
      state.ruleError = true;
      await page.getByRole("button", { name: "试算规则", exact: true }).click();
      await expect(page.getByText("正则执行超时，请修正规则")).toBeVisible();
      await expect(
        page.getByRole("button", { name: "下一步", exact: true }),
      ).toBeDisabled();
      state.ruleError = false;
      await page.getByLabel("匹配值", { exact: true }).fill("合成广告");
      await page.getByRole("button", { name: "试算规则", exact: true }).click();
      await expect(page.getByText("符合规则，可以下载")).toBeVisible();
    }
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.getByLabel("目标媒体库").selectOption("test-library");
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: `合成订阅 ${kind}`, exact: true }),
    ).toBeVisible();
  }
  expect(
    state.calls
      .filter((call) => call.operation === "subscriptions/save")
      .map((call) => (call.body.source as { kind: string }).kind),
  ).toEqual(["favorite", "collection", "series", "uploader"]);
  expect(
    state.calls
      .filter((call) => call.operation === "subscriptions/save")
      .map((call) => (call.body.source as { id: string }).id),
  ).toEqual(Object.values(sourceUrls));
  expect(
    state.view.subscriptions.every(
      (subscription) => !subscription.baseline_ready,
    ),
  ).toBe(true);
});

test("media subscription edit keeps scan interval and pause does not cancel jobs", async ({
  page,
}) => {
  const view = fixtureView();
  view.subscriptions = [fixtureSubscription()];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "视频订阅");
  await page.getByRole("button", { name: "暂停订阅" }).click();
  await expect(page.getByRole("button", { name: "恢复订阅" })).toBeVisible();
  expect(state.calls.some((call) => call.operation === "jobs/control")).toBe(
    false,
  );
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("扫描间隔（分钟）")).toHaveValue("30");
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已有收藏夹订阅", exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!;
  expect(saved.body.expected_revision).toBe(3);
  expect(saved.body.interval_seconds).toBe(1800);
  expect(saved.body.destination_subdirectory).toBe("");
  await page.getByRole("button", { name: "立即扫描", exact: true }).click();
  await expect(page.getByText("完整 · 3 个成员")).toBeVisible();
});

test("media subscriptions keep independent subdirectories under one target", async ({
  page,
}, info) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/video");
  await expectMediaSection(page, "视频订阅");
  for (const entry of [
    {
      kind: "favorite",
      label: "学习收藏夹",
      input: " 收藏夹\\学习 ",
      directory: "收藏夹/学习",
    },
    {
      kind: "uploader",
      label: "关注的 UP 主",
      input: "UP主/合成作者",
      directory: "UP主/合成作者",
    },
  ]) {
    await page.getByRole("button", { name: "新建订阅", exact: true }).click();
    await page.getByLabel("订阅名称").fill(entry.label);
    await page.getByLabel("来源类型").selectOption(entry.kind);
    await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.getByLabel("目标媒体库").selectOption("test-library");
    await page
      .getByRole("combobox", { name: "整理方式", exact: true })
      .selectOption("custom");
    await expect(page.locator("#media-subdirectory-preview")).toHaveText(
      "整理路径预览：/media/Bilibili",
    );
    await page.getByLabel("整理子目录（可选）").fill(entry.input);
    await expect(page.locator("#media-subdirectory-preview")).toHaveText(
      `整理路径预览：/media/Bilibili/${entry.directory}`,
    );
    await expect(
      page.getByText(/已有下载满足视频、分\s*P、画质和高级策略时会优先复用/),
    ).toBeVisible();
    if (entry.kind === "favorite")
      await page.screenshot({
        path: info.outputPath(`subdirectory-preview-${info.project.name}.png`),
        fullPage: true,
      });
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    const card = page.locator(".media-subscriptions > article").filter({
      has: page.getByRole("heading", { name: entry.label, exact: true }),
    });
    await expect(
      card.getByText(`/media/Bilibili/${entry.directory}`, { exact: true }),
    ).toBeVisible();
  }
  const saves = state.calls.filter(
    (call) => call.operation === "subscriptions/save",
  );
  expect(saves.map((call) => call.body.target_id)).toEqual([
    "test-library",
    "test-library",
  ]);
  expect(saves.map((call) => call.body.destination_subdirectory)).toEqual([
    "收藏夹/学习",
    "UP主/合成作者",
  ]);
  await page.getByLabel("查找订阅").fill("收藏夹/学习");
  await expect(page.locator(".media-subscriptions > article")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: "学习收藏夹", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
});

test("media subscription directory edits round trip and can return to the root", async ({
  page,
}, info) => {
  const view = fixtureView();
  view.subscriptions = [
    { ...fixtureSubscription(), destination_subdirectory: "收藏夹/原目录" },
  ];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await expectMediaSection(page, "视频订阅");
  const nextDirectory = `收藏夹/${"较长的学习资料目录".repeat(8)}`;
  for (const directory of [nextDirectory, ""]) {
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
      directory ? "收藏夹/原目录" : nextDirectory,
    );
    await expect(
      page.getByText(/修改目录后，下次扫描会将现有成员整理到新目录/),
    ).toBeVisible();
    await page.getByLabel("整理子目录（可选）").fill(directory);
    await expect(page.locator("#media-subdirectory-preview")).toHaveText(
      `整理路径预览：/media/Bilibili${directory ? `/${directory}` : ""}`,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    if (directory)
      await page.screenshot({
        path: info.outputPath(`subdirectory-long-${info.project.name}.png`),
        fullPage: true,
      });
    await page.getByRole("button", { name: "保存订阅", exact: true }).click();
    await expect(
      page
        .locator(".media-subscriptions > article")
        .getByText(`/media/Bilibili${directory ? `/${directory}` : ""}`, {
          exact: true,
        }),
    ).toBeVisible();
  }
  const saves = state.calls.filter(
    (call) => call.operation === "subscriptions/save",
  );
  expect(saves.map((call) => call.body.destination_subdirectory)).toEqual([
    nextDirectory,
    "",
  ]);
  expect(saves.map((call) => call.body.expected_revision)).toEqual([2, 3]);
  expect(
    saves.every(
      (call) => call.body.subscription_id === "existing-subscription",
    ),
  ).toBe(true);
});

test("media subdirectory errors keep the editor input for correction", async ({
  page,
}) => {
  await installMediaFixture(page);
  let rejected = false;
  await page.route("**/api/v1/media/subscriptions/save", async (route) => {
    if (rejected) return route.fallback();
    rejected = true;
    await route.fulfill({
      status: 400,
      json: { code: "invalid_destination_subdirectory" },
    });
  });
  await page.goto("/#/media/video");
  await expectMediaSection(page, "视频订阅");
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await page.getByLabel("订阅名称").fill("待修正目录的收藏夹");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await page
    .getByRole("combobox", { name: "整理方式", exact: true })
    .selectOption("custom");
  for (const invalid of ["/绝对目录", "../越界目录"]) {
    await page.getByLabel("整理子目录（可选）").fill(invalid);
    await expect(page.getByLabel("整理子目录（可选）")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(
      page.getByRole("button", { name: "保存订阅", exact: true }),
    ).toBeDisabled();
    await expect(page.locator("#media-subdirectory-preview")).toHaveText(
      "整理路径预览：请修正子目录",
    );
  }
  await page.getByLabel("整理子目录（可选）").fill("CON");
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText(/请检查整理子目录：使用相对目录/),
  ).toBeVisible();
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue("CON");
  await page.getByLabel("整理子目录（可选）").fill("收藏夹/已修正");
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "待修正目录的收藏夹", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("整理子目录（可选）")).toHaveCount(0);
});

test("media AssetLibrary targets explain managed directories and retain input when switching", async ({
  page,
}) => {
  const view = fixtureView();
  view.targets[0].kind = "media_server";
  view.targets.push({
    target_id: "legacy-assets",
    label: "历史资产库目标",
    kind: "assetlibrary",
    directory: null,
    state: "ready",
    servers: [],
  });
  const state = await installMediaFixture(page, view);
  let rejected = false;
  await page.route("**/api/v1/media/subscriptions/save", async (route) => {
    if (rejected) return route.fallback();
    rejected = true;
    // The server can reject a target capability that changed after the view snapshot.
    await route.fulfill({
      status: 409,
      json: { code: "destination_not_supported" },
    });
  });
  await page.goto("/#/media/video");
  await expectMediaSection(page, "视频订阅");
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await page.getByLabel("订阅名称").fill("历史资产库订阅");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await page
    .getByRole("combobox", { name: "整理方式", exact: true })
    .selectOption("custom");
  await page.getByLabel("整理子目录（可选）").fill("收藏夹/学习");
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText(
        "这个目标的目录由资产库管理，整理子目录请留空，或选择媒体服务器目标。（destination_not_supported）",
        { exact: true },
      ),
  ).toBeVisible();
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "收藏夹/学习",
  );
  await page.getByLabel("目标媒体库").selectOption("legacy-assets");
  await expect(page.locator("#media-subdirectory-help")).toHaveText(
    "此目标的目录由资产库管理，整理子目录请留空。切换目标会保留当前输入。",
  );
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "收藏夹/学习",
  );
  await expect(
    page.getByRole("button", { name: "保存订阅", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect(page.locator("#media-subdirectory-preview")).toHaveText(
    "整理路径预览：/media/Bilibili/收藏夹/学习",
  );
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "收藏夹/学习",
  );
  await page.getByLabel("目标媒体库").selectOption("legacy-assets");
  await page.getByLabel("整理子目录（可选）").fill("");
  await expect(page.locator("#media-subdirectory-preview")).toHaveText(
    "整理路径预览：由资产库管理",
  );
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "历史资产库订阅", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".media-subscriptions > article")
      .getByText("由资产库管理", { exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!;
  expect(saved.body.target_id).toBe("legacy-assets");
  expect(saved.body.destination_subdirectory).toBe("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
});

test("media jobs display their fixed directory and only reported download reuse", async ({
  page,
}) => {
  const view = fixtureView();
  view.subscriptions = [
    { ...fixtureSubscription(), destination_subdirectory: "收藏夹/新目录" },
  ];
  view.jobs = [
    fixtureJob({
      destination_subdirectory: "收藏夹/旧目录",
      destination_directory: "/media/Bilibili/收藏夹/旧目录",
      download_reused_cids: ["100"],
      subscription_id: "existing-subscription",
    }),
    fixtureJob({ job_id: "legacy-job", title: "旧投影未提供路径" }),
  ];
  await installMediaFixture(page, view);
  await page.goto("/#/media/jobs");
  await expectMediaSection(page, "下载中心");
  const card = page.locator('[data-job-id="media-job-one"]');
  await expect(
    card.getByText("/media/Bilibili/收藏夹/旧目录", { exact: true }),
  ).toBeVisible();
  await expect(
    card.getByText("已复用 1 个分 P", { exact: true }),
  ).toBeVisible();
  const legacy = page.locator('[data-job-id="legacy-job"]');
  await expect(
    legacy.getByText("目标根目录（完整路径未提供）", { exact: true }),
  ).toBeVisible();
  await expect(legacy.getByText(/已复用/)).toHaveCount(0);
  await card.getByRole("button", { name: "查看任务详情", exact: true }).click();
  const detail = page.getByRole("complementary", {
    name: "任务详情",
    exact: true,
  });
  await expect(
    detail.getByText("/media/Bilibili/收藏夹/旧目录", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("已复用 1 个分 P", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("/media/Bilibili/收藏夹/新目录", { exact: true }),
  ).toHaveCount(0);
});

test("media QR is generated locally and imported cookie leaves the editor", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "账号与媒体库");
  await page
    .getByRole("button", { name: "添加 B 站账号", exact: true })
    .click();
  await page.getByRole("button", { name: "生成登录二维码" }).click();
  await expect(page.getByAltText("B 站登录二维码")).toHaveAttribute(
    "src",
    /^data:image\/gif;base64,/,
  );
  await page.getByText("使用本人会话恢复登录", { exact: true }).click();
  await page
    .getByLabel("本人 B 站 Cookie")
    .fill("SESSDATA=synthetic-only-cookie");
  await page.getByRole("button", { name: "校验并导入会话" }).click();
  await expect(page.getByLabel("本人 B 站 Cookie")).toHaveValue("");
  await expect(
    page.getByRole("heading", { name: "我的 B 站账号", exact: true }),
  ).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "accounts/import")!.body
      .cookie,
  ).toBe("SESSDATA=synthetic-only-cookie");
  expect(
    await page.evaluate(() => Object.values(localStorage).join("")),
  ).not.toContain("synthetic-only-cookie");
});

test("media scanned QR state keeps its minimum polling interval", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.clock.install();
  let reads = 0;
  await page.route("**/api/v1/media/accounts/qr/poll", async (route) => {
    reads += 1;
    if (reads === 3)
      state.view.accounts.push({
        account_id: "qr-ready",
        label: "扫码完成账号",
        state: "ready",
        revision: 1,
        checked_at: 1791475200,
        code: "",
      });
    await route.fulfill({
      json: {
        state: reads === 1 ? "waiting" : reads === 2 ? "scanned" : "ready",
        account: reads === 3 ? state.view.accounts.at(-1) : null,
      },
    });
  });
  await page.goto("/#/media/download");
  await selectMediaSection(page, "账号与媒体库");
  await page
    .getByRole("button", { name: "添加 B 站账号", exact: true })
    .click();
  await page
    .getByRole("button", { name: "生成登录二维码", exact: true })
    .click();
  await expect(page.getByText("等待扫码", { exact: true })).toBeVisible();
  await page.clock.runFor(5000);
  await expect(
    page.getByText("已扫码，请在手机确认", { exact: true }),
  ).toBeVisible();
  expect(reads).toBe(2);
  await page.clock.runFor(4900);
  expect(reads).toBe(2);
  await page.clock.runFor(200);
  await expect(
    page.getByRole("heading", { name: "扫码完成账号", exact: true }),
  ).toBeVisible();
  expect(reads).toBe(3);
});

test("media job details preserve metadata revision and separate server results", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await page.getByRole("button", { name: "查看任务详情" }).click();
  await page.getByRole("button", { name: "重新解析来源", exact: true }).click();
  await expect(
    page.getByText("来源已重新解析，请查看最新元数据和封面状态。"),
  ).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "jobs/refresh_metadata")!.body
      .expected_revision,
  ).toBe(3);
  await page.getByRole("button", { name: "编辑下载资料", exact: true }).click();
  await page.getByLabel("展示标题").fill("人工展示标题");
  await page.getByLabel("展示简介").fill("人工补充的简介");
  await page.getByRole("button", { name: "保存元数据" }).click();
  await expect(
    page.getByText(
      "元数据已保存，任务将从对应阶段继续。源视频的原始资料会保留。",
    ),
  ).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "jobs/metadata")!.body
      .expected_revision,
  ).toBe(4);
  const current = state.view.jobs[0];
  current.state = "library_verifying";
  current.stage = "library_verifying";
  current.asset_receipt = {
    state: "published",
    library_id: "synthetic-assets",
  };
  current.library_results = [
    {
      server_id: "test-jellyfin",
      kind: "jellyfin",
      state: "verified",
      code: "",
      item_id: "synthetic-item",
    },
    {
      server_id: "test-emby",
      kind: "emby",
      state: "unavailable",
      code: "connection_failed",
      item_id: null,
    },
  ];
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "重新读取详情" }).click();
  await expect(page.getByText("已核对", { exact: true })).toBeVisible();
  await expect(page.getByText("暂不可用", { exact: true })).toBeVisible();
  if (test.info().project.name === "mobile") {
    await page.getByRole("button", { name: "返回任务列表" }).click();
    await expect(
      page.getByRole("button", { name: "查看任务详情" }),
    ).toBeFocused();
  } else {
    await page.getByRole("button", { name: "返回任务列表" }).click();
  }
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(page.getByText("已取消", { exact: true }).first()).toBeVisible();
});

test("media switching accounts rejects late video details", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  let release: (() => void) | undefined;
  let arrived: (() => void) | undefined;
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  state.resolveHandler = async (route) => {
    const waiting = new Promise<void>((resolve) => {
      release = resolve;
    });
    arrived?.();
    await waiting;
    await route
      .fulfill({
        contentType: "application/json",
        body: JSON.stringify(fixturePreview()),
      })
      .catch(() => undefined);
  };
  await page.goto("/#/media/download");
  await page
    .getByLabel("B 站视频链接")
    .fill("https://www.bilibili.com/video/BV1xx411c7mD");
  await page.getByRole("button", { name: "解析链接", exact: true }).click();
  await started;
  await page.getByLabel("B 站账号").selectOption("test-account");
  await page.getByLabel("B 站视频链接").fill("");
  release?.();
  await expect(
    page.getByText("粘贴链接，查看视频", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "隔离样例 · 城市与山海", exact: true }),
  ).toHaveCount(0);
});

test("media outages preserve a marked snapshot and session loss clears it", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await expect(
    page.getByRole("heading", { name: "隔离样例 · 城市与山海", exact: true }),
  ).toBeVisible();
  state.unavailable = true;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(
    page.getByText("下面仍是上次成功读取的快照。连接恢复后会自动更新。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "隔离样例 · 城市与山海", exact: true }),
  ).toBeVisible();
  state.unavailable = false;
  state.authenticated = false;
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(page).toHaveURL(/#\/login/);
  await expect(
    page.getByRole("heading", { name: "隔离样例 · 城市与山海", exact: true }),
  ).toHaveCount(0);
});

test("media viewport and keyboard keep details reachable", async ({
  page,
}, info) => {
  const state = await installMediaFixture(page);
  state.view.jobs = [
    fixtureJob({
      state: "failed",
      stage: "downloading",
      code: "engine_unavailable",
      can_retry: true,
    }),
  ];
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心", true);
  await expect(page).toHaveURL(/#\/subscriptions\/2$/);
  await page.getByRole("button", { name: "查看任务详情" }).click();
  const detail = page.getByRole("complementary", { name: "任务详情" });
  await expect(detail.getByText("1080P", { exact: true })).toBeVisible();
  await expect(
    detail.getByText("下载引擎暂不可用。", { exact: true }),
  ).toBeVisible();
  await expect(
    detail.getByText("原因码：engine_unavailable", { exact: true }),
  ).toBeHidden();
  await expect(
    page.getByRole("complementary", { name: "任务详情" }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath(`media-${info.project.name}.png`),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "查看任务详情" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "重试任务" }).click();
  await expect(
    page.getByText("等待下载", { exact: true }).first(),
  ).toBeVisible();
});

test("media unconfigured state never shows synthetic work", async ({
  page,
}) => {
  const view = fixtureView();
  view.configured = false;
  view.accounts = [];
  view.targets = [];
  view.jobs = [];
  await installMediaFixture(page, view);
  await page.goto("/#/media/download");
  await expect(
    page.getByText("视频订阅服务尚未配置", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("article.media-job")).toHaveCount(0);
});

test("media metadata conflict keeps unsaved text and refuses an old revision", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await page.getByRole("button", { name: "查看任务详情" }).click();
  await page.getByRole("button", { name: "编辑下载资料", exact: true }).click();
  await page.getByLabel("展示标题").fill("本页尚未保存的标题");
  state.view.jobs[0].revision += 1;
  await page.getByRole("button", { name: "保存元数据" }).click();
  await expect(
    page.getByRole("dialog").getByText(/记录版本已改变/),
  ).toBeVisible();
  await expect(page.getByLabel("展示标题")).toHaveValue("本页尚未保存的标题");
  expect(state.view.jobs[0].title).toBe("隔离样例 · 城市与山海");
});

test("media LAN HTTP UUID fallback creates a command without secure-context APIs", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(crypto, "randomUUID", { value: undefined });
    Object.defineProperty(crypto, "subtle", { value: undefined });
  });
  const state = await installMediaFixture(page);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "视频订阅");
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await page.getByLabel("订阅名称").fill("LAN HTTP 合成订阅");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "添加白名单组" }).click();
  await page.getByLabel("匹配值", { exact: true }).fill("😀".repeat(4096));
  await expect(page.getByLabel("匹配值", { exact: true })).toHaveValue(
    "😀".repeat(4096),
  );
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "LAN HTTP 合成订阅", exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!;
  expect(saved.body.client_request_id).toMatch(/^[\da-f-]{36}$/);
});

test("media progress uses the owners normalized ratio", async ({ page }) => {
  const view = fixtureView();
  view.jobs = [
    fixtureJob({
      state: "downloading",
      stage: "downloading",
      code: "",
      progress: 0.5,
    }),
  ];
  await installMediaFixture(page, view);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await expect(page.getByText("50%", { exact: true })).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveAttribute("value", "50");
});

test("media retries an unconfirmed command with the original idempotency key", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  const keys: string[] = [];
  await page.route("**/api/v1/media/enqueue", async (route) => {
    keys.push(route.request().postDataJSON().client_request_id);
    if (keys.length === 1) return route.abort("failed");
    return route.fulfill({
      json: { jobs: [fixtureJob({ state: "queued" })], replayed: true },
    });
  });
  await page.goto("/#/media/download");
  await page
    .getByLabel("B 站视频链接")
    .fill("https://www.bilibili.com/video/BV1xx411c7mD");
  await page.getByRole("button", { name: "解析链接", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "创建下载任务（2 个分 P）" }),
  ).toBeVisible();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await page.getByRole("button", { name: "创建下载任务（2 个分 P）" }).click();
  await expect(page.getByText(/连接中断，结果尚未确认/)).toBeVisible();
  await page.getByRole("button", { name: "创建下载任务（2 个分 P）" }).click();
  await expectMediaSection(page, "下载中心");
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(
    state.calls.filter((call) => call.operation === "resolve"),
  ).toHaveLength(1);
});

test("media task history follows the server cursor beyond the initial view", async ({
  page,
}) => {
  const view = fixtureView();
  view.jobs = Array.from({ length: 105 }, (_, index) =>
    fixtureJob({
      job_id: `history-${index}`,
      title: `历史视频 ${index}`,
      state: index % 2 ? "completed" : "failed",
      created_at: 1791475200 - index,
      can_cancel: false,
    }),
  );
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await expect(page.locator("article.media-job")).toHaveCount(20);
  for (const count of [40, 60, 80, 100, 105]) {
    await page.getByRole("button", { name: /加载更多任务/ }).click();
    await expect(page.locator("article.media-job")).toHaveCount(count);
  }
  await expect(
    page.getByRole("heading", { name: "历史视频 104", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /加载更多任务/ })).toHaveCount(
    0,
  );
  await page.getByLabel("筛选任务").selectOption("finished");
  await expect(page.locator("article.media-job")).toHaveCount(20);
  const calls = state.calls.filter((call) => call.operation === "jobs/list");
  expect(calls.slice(0, 6).map((call) => call.body.cursor)).toEqual([
    null,
    "20",
    "40",
    "60",
    "80",
    "100",
  ]);
  expect(calls.at(-1)!.body).toEqual({
    states: ["completed", "published"],
    cursor: null,
    page_size: 20,
  });
  await expect(
    page.getByRole("heading", { name: "历史视频 0", exact: true }),
  ).toHaveCount(0);
});

test("media filter changes discard a delayed continuation", async ({
  page,
}) => {
  const view = fixtureView();
  view.jobs = Array.from({ length: 21 }, (_, index) =>
    fixtureJob({
      job_id: `late-${index}`,
      title: `待处理视频 ${index}`,
      state: "failed",
    }),
  );
  const state = await installMediaFixture(page, view);
  let release: () => void = () => undefined;
  let arrived: () => void = () => undefined;
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  state.listHandler = async (route) => {
    const body = route.request().postDataJSON();
    if (body.cursor) {
      const waiting = new Promise<void>((resolve) => {
        release = resolve;
      });
      arrived();
      await waiting;
      await route
        .fulfill({
          json: {
            jobs: [
              fixtureJob({
                job_id: "late-old-result",
                title: "旧筛选的迟到视频",
              }),
            ],
            page: { next_cursor: null, has_more: false },
          },
        })
        .catch(() => undefined);
    } else {
      await route.fulfill({
        json: {
          jobs: body.states ? [] : view.jobs.slice(0, 20),
          page: {
            next_cursor: body.states ? null : "20",
            has_more: !body.states,
          },
        },
      });
    }
  };
  await page.goto("/#/media/download");
  await selectMediaSection(page, "下载中心");
  await page.getByRole("button", { name: /加载更多任务/ }).click();
  await started;
  await page.getByLabel("筛选任务").selectOption("finished");
  release();
  await expect(
    page.getByText("这个筛选下没有任务", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "旧筛选的迟到视频", exact: true }),
  ).toHaveCount(0);
});

test("external media servers can be added and selected without AssetLibrary", async ({
  page,
}) => {
  const view = fixtureView();
  view.targets = [];
  view.jobs = [];
  view.subscriptions = [];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/accounts");
  await expect(
    page.getByText(
      "尚未添加媒体服务器。添加后即可选择订阅和下载的目标媒体库。",
    ),
  ).toBeVisible();
  await expect(page.getByText("天枢媒体库", { exact: true })).toHaveCount(0);
  await page
    .getByRole("button", { name: "添加媒体服务器", exact: true })
    .click();
  await page.getByLabel("显示名称", { exact: true }).fill("我的 Plex");
  await page.getByLabel("服务器类型").selectOption("plex");
  await page
    .getByLabel("服务器地址", { exact: true })
    .fill("http://127.0.0.1:32400");
  await page.getByLabel("API Key / Plex Token").fill("synthetic-token");
  await page.getByRole("button", { name: "测试连接并读取媒体库" }).click();
  await page
    .getByRole("combobox", { name: "服务器媒体库", exact: true })
    .selectOption("1");
  await expect(page.getByLabel("对应目录（媒体服务器可见）")).toHaveValue(
    "/library/videos",
  );
  await page.getByLabel("整理目录（天枢可见）").fill("/media/videos");
  await page.getByRole("button", { name: "保存媒体服务器" }).click();
  await expect(
    page.getByRole("heading", { name: "我的 Plex", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("API Key / Plex Token")).toHaveCount(0);
  expect(
    state.calls.find((call) => call.operation === "targets/save")?.body.kind,
  ).toBe("plex");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await selectMediaSection(page, "链接下载");
  await expect(
    page.getByLabel("目标媒体库").getByRole("option", { name: /我的 Plex/ }),
  ).toHaveCount(1);
});
