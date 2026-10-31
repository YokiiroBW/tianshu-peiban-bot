import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  approach,
  automaticRoom,
  daylight,
  localHour,
  resolveRoom,
} from "../src/features/room/environment";

async function openRoom(page: Page) {
  await page.goto("/#/roles/room");
  await expect(page.locator("canvas")).toHaveAttribute("data-frames", /[1-9]/);
  await expect(page.locator(".room-fallback")).toHaveCount(0);
}
async function range(page: Page, name: string, end: "Home" | "End") {
  const control = page.getByRole("slider", { name, exact: true });
  const frames = await page.locator("canvas").getAttribute("data-frames");
  await control.focus();
  if ((await control.inputValue()) === (end === "Home" ? "0" : "100"))
    await control.press(end === "Home" ? "ArrowRight" : "ArrowLeft");
  await control.press(end);
  await expect
    .poll(() => page.locator("canvas").getAttribute("data-frames"))
    .not.toBe(frames);
}
async function pixels(page: Page) {
  const buffer = await page.locator("canvas").screenshot();
  return page.evaluate(async (base64) => {
    const bytes = Uint8Array.from(atob(base64), (character) =>
      character.charCodeAt(0),
    );
    const bitmap = await createImageBitmap(
      new Blob([bytes], { type: "image/png" }),
    );
    const surface = new OffscreenCanvas(
      384,
      Math.round((384 * bitmap.height) / bitmap.width),
    );
    const context = surface.getContext("2d")!;
    context.drawImage(bitmap, 0, 0, surface.width, surface.height);
    const data = Array.from(
      context.getImageData(0, 0, surface.width, surface.height).data,
    );
    bitmap.close();
    return data;
  }, buffer.toString("base64"));
}
function mean(data: number[]) {
  let sum = 0;
  for (let i = 0; i < data.length; i += 4)
    sum += (data[i] + data[i + 1] + data[i + 2]) / 3;
  return sum / (data.length / 4);
}
function changed(a: number[], b: number[]) {
  const indices = new Set<number>();
  for (let i = 0; i < a.length; i += 4)
    if (
      Math.max(
        Math.abs(a[i] - b[i]),
        Math.abs(a[i + 1] - b[i + 1]),
        Math.abs(a[i + 2] - b[i + 2]),
      ) > 10
    )
      indices.add(i / 4);
  return indices;
}

test("room curve stays continuous and manual overrides survive clock changes", () => {
  expect(localHour(new Date("2026-09-14T00:00:00Z"))).toBe(8);
  expect(daylight(14)).toBeGreaterThan(daylight(18.5));
  for (const boundary of [0, 6, 19, 24])
    expect(
      Math.abs(daylight(boundary - 0.0001) - daylight(boundary + 0.0001)),
    ).toBeLessThan(0.001);
  const held = { window: 0, desk: 0.35, blackout: 1 };
  for (const hour of [0, 8, 14, 23])
    expect(resolveRoom(hour, held)).toMatchObject(held);
  expect(automaticRoom(23).desk).toBeGreaterThan(automaticRoom(14).desk);
  const halfSteps = approach(approach(1, 0, 0.5, 1.5), 0, 0.5, 1.5);
  expect(halfSteps).toBeCloseTo(approach(1, 0, 1, 1.5));
  expect(halfSteps).toBeGreaterThan(0);
});

