import { test, expect, type Page, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  fixtureSourceDirectories,
  fixtureSubscription,
  fixtureView,
  installMediaFixture,
} from "./media.fixtures";

const pathPreview = (page: Page) => page.locator("#media-subdirectory-preview");
const mode = (page: Page) =>
  page.getByRole("combobox", { name: "整理方式", exact: true });
async function directoryStep(page: Page) {
  await page.getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("button", { name: "下一步", exact: true }).click();
}
async function newSubscription(
  page: Page,
  kind = "favorite",
  label = "合成来源订阅",
) {
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await page.getByLabel("订阅名称").fill(label);
  await page.getByLabel("来源类型").selectOption(kind);
  if (kind !== "watch_later")
    await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
}
const save = (page: Page) =>
  page.getByRole("button", { name: "保存订阅", exact: true });
async function reply(route: Route, directory: string, status = 200) {
  await route
    .fulfill({
      status,
      json:
        status === 200
          ? {
              destination_mode: "source",
              destination_subdirectory: directory,
              destination_directory: `/media/Bilibili/${directory}`,
            }
          : { code: "dependency_unavailable" },
    })
    .catch(() => {}); // A cancelled browser request may already have closed its route.
}

test("source destinations default to the owner's four directory groups and remain searchable", async ({
  page,
}, info) => {
  const state = await installMediaFixture(page);
  await page.goto("/#/media/video");
  for (const kind of [
    "uploader",
    "favorite",
    "collection",
    "series",
    "watch_later",
  ] as const) {
    await newSubscription(page, kind, `分类 ${kind}`);
    if (kind === "watch_later")
      await page.getByLabel("B 站账号").selectOption("test-account");
    await directoryStep(page);
    await expect(mode(page)).toHaveValue("source");
    await expect(page.getByLabel("整理子目录（可选）")).toHaveCount(0);
    await page.getByLabel("目标媒体库").selectOption("test-library");
    await expect(pathPreview(page)).toHaveText(
      `整理路径预览：/media/Bilibili/${fixtureSourceDirectories[kind]}`,
    );
    await expect(
      page.getByText(/自动归入 UP主、收藏夹、合集或稍后再看/),
    ).toBeVisible();
    if (kind === "uploader") {
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: info.outputPath(`source-directory-${info.project.name}.png`),
        fullPage: true,
      });
    }
    await save(page).click();
    await expect(
      page.getByRole("heading", { name: `分类 ${kind}`, exact: true }),
    ).toBeVisible();
  }
  const saves = state.calls.filter(
    (call) => call.operation === "subscriptions/save",
  );
  expect(saves.map((call) => call.body.destination_mode)).toEqual(
    Array(5).fill("source"),
  );
  expect(saves.map((call) => call.body.destination_subdirectory)).toEqual(
    Object.values(fixtureSourceDirectories),
  );
  await page.getByLabel("查找订阅").fill("稍后再看");
  await expect(page.locator(".media-subscriptions > article")).toHaveCount(1);
  await expect(page.locator(".media-subscriptions > article")).toContainText(
    "按来源自动归类",
  );
  await expect(
    page.locator(".media-subscriptions > article"),
  ).not.toContainText("self");
});

