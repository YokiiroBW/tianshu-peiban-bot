import { test, expect, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  fixtureBreakdown,
  fixtureView,
  installMediaFixture,
} from "./media.fixtures";

const legend = (disk: Locator, category: string) =>
  disk.locator(`.overview-storage-facts > [data-category="${category}"] dd`);

test("storage ready breakdown displays conserved capacities and four proportional colors", async ({
  page,
}, info) => {
  await installMediaFixture(page);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk.locator(".overview-storage-capacity strong")).toHaveText(
    "1 TiB",
  );
  await expect(disk.getByLabel("四类容量").locator("dt")).toHaveText([
    "订阅占用",
    "Emby占用",
    "其他占用",
    "未使用",
  ]);
  await expect(disk.getByLabel("四类容量").locator("dd")).toHaveText([
    "128 GiB",
    "256 GiB",
    "256 GiB",
    "384 GiB",
  ]);
  const bar = disk.locator(".overview-storage-bar");
  await expect(bar).toHaveAttribute(
    "aria-label",
    /订阅占用 128 GiB，Emby占用 256 GiB，其他占用 256 GiB，未使用 384 GiB/,
  );
  const segments = bar.locator(".overview-storage-segment");
  await expect(segments).toHaveCount(4);
  const proportions = await segments.evaluateAll((elements) =>
    elements.map((element) => ({
      width: element.getBoundingClientRect().width,
      color: getComputedStyle(element).backgroundColor,
    })),
  );
  const bounds = await bar.boundingBox();
  for (const [index, ratio] of [0.125, 0.25, 0.25, 0.375].entries())
    expect(proportions[index].width / bounds!.width).toBeCloseTo(ratio, 2);
  expect(new Set(proportions.map((item) => item.color)).size).toBe(4);
  await expect(disk).toContainText("其他占用含文件系统开销与预留空间");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await disk.screenshot({
    path: info.outputPath(`storage-ready-${info.project.name}.png`),
  });
});

test("storage tiny positive allocation is a marked thin line and zeros draw no segment", async ({
  page,
}, info) => {
  const view = fixtureView();
  view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    subscription_bytes: 1,
    emby_bytes: 0,
    other_bytes: 1024 ** 3 * 640 - 1,
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  const tiny = disk.locator(
    '.overview-storage-segment[data-category="subscription"]',
  );
  await expect(tiny).toHaveClass(/is-tiny/);
  const width = (await tiny.boundingBox())!.width;
  expect(width).toBeGreaterThan(0);
  expect(width).toBeLessThanOrEqual(1.05);
  await expect(legend(disk, "subscription")).toHaveText("1 B");
  await expect(legend(disk, "emby")).toHaveText("0 B");
  await expect(
    disk.locator('.overview-storage-segment[data-category="emby"]'),
  ).toHaveCount(0);
  await expect(disk).toContainText("不表示精确比例");
  await disk.screenshot({
    path: info.outputPath(`storage-tiny-${info.project.name}.png`),
  });
});

test("storage scanning displays neutral used space and unknown categories", async ({
  page,
}, info) => {
  const view = fixtureView();
  view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    state: "scanning",
    subscription_bytes: null,
    emby_bytes: null,
    other_bytes: null,
    measured_at: null,
    code: "storage_scanning",
    refreshing: true,
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk.getByLabel("四类容量").locator("dd")).toHaveText([
    "—",
    "—",
    "—",
    "384 GiB",
  ]);
  await expect(disk.locator(".overview-storage-segment")).toHaveCount(2);
  await expect(
    disk.locator('.overview-storage-segment[data-category="pending"]'),
  ).toHaveCount(1);
  await expect(
    disk.locator('.overview-storage-segment[data-category="subscription"]'),
  ).toHaveCount(0);
  await expect(disk.getByRole("img")).toHaveAttribute(
    "aria-label",
    /已用区域待分类统计/,
  );
  await expect(disk).toContainText("正在后台统计分类占用");
  await expect(disk.getByText(/^统计时间：/)).toHaveCount(0);
  await expect(disk).not.toContainText("当前显示上次统计");
  await disk.screenshot({
    path: info.outputPath(`storage-scanning-${info.project.name}.png`),
  });
});