test("room keyboard controls hold per object, restore and never persist or request business state", async ({
  page,
}) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: { authenticated: false, csrf: "synthetic-room-preview" },
    }),
  );
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await openRoom(page);
  await range(page, "窗户开度", "Home");
  await expect(
    page.getByRole("group", { name: "窗户", exact: true }),
  ).toContainText("手动保持");
  await page.getByRole("button", { name: "夜晚", exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "窗户开度", exact: true }),
  ).toHaveValue("0");
  await range(page, "书桌灯亮度", "Home");
  await page
    .getByRole("slider", { name: "书桌灯亮度", exact: true })
    .press("ArrowRight");
  await expect(
    page.getByRole("slider", { name: "书桌灯亮度", exact: true }),
  ).toHaveValue("1");
  await page.getByRole("button", { name: "午后", exact: true }).click();
  await expect(
    page.getByRole("slider", { name: "书桌灯亮度", exact: true }),
  ).toHaveValue("1");
  await page
    .getByRole("button", { name: "恢复书桌灯自动", exact: true })
    .click();
  await expect(
    page.getByRole("slider", { name: "书桌灯亮度", exact: true }),
  ).toHaveValue("0");
  await expect(
    page.getByRole("button", { name: "恢复书桌灯自动" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "跟随本机时间" }).click();
  await expect(page.getByText("跟随本机时钟 · 上海时区")).toBeVisible();
  await expect(
    page.getByRole("slider", { name: "窗户开度", exact: true }),
  ).toHaveValue("0");
  await page.reload();
  await expect(
    page.getByRole("group", { name: "窗户", exact: true }),
  ).toContainText("本地自动");
  expect(
    requests.filter(
      (url) =>
        /\/api\/|^https:|websocket/i.test(url) &&
        !url.endsWith("/api/v1/session"),
    ),
  ).toEqual([]);
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter((key) => key !== "tianshu.appearance"),
    ),
  ).toEqual([]);
});

test("room WebGL pixels prove glass transmission, layered curtains and independent local lights", async ({
  page,
}, testInfo) => {
  test.setTimeout(90000);
  await openRoom(page);
  await range(page, "窗户开度", "Home");
  await range(page, "纱帘开度", "End");
  await range(page, "遮光帘开度", "End");
  const daylightClosedGlass = await pixels(page);
  await page
    .locator("canvas")
    .screenshot({ path: testInfo.outputPath("day-glass-closed.png") });
  await range(page, "纱帘开度", "Home");
  const sheer = await pixels(page);
  await range(page, "遮光帘开度", "Home");
  const blackout = await pixels(page);
  expect(mean(daylightClosedGlass)).toBeGreaterThan(mean(sheer));
  expect(mean(sheer)).toBeGreaterThan(mean(blackout) + 2);
  await page
    .locator("canvas")
    .screenshot({ path: testInfo.outputPath("day-blackout-closed.png") });
  await page.getByRole("button", { name: "夜晚", exact: true }).click();
  await range(page, "书桌灯亮度", "Home");
  await range(page, "床头灯亮度", "Home");
  const off = await pixels(page);
  expect(mean(daylightClosedGlass)).toBeGreaterThan(mean(off) + 10);
  await range(page, "书桌灯亮度", "End");
  const desk = await pixels(page);
  await page
    .locator("canvas")
    .screenshot({ path: testInfo.outputPath("night-desk-only.png") });
  await range(page, "书桌灯亮度", "Home");
  await range(page, "床头灯亮度", "End");
  const bedside = await pixels(page);
  await page
    .locator("canvas")
    .screenshot({ path: testInfo.outputPath("night-bedside-only.png") });
  const deskPixels = changed(off, desk);
  const bedPixels = changed(off, bedside);
  expect(deskPixels.size).toBeGreaterThan(300);
  expect(bedPixels.size).toBeGreaterThan(300);
  expect(deskPixels.size / (off.length / 4)).toBeLessThan(0.3);
  expect(bedPixels.size / (off.length / 4)).toBeLessThan(0.3);
  const common = [...deskPixels].filter((index) => bedPixels.has(index)).length;
  expect(common / Math.min(deskPixels.size, bedPixels.size)).toBeLessThan(0.4);
  await range(page, "书桌灯亮度", "End");
  const both = await pixels(page);
  expect(mean(both)).toBeGreaterThan(mean(desk));
  expect(mean(both)).toBeGreaterThan(mean(bedside));
  await testInfo.attach("pixel-evidence", {
    body: JSON.stringify({
      meanDay: mean(daylightClosedGlass),
      meanSheer: mean(sheer),
      meanBlackout: mean(blackout),
      meanNightOff: mean(off),
      deskPixels: deskPixels.size,
      bedPixels: bedPixels.size,
      common,
    }),
    contentType: "application/json",
  });
});