test("legacy editing preserves custom directories and source identity when the display name changes", async ({
  page,
}) => {
  const view = fixtureView();
  view.subscriptions = [
    { ...fixtureSubscription(), destination_subdirectory: "旧收藏/学习" },
  ];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("来源类型")).toBeDisabled();
  await expect(
    page.getByLabel("来源链接或 ID", { exact: true }),
  ).toBeDisabled();
  await page.getByLabel("订阅名称").fill("更名后的收藏夹");
  await directoryStep(page);
  await expect(mode(page)).toHaveValue("custom");
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "旧收藏/学习",
  );
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/旧收藏/学习",
  );
  await save(page).click();
  await expect(
    page.getByRole("heading", { name: "更名后的收藏夹", exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!;
  expect(saved.body.destination_mode).toBe("custom");
  expect(saved.body.destination_subdirectory).toBe("旧收藏/学习");
  expect(saved.body.source).toEqual({ kind: "favorite", id: "123456" });
});

test("source editing retains the persisted relative directory across rename mode and target changes", async ({
  page,
}) => {
  const view = fixtureView();
  view.subscriptions = [
    {
      ...fixtureSubscription(),
      destination_mode: "source",
      destination_subdirectory: "收藏夹/原名称 [FID123456]",
    },
  ];
  view.targets.push({
    ...view.targets[0],
    target_id: "second-library",
    label: "第二媒体库",
    directory: "/media/Second",
  });
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("订阅名称").fill("修改显示名");
  await directoryStep(page);
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/收藏夹/原名称 [FID123456]",
  );
  await expect(page.getByText(/修改订阅名称会保留原目录/)).toBeVisible();
  await mode(page).selectOption("custom");
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "收藏夹/原名称 [FID123456]",
  );
  await page.getByLabel("整理子目录（可选）").fill("自定义/新目录");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/自定义/新目录",
  );
  await mode(page).selectOption("source");
  await page.getByLabel("目标媒体库").selectOption("second-library");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Second/收藏夹/原名称 [FID123456]",
  );
  await save(page).click();
  await expect(
    page.getByRole("heading", { name: "修改显示名", exact: true }),
  ).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "subscriptions/save")!.body
      .destination_subdirectory,
  ).toBe("收藏夹/原名称 [FID123456]");
});

test("watch later uses a valid selected account without an ID and editing follows the same account", async ({
  page,
}) => {
  const view = fixtureView();
  view.accounts.push({
    ...view.accounts[0],
    account_id: "another-account",
    label: "第二合成账号",
  });
  view.accounts.push({
    ...view.accounts[0],
    account_id: "expired-account",
    label: "失效合成账号",
    state: "auth_required",
  });
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await newSubscription(page, "watch_later", "稍后再看订阅");
  await expect(page.getByLabel("来源链接或 ID", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "下一步", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByLabel("B 站账号").locator('option[value="expired-account"]'),
  ).toHaveAttribute("disabled", "");
  await page.getByLabel("B 站账号").selectOption("another-account");
  await directoryStep(page);
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/稍后再看",
  );
  await save(page).click();
  await expect(
    page.getByRole("heading", { name: "稍后再看订阅", exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!;
  expect(saved.body.source).toEqual({ kind: "watch_later", id: "self" });
  expect(saved.body.account_id).toBe("another-account");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("B 站账号")).toHaveValue("another-account");
  await expect(page.getByLabel("B 站账号")).toBeDisabled();
  await expect(page.getByLabel("来源类型")).toBeDisabled();
  await expect(
    page.getByText("此订阅跟随该账号的稍后再看，其他账号可新建订阅。", {
      exact: true,
    }),
  ).toBeVisible();
});

test("destination preview cancels old targets and ignores late success and errors after source and account changes", async ({
  page,
}) => {
  const view = fixtureView();
  view.targets.push({
    ...view.targets[0],
    target_id: "second-library",
    label: "第二媒体库",
  });
  const state = await installMediaFixture(page, view);
  const pending: Route[] = [];
  state.destinationHandler = async (route) => {
    pending.push(route);
  };
  await page.goto("/#/media/video");
  await newSubscription(page);
  await directoryStep(page);
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect.poll(() => pending.length).toBe(1);
  await expect(save(page)).toBeDisabled();
  await page.getByLabel("目标媒体库").selectOption("second-library");
  await expect.poll(() => pending.length).toBe(2);
  await reply(pending[1], "收藏夹/第二目标的目录");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/收藏夹/第二目标的目录",
  );
  await reply(pending[0], "陈旧目标目录");
  await expect(pathPreview(page)).not.toContainText("陈旧目标目录");
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByLabel("来源类型").selectOption("watch_later");
  await page.getByLabel("B 站账号").selectOption("test-account");
  await directoryStep(page);
  await expect.poll(() => pending.length).toBe(3);
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByRole("button", { name: "上一步", exact: true }).click();
  await page.getByLabel("来源类型").selectOption("uploader");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("654321");
  await page.getByLabel("B 站账号").selectOption("");
  await directoryStep(page);
  await expect.poll(() => pending.length).toBe(4);
  await reply(pending[3], "UP主/当前来源目录");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/UP主/当前来源目录",
  );
  await reply(pending[2], "", 503);
  await expect(page.getByText(/后台或上游暂不可用/)).toHaveCount(0);
  await expect(save(page)).toBeEnabled();
  const previews = state.calls.filter(
    (call) => call.operation === "subscriptions/destination-preview",
  );
  expect(previews[2].body).toMatchObject({
    source: { kind: "watch_later", id: "self" },
    account_id: "test-account",
    target_id: "second-library",
  });
  expect(previews[3].body).toMatchObject({
    source: { kind: "uploader", id: "654321" },
    account_id: null,
    target_id: "second-library",
  });
});

