import { test, expect } from "@playwright/test";
import {
  BUILTIN_THEME_PRESETS,
  isValidThemePreset,
} from "../src/design/themeRegistry";
import { getShichenInfo, routineTimeProgress } from "../src/design/shichen";
import { answer, nativeSession, state } from "./native-fixture";

test("theme imports reject incomplete records without accepting layout properties", () => {
  for (const preset of BUILTIN_THEME_PRESETS)
    expect(isValidThemePreset(preset)).toBe(true);
  const preset = structuredClone(BUILTIN_THEME_PRESETS[0]);
  for (const value of [
    null,
    { id: "broken", name: "损坏主题", tokens: { day: null, night: {} } },
    { ...preset, previewColors: null },
    { ...preset, tokens: { day: {}, night: {} } },
    {
      ...preset,
      tokens: {
        ...preset.tokens,
        day: { ...preset.tokens.day, display: "none" },
      },
    },
  ]) {
    expect(isValidThemePreset(value)).toBe(false);
  }
});

test("daylight countdown and configured overnight routine use the displayed timezone", () => {
  const at = (time: string) => new Date(`2026-11-04T${time}:00+08:00`);
  const beforeDawn = getShichenInfo(at("05:45"), "Asia/Shanghai");
  expect(beforeDawn.nextTransitionLabel).toContain("06:00");
  expect(beforeDawn.remainingLabel).toContain("15m");
  expect(getShichenInfo(at("06:00"), "Asia/Shanghai").remainingLabel).toContain(
    "12h 30m",
  );
  expect(getShichenInfo(at("18:15"), "Asia/Shanghai").remainingLabel).toContain(
    "15m",
  );
  expect(getShichenInfo(at("18:30"), "Asia/Shanghai").remainingLabel).toContain(
    "11h 30m",
  );
  expect(
    routineTimeProgress(at("15:00"), "Asia/Shanghai", "07:00", "23:00"),
  ).toBe(50);
  expect(
    routineTimeProgress(at("02:00"), "Asia/Shanghai", "22:00", "06:00"),
  ).toBe(50);
  expect(
    routineTimeProgress(at("08:00"), "Asia/Shanghai", "22:00", "06:00"),
  ).toBe(0);
  expect(
    routineTimeProgress(at("08:00"), "Asia/Shanghai", "invalid", "06:00"),
  ).toBeNull();
});

test("all theme presets render in both modes and survive a reload with malformed stored themes", async ({
  page,
}) => {
  await nativeSession(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() =>
    localStorage.setItem(
      "tianshu.customThemes.v1",
      JSON.stringify([
        { id: "bad", name: "坏主题", tokens: { day: null, night: null } },
      ]),
    ),
  );
  await page.goto("/#/workbench");
  await page.getByRole("button", { name: "外观设置", exact: true }).click();
  const gallery = page.getByRole("dialog", { name: "外观设置 · 多宝主题画廊" });
  for (const preset of BUILTIN_THEME_PRESETS) {
    await gallery
      .getByRole("button", { name: new RegExp(preset.name) })
      .click();
    for (const [mode, label] of [
      ["light", "☀️ 昼境（浅色宣纸系）"],
      ["dark", "🌙 夜境（深色大漆系）"],
    ]) {
      await gallery.getByRole("radio", { name: label, exact: true }).check();
      await expect(page.locator("html")).toHaveAttribute("data-theme", mode);
      await expect(page.locator("html")).toHaveAttribute(
        "data-preset",
        preset.id,
      );
    }
  }
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute(
    "data-preset",
    BUILTIN_THEME_PRESETS.at(-1)!.id,
  );
  await expect(
    page.getByRole("heading", { name: "小屋角色的近况", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("aggregated evidence preserves each revision and the exact source text", async ({
  page,
}) => {
  await nativeSession(page);
  const original =
    "请保留 person_1234567890abcdef 和 memory_batch_abcdef1234567890 的完整原文。";
  const sources = [
    { source_id: "message_1234567890abcdef", revision: 1, locator: null },
    { source_id: "message_abcdef1234567890", revision: 2, locator: null },
  ];
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v1/life/state**", (route) =>
    answer(route, {
      ...state,
      activity: { ...state.activity, source_refs: sources },
    }),
  );
  await page.route("**/api/v1/sources/read**", (route) => {
    requests.push(route.request().postDataJSON());
    return answer(route, {
      content: original,
      media: null,
      representation: "text",
      coverage: { complete: true, missing: [] },
      next_position: null,
    });
  });
  await page.goto("/#/roles/life");
  const current = page.locator(".life-now");
  await current.getByRole("button", { name: /核对原文依据/ }).click();
  await current
    .locator("summary")
    .filter({ hasText: "查看原文依据 · 修订 2" })
    .click();
  await expect(current.locator("blockquote")).toHaveText(original);
  expect(requests).toHaveLength(1);
  expect(requests[0].source_ref).toEqual(sources[1]);
  await expect(page.locator(".sundial-ring")).toHaveCSS(
    "animation-name",
    "none",
  );
  await expect(page.getByText("神思 · 清朗")).toHaveCount(0);
  await expect(page.getByText("气韵 · 平和")).toHaveCount(0);
});
