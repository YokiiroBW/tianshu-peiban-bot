import { test, expect, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import {
  fixtureSubscription,
  fixtureView,
  installMediaFixture,
} from "./media.fixtures";
import type { Quality, Rules, RulePreview } from "../src/features/media/types";

const BV = "https://www.bilibili.com/video/BV1xx411c7mD";
const next = (page: Page) =>
  page.getByRole("button", { name: "下一步", exact: true });
const rows = (page: Page) => page.locator(".media-rule-row");
async function create(page: Page) {
  await page.goto("/#/media/video");
  await page.getByRole("button", { name: "新建订阅", exact: true }).click();
  await page.getByLabel("订阅名称").fill("高级策略样例");
  await page.getByLabel("来源链接或 ID", { exact: true }).fill("123456");
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await next(page).click();
}
async function field(row: Locator, name: string, op?: string) {
  await row
    .getByRole("combobox", { name: "字段", exact: true })
    .selectOption(name);
  if (op)
    await row
      .getByRole("combobox", { name: "条件", exact: true })
      .selectOption(op);
}
async function advanced(page: Page) {
  await page.locator(".media-stream-options > .detail-card-trigger").click();
}
async function save(page: Page) {
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await next(page).click();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await expect(page.locator("#media-subdirectory-preview")).toContainText(
    "/media/Bilibili/",
  );
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "高级策略样例", exact: true }),
  ).toBeVisible();
}

test("advanced rules submit typed dates ranges zero and false with readable unknown NOT facts", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  let requestRules: Rules | undefined;
  state.rulePreviewHandler = async (route) => {
    requestRules = route.request().postDataJSON().rules;
    const rule = requestRules!.whitelist[0].rules[3];
    const response: RulePreview = {
      context: {
        source: { kind: "favorite", id: "123456" },
        member: false,
        fields: {
          published_at: { available: true, value: "2026-10-14T00:00:00Z" },
          favorited_at: { available: false, value: null },
          part_count: { available: true, value: 0 },
          duration_seconds: { available: true, value: 0 },
          is_collaboration: { available: true, value: false },
        },
      },
      results: [
        {
          cid: "100",
          decision: "skip",
          reason: "whitelist_no_match",
          automatic_enqueue_allowed: false,
          requires_rule_attention: false,
          trace: {
            trace: [
              {
                group_id: "test",
                list_kind: "whitelist",
                result: "not_matched",
                reason: "whitelist_no_match",
                rules: [
                  {
                    rule_id: rule.id,
                    field: "favorited_at",
                    op: "between",
                    negate: true,
                    result: "not_matched",
                    reason: "field_missing",
                  },
                ],
              },
            ],
          },
        },
      ],
    };
    await route.fulfill({ json: response });
  };
  await create(page);
  await page.getByRole("button", { name: "添加白名单组" }).click();
  await field(rows(page).nth(0), "duration_seconds", "between");
  await rows(page).nth(0).getByLabel("范围起点").fill("0");
  await rows(page).nth(0).getByLabel("范围终点").fill("300.5");
  await page.getByRole("button", { name: "添加 AND 条件" }).click();
  await field(rows(page).nth(1), "part_count");
  await rows(page).nth(1).getByLabel("匹配值", { exact: true }).fill("0");
  await page.getByRole("button", { name: "添加 AND 条件" }).click();
  await field(rows(page).nth(2), "published_at", "gte");
  await rows(page)
    .nth(2)
    .getByLabel("匹配值", { exact: true })
    .fill("2026-10-15T08:00");
  await page.getByRole("button", { name: "添加 AND 条件" }).click();
  await field(rows(page).nth(3), "favorited_at", "between");
  await rows(page).nth(3).getByLabel("范围起点").fill("2026-10-01T00:00");
  await rows(page).nth(3).getByLabel("范围终点").fill("2026-10-15T23:59:59");
  await rows(page).nth(3).getByLabel("取反（NOT）").check();
  await page.getByRole("button", { name: "添加 AND 条件" }).click();
  await field(rows(page).nth(4), "is_collaboration");
  await expect(rows(page).nth(4).getByLabel("是否联合投稿")).toHaveValue(
    "false",
  );
  await expect(next(page)).toBeEnabled();
  await page.getByLabel("试算视频链接").fill(BV);
  await page.getByRole("button", { name: "试算规则", exact: true }).click();
  await expect(
    page.getByText("已核实不在该来源", { exact: false }),
  ).toBeVisible();
  const context = page.locator(".media-rule-context");
  await expect(context).toContainText("所有分 P 共用");
  await expect(
    context
      .locator("div")
      .filter({ has: page.locator("dt", { hasText: "联合投稿" }) }),
  ).toContainText("否");
  await page.getByText("查看逐条规则解释", { exact: true }).click();
  await expect(
    page.getByText("字段值未知，取反也不会视为命中", { exact: false }),
  ).toBeVisible();
  expect(requestRules!.whitelist[0].rules.map((rule) => rule.value)).toEqual([
    [0, 300.5],
    0,
    "2026-10-15T00:00:00.000Z",
    ["2026-09-30T16:00:00.000Z", "2026-10-15T15:59:59.000Z"],
    false,
  ]);
  const previewCall = state.calls.find(
    (call) => call.operation === "rules/preview",
  )!;
  expect(previewCall.body.source).toEqual({ kind: "favorite", id: "123456" });
  expect(previewCall.body).not.toHaveProperty("favorited_at");
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await save(page);
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!.body;
  expect(saved.rules).toEqual(requestRules);
  expect(saved.quality).not.toHaveProperty("stream_options");
});