test("destination preview failures can retry and closing the editor cancels late errors", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  const pending: Route[] = [];
  state.destinationHandler = async (route) => {
    pending.push(route);
  };
  await page.goto("/#/media/video");
  await newSubscription(page);
  await directoryStep(page);
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect.poll(() => pending.length).toBe(1);
  await reply(pending[0], "", 503);
  await expect(
    page.getByRole("button", { name: "重新预览目录", exact: true }),
  ).toBeVisible();
  await expect(save(page)).toBeDisabled();
  await page.getByRole("button", { name: "重新预览目录", exact: true }).click();
  await expect.poll(() => pending.length).toBe(2);
  await reply(pending[1], "收藏夹/重试成功的目录");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/收藏夹/重试成功的目录",
  );
  await expect(page.getByText(/后台或上游暂不可用/)).toHaveCount(0);
  await mode(page).selectOption("custom");
  await expect.poll(() => pending.length).toBe(3);
  await page.getByRole("button", { name: "关闭编辑", exact: true }).click();
  await reply(pending[2], "", 503);
  await expect(
    page.getByRole("region", { name: "新建订阅", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/后台或上游暂不可用/)).toHaveCount(0);
  expect(
    state.calls.some((call) => call.operation === "subscriptions/save"),
  ).toBe(false);
});

test("AssetLibrary does not claim automatic destination support and custom input survives target switches", async ({
  page,
}) => {
  const view = fixtureView();
  view.targets.push({
    ...view.targets[0],
    target_id: "legacy-assets",
    label: "资产库目标",
    kind: "assetlibrary",
    directory: null,
    destination_subdirectory_supported: false,
  });
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await newSubscription(page);
  await directoryStep(page);
  await page.getByLabel("目标媒体库").selectOption("legacy-assets");
  await expect(
    page.getByText(/无法按来源归类。请选择自定义目录并留空/),
  ).toBeVisible();
  await expect(save(page)).toBeDisabled();
  expect(
    state.calls.some(
      (call) => call.operation === "subscriptions/destination-preview",
    ),
  ).toBe(false);
  await mode(page).selectOption("custom");
  await expect(pathPreview(page)).toHaveText("整理路径预览：由资产库管理");
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await page.getByLabel("整理子目录（可选）").fill("自定义/保持输入");
  await expect(pathPreview(page)).toHaveText(
    "整理路径预览：/media/Bilibili/自定义/保持输入",
  );
  await page.getByLabel("目标媒体库").selectOption("legacy-assets");
  await expect(page.getByLabel("整理子目录（可选）")).toHaveValue(
    "自定义/保持输入",
  );
  await expect(save(page)).toBeDisabled();
  await page.getByLabel("整理子目录（可选）").fill("");
  await expect(pathPreview(page)).toHaveText("整理路径预览：由资产库管理");
  await save(page).click();
  await expect(
    page.getByRole("heading", { name: "合成来源订阅", exact: true }),
  ).toBeVisible();
  expect(
    state.calls.find((call) => call.operation === "subscriptions/save")!.body,
  ).toMatchObject({
    destination_mode: "custom",
    destination_subdirectory: "",
    target_id: "legacy-assets",
  });
});