test("room motion decays, rapid controls keep one loop, visibility pauses and unmount releases WebGL", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.addInitScript(() => {
    const request = window.requestAnimationFrame.bind(window);
    const cancel = window.cancelAnimationFrame.bind(window);
    const pending = new Set<number>();
    let peak = 0;
    window.requestAnimationFrame = (callback) => {
      const id = request((time) => {
        pending.delete(id);
        callback(time);
      });
      pending.add(id);
      peak = Math.max(peak, pending.size);
      return id;
    };
    window.cancelAnimationFrame = (id) => {
      pending.delete(id);
      cancel(id);
    };
    Object.defineProperty(window, "roomTestRaf", {
      get: () => ({ pending: pending.size, peak }),
    });
  });
  await openRoom(page);
  const canvas = page.locator("canvas");
  await range(page, "窗户开度", "End");
  await expect
    .poll(async () => Number(await canvas.getAttribute("data-wind")), {
      timeout: 10000,
    })
    .toBeGreaterThan(0.75);
  await range(page, "纱帘开度", "Home");
  await expect(canvas).toHaveAttribute("data-window", "1.0000");
  await page.waitForTimeout(900);
  const movingCloth = await pixels(page);
  await page.waitForTimeout(350);
  expect(changed(movingCloth, await pixels(page)).size).toBeGreaterThan(0);
  await range(page, "窗户开度", "Home");
  const afterClose = Number(await canvas.getAttribute("data-wind"));
  expect(afterClose).toBeGreaterThan(0.3);
  await expect
    .poll(async () => Number(await canvas.getAttribute("data-wind")), {
      timeout: 10000,
    })
    .toBeLessThan(0.04);
  for (let i = 0; i < 8; i++)
    await page
      .getByRole("slider", { name: "窗户开度", exact: true })
      .press(i % 2 ? "Home" : "End");
  const before = Number(await canvas.getAttribute("data-frames"));
  const start = Date.now();
  await page.waitForTimeout(2000);
  const rate =
    (Number(await canvas.getAttribute("data-frames")) - before) /
    ((Date.now() - start) / 1000);
  expect(rate).toBeGreaterThan(0);
  expect(rate).toBeLessThanOrEqual(32);
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { roomTestRaf: { peak: number } }).roomTestRaf
          .peak,
    ),
  ).toBeLessThanOrEqual(2);
  // Explicit visibility-event fixture, not a CSS-hidden canvas or a real-phone claim.
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const paused = await canvas.getAttribute("data-frames");
  await page.waitForTimeout(400);
  expect(await canvas.getAttribute("data-frames")).toBe(paused);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => canvas.getAttribute("data-frames")).not.toBe(paused);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(canvas).toHaveAttribute("data-motion", "reduced");
  await expect(canvas).toHaveAttribute("data-wind", "0.0000");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(canvas).toHaveAttribute("data-motion", "normal");
  await page.getByRole("button", { name: "外观设置", exact: true }).click();
  await page.getByRole("checkbox", { name: /减少动态/ }).check();
  await page.keyboard.press("Escape");
  await expect(canvas).toHaveAttribute("data-motion", "reduced");
  await page.waitForTimeout(150);
  const idle = await canvas.getAttribute("data-frames");
  await page.waitForTimeout(350);
  expect(await canvas.getAttribute("data-frames")).toBe(idle);
  const handle = await canvas.elementHandle();
  await page.getByRole("link", { name: "前往陪伴", exact: true }).click();
  await expect(canvas).toHaveCount(0);
  expect(
    await handle!.evaluate((element) =>
      (element as HTMLCanvasElement).getContext("webgl2")!.isContextLost(),
    ),
  ).toBe(true);
  await page.waitForTimeout(200);
  expect(await handle!.getAttribute("data-frames")).toBe(idle);
  await openRoom(page);
  await expect(canvas).toHaveCount(1);
  await testInfo.attach("renderer-sample", {
    body: JSON.stringify({
      viewport: testInfo.project.use.viewport,
      browser: await page.context().browser()!.version(),
      seconds: 2,
      deliveredFramesPerSecond: rate,
      drawCalls: await canvas.getAttribute("data-draw-calls"),
      triangles: await canvas.getAttribute("data-triangles"),
      gpu: await canvas.evaluate((element) => {
        const gl = (element as HTMLCanvasElement).getContext("webgl2")!;
        const extension = gl.getExtension("WEBGL_debug_renderer_info");
        return extension
          ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL)
          : "unavailable";
      }),
      condition:
        "Windows Chromium headless, warm scene, normal motion, test runner; not physical mobile",
    }),
    contentType: "application/json",
  });
});

