import { test, expect } from "@playwright/test";

test("personal timeline separates portraits, shows source and coverage, and clears revoked material", async ({
  page,
}, info) => {
  const csrf = "timeline-browser-fixture";
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        csrf,
        username: "timeline-owner",
        conversations: [],
        dialogue: { available: false },
      },
    }),
  );
  let revoked = false;
  let role = "actor:a";
  await page.route("**/api/v1/memory/*", (route) => {
    const name = route.request().url().split("/").at(-1);
    const request = route.request().postDataJSON();
    if (name === "state")
      return route.fulfill({
        json: {
          available: true,
          code: "ready",
          actor_id: "actor:a",
          roles: ["a", "b"].map((id) => ({
            id: `actor:${id}`,
            label: `角色${id}`,
            version: 1,
            available: true,
            reason: null,
          })),
        },
      });
    expect(request.role_version).toBe(1);
    role = request.role_id;
    if (revoked)
      return route.fulfill({
        status: 403,
        json: { code: "upstream_forbidden" },
      });
    if (name === "overview")
      return route.fulfill({
        json: {
          memory_group_count: 2,
          counts_truncated: false,
          scope_version: 1,
          verified_at: "2026-10-28T12:00:00Z",
          learning: { received: 8, completed: 6, waiting: 2, truncated: false },
        },
      });
    if (name === "records") {
      expect(request.subject).toBeNull();
      return route.fulfill({
        json: {
          verified_at: "2026-10-28T12:00:00Z",
          scope_version: 1,
          next_cursor: null,
          items: ["summary", "portrait"].map((kind, index) => ({
            semantic_group_id: `${role}:${kind}`,
            category: "evidence",
            field_key: `memory.${kind}`,
            item_key: null,
            units: [
              {
                record_id: `${role}:${index}`,
                record_version: 1,
                statement: `${role}的${kind === "portrait" ? "长期画像" : "周末散步经历"}`,
              },
            ],
            provenance: {
              kind,
              subject_person_id: "person:owner",
              organized_at: "2026-10-28T11:00:00Z",
              effective_at: index ? null : "2026-10-24T00:00:00Z",
              expires_at: "2027-01-01T00:00:00Z",
              expired: false,
              importance: "normal",
              retention: "medium",
              memory_kind: null,
              memory_state: null,
              source_count: 1,
              context_count: 3,
              source_excerpts: [
                {
                  observed_at: "2026-10-24T00:00:00Z",
                  text: `${role}本人原话：我周末去河边散步。`,
                  truncated: false,
                },
              ],
              excerpts_truncated: false,
            },
          })),
        },
      });
    }
    throw new Error(`Unexpected memory route ${name}`);
  });
  await page.goto("/#/people");
  await expect(
    page.getByText("actor:a的周末散步经历", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("status").filter({ hasText: "待整理 2 条" }),
  ).toBeVisible();
  await expect(page.locator(".memory-records > li").first()).toContainText(
    "长期画像",
  );
  await page.getByRole("button", { name: "经历与表达", exact: true }).click();
  await expect(
    page.getByText("actor:a的长期画像", { exact: true }),
  ).toHaveCount(0);
  await page.getByText("查看整理依据", { exact: true }).click();
  await expect(
    page.getByText("actor:a本人原话：我周末去河边散步。", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath(`memory-timeline-${info.project.name}.png`),
    fullPage: true,
  });
  await page.getByLabel("查看哪位角色的记忆").selectOption("actor:b");
  await expect(
    page.getByText("actor:b的周末散步经历", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("actor:a本人原话：我周末去河边散步。", { exact: true }),
  ).toHaveCount(0);
  revoked = true;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(
    page.getByText("actor:b的周末散步经历", { exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".memory-provenance")).toHaveCount(0);
});

test("review shows existing memories and actual progress, then clears a source withdrawal with valid authorization", async ({
  page,
}, info) => {
  await page.clock.install();
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        csrf: "review-fixture",
        username: "review-owner",
        conversations: [],
        dialogue: { available: false },
      },
    }),
  );
  let version = 1;
  let removed = false;
  const entry = (id: string, text: string) => ({
    semantic_group_id: id,
    text,
    truncated: false,
    effective_at: "2026-10-27T00:00:00Z",
    state: "confirmed",
    valid_until: "2026-10-28T00:00:00Z",
  });
  const agreement = entry(
    "agreement:one",
    "周末约好一起取书，完成情况尚未确认。",
  );
  const portrait = entry("portrait:one", "愿意分享读书经历。");
  await page.route("**/api/v1/memory/*", (route) => {
    const name = route.request().url().split("/").at(-1);
    if (name === "state")
      return route.fulfill({
        json: {
          available: true,
          code: "ready",
          actor_id: "actor:a",
          roles: [
            {
              id: "actor:a",
              label: "角色a",
              version: 1,
              available: true,
              reason: null,
            },
          ],
        },
      });
    if (name === "overview")
      return route.fulfill({
        json: {
          memory_group_count: removed ? 0 : 2,
          counts_truncated: false,
          scope_version: version,
          verified_at: "2026-10-29T00:00:00Z",
          review: {
            periods: {
              "7": {
                count: removed ? 0 : 1,
                items: removed ? [] : [agreement],
              },
              "30": {
                count: removed ? 0 : 2,
                items: removed
                  ? []
                  : [agreement, entry("older", "月初还讨论过一本旧书。")],
              },
            },
            open_agreements: {
              count: removed ? 0 : 1,
              items: removed ? [] : [{ ...agreement, overdue: true }],
            },
            portrait: removed ? null : portrait,
            truncated: false,
          },
        },
      });
    if (name === "records")
      return route.fulfill({
        json: {
          verified_at: "2026-10-29T00:00:00Z",
          scope_version: version,
          next_cursor: null,
          items: removed
            ? []
            : [agreement, portrait].map((item, index) => ({
                semantic_group_id: item.semantic_group_id,
                category: "evidence",
                field_key: "memory.summary",
                item_key: null,
                units: [
                  {
                    record_id: `record:${index}`,
                    record_version: 1,
                    statement: item.text,
                  },
                ],
                provenance: {
                  kind: index ? "portrait" : "summary",
                  subject_person_id: "person:owner",
                  organized_at: "2026-10-28T00:00:00Z",
                  effective_at: item.effective_at,
                  expires_at: "2027-01-01T00:00:00Z",
                  expired: false,
                  importance: "normal",
                  retention: "long",
                  valid_until: item.valid_until,
                  memory_kind: index ? "experience" : "agreement",
                  memory_state: index ? "described" : "confirmed",
                  source_count: 1,
                  context_count: 2,
                  replacement_count: index ? 0 : 1,
                  related_memory_count: 0,
                  source_excerpts: [
                    {
                      observed_at: item.effective_at,
                      text: "本人原话：书已预约，待取。",
                      truncated: false,
                    },
                  ],
                  excerpts_truncated: false,
                },
              })),
        },
      });
    throw new Error(`Unexpected memory route ${name}`);
  });
  await page.goto("/#/people");
  const review = page.getByRole("region", { name: "记忆回顾" });
  await expect(
    review
      .locator("section")
      .filter({
        has: page.getByRole("heading", { name: "尚未结束的约定", exact: true }),
      })
      .getByText(/约定期限：/),
  ).toBeVisible();
  await expect(
    review.getByText("约定日期已过，完成情况仍需确认。", { exact: true }),
  ).toBeVisible();
  await expect(review.getByText("已完成", { exact: true })).toHaveCount(0);
  await page.getByLabel("回顾时间范围").selectOption("30");
  await expect(
    review.getByText("月初还讨论过一本旧书。", { exact: true }),
  ).toBeVisible();
  const record = page
    .locator(".memory-records > li")
    .filter({ hasText: agreement.text });
  await record.getByText("查看整理依据", { exact: true }).click();
  await expect(
    record.getByText("本条替代了 1 条先前记忆，当前内容依据本次原话保存。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath(`memory-review-${info.project.name}.png`),
    fullPage: true,
  });
  removed = true;
  version = 2;
  await page.clock.fastForward(15001);
  await expect(page.locator(".memory-records > li")).toHaveCount(0);
  await expect(
    page.getByText("本人原话：书已预约，待取。", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(agreement.text, { exact: true })).toHaveCount(0);
  await expect(
    review.getByText("本次核验范围没有未结束的约定。", { exact: true }),
  ).toBeVisible();
});