test("legacy editing keeps timestamps rules and quality unchanged when advanced options are only opened", async ({
  page,
}) => {
  const view = fixtureView();
  const subscription = fixtureSubscription();
  subscription.rules.whitelist = [
    {
      id: "group-date",
      rules: [
        {
          id: "date",
          field: "published_at",
          op: "gte",
          value: "2026-10-15T08:00:00+08:00",
          case_sensitive: false,
        },
      ],
    },
  ];
  subscription.quality = {
    mode: "exact",
    quality_id: "80",
    allow_fallback: false,
  };
  view.subscriptions = [subscription];
  const state = await installMediaFixture(page, view);
  await page.goto("/#/media/video");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await next(page).click();
  await advanced(page);
  await expect(page.getByLabel("允许 HDR 视频")).toBeChecked();
  await expect(page.getByLabel("允许杜比视界视频")).toBeChecked();
  await expect(page.getByLabel("允许杜比音频")).toBeChecked();
  await expect(page.getByLabel("允许 Hi-Res 音频")).toBeChecked();
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await next(page).click();
  await page.getByRole("button", { name: "保存订阅", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: subscription.label, exact: true }),
  ).toBeVisible();
  const saved = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!.body;
  expect(saved.quality).toEqual(subscription.quality);
  expect((saved.rules as Rules).whitelist).toEqual(
    subscription.rules.whitelist,
  );
});

test("numeric and date ranges report local errors without turning blank inputs into zero", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await create(page);
  await page.getByRole("button", { name: "添加白名单组" }).click();
  await field(rows(page).first(), "part_count", "between");
  await expect(next(page)).toBeDisabled();
  await rows(page).first().getByLabel("范围起点").fill("5");
  await rows(page).first().getByLabel("范围终点").fill("1");
  await expect(page.getByText("范围起点不能晚于或大于终点。")).toBeVisible();
  await expect(next(page)).toBeDisabled();
  await rows(page).first().getByLabel("范围终点").fill("5");
  await expect(next(page)).toBeEnabled();
  await rows(page).first().getByLabel("范围起点").fill("1.5");
  await expect(page.getByText("请填写非负整数。")).toBeVisible();
  await field(rows(page).first(), "published_at", "between");
  await rows(page).first().getByLabel("范围起点").fill("2026-10-15T00:00");
  await rows(page).first().getByLabel("范围终点").fill("2026-10-01T00:00");
  await expect(page.getByText("范围起点不能晚于或大于终点。")).toBeVisible();
  expect(
    state.calls.some((call) => call.operation === "subscriptions/save"),
  ).toBe(false);
});

async function setPolicy(page: Page) {
  await advanced(page);
  await page.getByLabel("最低视频质量").selectOption("64");
  await page.getByLabel("最高视频质量").selectOption("80");
  await page.getByLabel("最低音频质量").selectOption("30280");
  await page.getByLabel("最高音频质量").selectOption("30251");
  await page
    .getByRole("button", { name: "添加 HEVC / H.265", exact: true })
    .click();
  await page
    .getByRole("button", { name: "添加 AVC / H.264", exact: true })
    .click();
  await page
    .getByRole("button", { name: "上移 AVC / H.264", exact: true })
    .click();
  await page.getByLabel("允许 HDR 视频").uncheck();
  await page.getByLabel("允许杜比视界视频").uncheck();
  await page.getByLabel("允许杜比音频").uncheck();
  await page.keyboard.press("Escape");
}