test("storage partial mapping preserves known values without drawing false categories", async ({
  page,
}) => {
  const view = fixtureView();
  view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    state: "partial",
    emby_bytes: null,
    other_bytes: null,
    code: "storage_path_unmapped",
  });
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk.getByLabel("四类容量").locator("dd")).toHaveText([
    "128 GiB",
    "—",
    "—",
    "384 GiB",
  ]);
  await expect(disk).toContainText("部分 Emby 媒体目录尚未映射");
  await expect(disk.locator(".overview-storage-segment")).toHaveCount(2);
  state.view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    state: "scanning",
    subscription_bytes: null,
    emby_bytes: null,
    other_bytes: null,
    measured_at: null,
    code: "storage_scanning",
  });
  await page.getByRole("button", { name: "读取状态", exact: true }).click();
  await expect(legend(disk, "subscription")).toHaveText("—");
  await expect(disk.getByText(/^统计时间：/)).toHaveCount(0);
});

test("storage retains dated ready snapshot during background refresh and labels logical estimates", async ({
  page,
}) => {
  const view = fixtureView();
  view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    basis: "logical",
    code: "storage_logical_estimate",
    refreshing: true,
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(legend(disk, "subscription")).toHaveText("128 GiB");
  await expect(disk.locator(".overview-storage-segment")).toHaveCount(4);
  await expect(disk).toContainText("统计时间：2026/10/9 00:00:00");
  await expect(disk).toContainText("当前显示上次统计的容量");
  await expect(
    disk.getByText("按文件逻辑大小估算，分类占用为近似值。", { exact: true }),
  ).toHaveCount(1);
});

test("storage unavailable readings remain unknown in all four legends", async ({
  page,
}) => {
  const view = fixtureView();
  Object.assign(view.overview!.storage.volumes[0], {
    state: "unavailable",
    total_bytes: null,
    used_bytes: null,
    free_bytes: null,
    breakdown: fixtureBreakdown({
      state: "unavailable",
      subscription_bytes: null,
      emby_bytes: null,
      other_bytes: null,
      unused_bytes: null,
      measured_at: null,
      code: "storage_capacity_unavailable",
    }),
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk.getByLabel("四类容量").locator("dd")).toHaveText([
    "—",
    "—",
    "—",
    "—",
  ]);
  await expect(disk.locator(".overview-storage-segment")).toHaveCount(0);
  await expect(disk.locator(".overview-storage-capacity")).toHaveCount(0);
  await expect(disk).not.toContainText("0 B");
});

test("storage inconsistent ready numbers stay visible without claiming exact proportions", async ({
  page,
}) => {
  const view = fixtureView();
  view.overview!.storage.volumes[0].breakdown = fixtureBreakdown({
    other_bytes: 1024 ** 4,
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(legend(disk, "other")).toHaveText("1 TiB");
  await expect(
    disk.locator('.overview-storage-segment[data-category="pending"]'),
  ).toHaveCount(1);
  await expect(
    disk.locator('.overview-storage-segment[data-category="other"]'),
  ).toHaveCount(0);
  await expect(disk).toContainText("分类统计与磁盘容量不一致");
});

test("storage zero capacity keeps four real zeros and draws no segments", async ({
  page,
}) => {
  const view = fixtureView();
  Object.assign(view.overview!.storage.volumes[0], {
    total_bytes: 0,
    used_bytes: 0,
    free_bytes: 0,
    breakdown: fixtureBreakdown({
      subscription_bytes: 0,
      emby_bytes: 0,
      other_bytes: 0,
      unused_bytes: 0,
    }),
  });
  await installMediaFixture(page, view);
  await page.goto("/#/media/storage");
  const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
  await expect(disk.getByLabel("四类容量").locator("dd")).toHaveText([
    "0 B",
    "0 B",
    "0 B",
    "0 B",
  ]);
  await expect(disk.locator(".overview-storage-capacity strong")).toHaveText(
    "0 B",
  );
  await expect(disk.locator(".overview-storage-segment")).toHaveCount(0);
  await expect(disk).not.toContainText("NaN");
});

test("storage breakdown stays accessible in shared light and dark themes", async ({
  page,
}, info) => {
  await installMediaFixture(page);
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/#/media/storage");
    const disk = page.getByRole("region", { name: "存储磁盘", exact: true });
    await expect(disk.getByLabel("四类容量")).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await disk.screenshot({
      path: info.outputPath(`storage-${theme}-${info.project.name}.png`),
    });
  }
});