test("room unavailable WebGL retains controls and retry recovers", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      ...args: Parameters<typeof getContext>
    ) {
      if (
        args[0] === "webgl2" &&
        !(window as unknown as { roomTestAllow?: boolean }).roomTestAllow
      )
        return null;
      return getContext.apply(this, args);
    } as typeof getContext;
  });
  await page.goto("/#/roles/room");
  await expect(page.getByRole("alert")).toContainText("当前无法显示 3D 画面");
  await page
    .getByRole("slider", { name: "窗户开度", exact: true })
    .press("Home");
  await page.evaluate(() => {
    (window as unknown as { roomTestAllow: boolean }).roomTestAllow = true;
  });
  await page.getByRole("button", { name: "重试画面", exact: true }).click();
  await expect(page.locator("canvas")).toHaveAttribute("data-frames", /[1-9]/);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("slider", { name: "窗户开度", exact: true }),
  ).toHaveValue("0");
});

test("room real context loss restores render and held state", async ({
  page,
}) => {
  await openRoom(page);
  await range(page, "窗户开度", "Home");
  await page.locator("canvas").evaluate((element) => {
    const extension = (element as HTMLCanvasElement)
      .getContext("webgl2")!
      .getExtension("WEBGL_lose_context")!;
    (window as unknown as { roomTestLoss: WEBGL_lose_context }).roomTestLoss =
      extension;
    extension.loseContext();
  });
  await expect(page.getByRole("alert")).toContainText("图形画面暂时中断");
  await page.waitForTimeout(150);
  await page.evaluate(() =>
    (
      window as unknown as { roomTestLoss: WEBGL_lose_context }
    ).roomTestLoss.restoreContext(),
  );
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator("canvas")).toHaveAttribute("data-window", "0.0000");
});

test("room desktop and mobile real screenshots, themes, keyboard, reduced motion and 200% text", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openRoom(page);
  for (const [name, file] of [
    ["清晨", "morning"],
    ["午后", "afternoon"],
    ["黄昏", "dusk"],
    ["夜晚", "night"],
  ]) {
    await page.getByRole("button", { name, exact: true }).click();
    await page
      .locator(".room-figure")
      .screenshot({ path: testInfo.outputPath(`${file}.png`) });
  }
  for (const theme of ["浅色", "深色"]) {
    await page.getByRole("button", { name: "外观设置", exact: true }).click();
    await page.getByRole("radio", { name: theme, exact: true }).check();
    await page.keyboard.press("Escape");
    expect(
      (
        await new AxeBuilder({ page })
          .include(".room-preview")
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(
        `${theme === "浅色" ? "light" : "dark"}-page.png`,
      ),
      fullPage: true,
    });
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "200%";
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "";
    });
  }
  const summary = page.locator(".room-controls > summary");
  await summary.focus();
  await summary.press("Enter");
  await expect(
    page.getByRole("slider", { name: "窗户开度", exact: true }),
  ).not.toBeVisible();
  await summary.press("Enter");
  await expect(
    page.getByRole("slider", { name: "窗户开度", exact: true }),
  ).toBeVisible();
  await range(page, "窗户开度", "End");
  await expect(page.locator("canvas")).toHaveAttribute("data-window", "1.0000");
  await expect(page.locator("canvas")).toHaveAttribute("data-wind", "0.0000");
  await page.getByRole("checkbox", { name: /简化画质/ }).check();
  await expect(page.locator("canvas")).toHaveAttribute("data-frames", /[1-9]/);
  await expect(page.locator("canvas")).toHaveAttribute("data-window", "1.0000");
  expect(errors).toEqual([]);
});
