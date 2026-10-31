import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";

test("isolated four-tone fixture preserves rail selection and explicit unknown health", async ({
  page,
}, testInfo) => {
  const css = ["../src/design/tokens.css", "../src/app/app.css"]
    .map((path) => readFileSync(new URL(path, import.meta.url), "utf8"))
    .join("\n");
  const cases = [
    ["blue", "运行中 · 健康未知"],
    ["yellow", "正在启动 · 待核对"],
    ["red", "运行中 · 健康异常"],
    ["gray", "状态未同步"],
  ] as const;
  const rows = cases
    .map(
      ([tone, label]) =>
        `<div class="status-rail tone-${tone}"><span class="rail-label">${label}</span></div>`,
    )
    .join("");
  await page.setContent(
    `<html lang="zh-CN"><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>隔离细轨测试夹具</title><style>${css}</style></head><body><main class="panel"><h1>隔离展示夹具 · 非设备数据</h1>${rows}</main></body></html>`,
  );
  for (const theme of ["light", "dark"]) {
    await page.locator("html").evaluate((element, value) => {
      element.dataset.theme = value;
    }, theme);
    for (const [tone, label] of cases) {
      const rail = page.locator(`.tone-${tone}`);
      await expect(rail).toHaveText(label);
      const before = await rail.evaluate(
        (element) => getComputedStyle(element).borderInlineStartColor,
      );
      await rail.evaluate((element) => {
        (element as HTMLElement).dataset.selected = "true";
      });
      expect(
        await rail.evaluate(
          (element) => getComputedStyle(element).borderInlineStartColor,
        ),
      ).toBe(before);
      expect(
        await rail.evaluate(
          (element) => getComputedStyle(element).borderInlineStartStyle,
        ),
      ).toBe(tone === "yellow" ? "dashed" : "solid");
    }
    expect(
      (await new AxeBuilder({ page }).withTags(["wcag2aa"]).analyze())
        .violations,
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`rails-${theme}.png`),
      fullPage: true,
    });
  }
});