test("subscriptions and links share the same policy payload and changing parts retains advanced constraints", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  await create(page);
  await setPolicy(page);
  if ((await page.locator("dialog:modal").count()) > 1)
    await page.keyboard.press("Escape");
  await save(page);
  const subscriptionQuality = state.calls.find(
    (call) => call.operation === "subscriptions/save",
  )!.body.quality as Quality;
  expect(subscriptionQuality.stream_options).toEqual({
    video_min_quality: "64",
    video_max_quality: "80",
    audio_min_quality: "30280",
    audio_max_quality: "30251",
    codecs: ["avc", "hevc"],
    allow_hdr: false,
    allow_dolby_vision: false,
    allow_dolby_audio: false,
    allow_hires: true,
  });
  await page.goto("/#/media/download");
  await page.getByLabel("B 站视频链接").fill(BV);
  await page.getByRole("button", { name: "解析链接", exact: true }).click();
  await expect(page.locator(".media-preview")).toBeVisible();
  await page.getByLabel("目标媒体库").selectOption("test-library");
  await setPolicy(page);
  await page.getByText("P2 · 山海", { exact: true }).click();
  await advanced(page);
  await expect(page.getByLabel("最高视频质量")).toHaveValue("80");
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "创建下载任务（1 个分 P）", exact: true })
    .click();
  const enqueue = state.calls.find((call) => call.operation === "enqueue")!;
  expect(enqueue.body.quality).toEqual(subscriptionQuality);
  expect(enqueue.body).not.toHaveProperty("rules");
});

test("audio business ordering and exact policy conflicts explain limits without blocking valid saves", async ({
  page,
}) => {
  await installMediaFixture(page);
  await create(page);
  await page.getByLabel("质量策略").selectOption("120");
  await advanced(page);
  await page.getByLabel("最低视频质量").selectOption("80");
  await page.getByLabel("最高视频质量").selectOption("64");
  await expect(
    page.getByText("最低视频质量不能高于最高视频质量。"),
  ).toBeVisible();
  await expect(next(page)).toBeDisabled();
  await page.getByLabel("最高视频质量").selectOption("80");
  await page.getByLabel("最低音频质量").selectOption("30280");
  await page.getByLabel("最高音频质量").selectOption("30250");
  await expect(next(page)).toBeEnabled();
  await expect(page.getByText(/指定画质在当前允许策略之外/)).toBeVisible();
  await page.getByLabel("最低音频质量").selectOption("30251");
  await expect(
    page.getByText("最低音频质量不能高于最高音频质量。"),
  ).toBeVisible();
  await expect(next(page)).toBeDisabled();
  await page.getByRole("button", { name: "恢复高级策略默认值" }).click();
  await expect(next(page)).toBeEnabled();
  await expect(page.locator(".media-policy-summary")).toContainText(
    "不限制质量范围与编码",
  );
});

test("advanced controls remain reachable by keyboard without overflow and pass accessibility checks", async ({
  page,
}, info) => {
  await installMediaFixture(page);
  await create(page);
  await page.getByRole("button", { name: "添加白名单组" }).click();
  await field(rows(page).first(), "favorited_at", "between");
  await rows(page).first().getByLabel("范围起点").fill("2026-10-01T00:00");
  await rows(page).first().getByLabel("范围终点").fill("2026-10-15T00:00");
  const summary = page.locator(".media-stream-options > .detail-card-trigger");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("最低视频质量")).toBeVisible();
  await page.getByRole("button", { name: "添加 AV1", exact: true }).click();
  await page
    .getByRole("button", { name: "添加 AVC / H.264", exact: true })
    .click();
  await page.getByRole("button", { name: "上移 AVC / H.264" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".media-codec-list li").first()).toContainText(
    "AVC / H.264",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath("media-policy-" + info.project.name + ".png"),
    fullPage: true,
  });
});

test("editing source or rules cancels late rule preview results and errors", async ({
  page,
}) => {
  const state = await installMediaFixture(page);
  let release: (() => Promise<void>) | undefined;
  state.rulePreviewHandler = async (route) => {
    await new Promise<void>((resolve) => {
      release = async () => {
        await route
          .fulfill({ status: 503, json: { code: "source_budget_exceeded" } })
          .catch(() => {});
        resolve();
      };
    });
  };
  await create(page);
  await page.getByLabel("试算视频链接").fill(BV);
  await page.getByRole("button", { name: "试算规则", exact: true }).click();
  await expect.poll(() => !!release).toBe(true);
  await page.getByLabel("试算视频链接").fill(BV + "?p=2");
  await release!();
  await expect(
    page.getByRole("button", { name: "试算规则", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText(/来源成员扫描超时/)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "规则试算结果" })).toHaveCount(
    0,
  );
});
