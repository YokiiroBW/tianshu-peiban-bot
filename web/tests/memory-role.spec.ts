import { test, expect, type Route } from "@playwright/test";

const roles = [
  {
    id: "actor:a",
    label: "默认角色",
    version: 0,
    available: true,
    reason: null,
  },
  { id: "actor:b", label: "小岚", version: 2, available: true, reason: null },
];

async function answer(route: Route, value: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

test("memory role selection persists and late responses cannot replace the new role", async ({
  page,
}, testInfo) => {
  await page.route("**/api/v1/session", (route) =>
    answer(route, {
      authenticated: true,
      csrf: "memory-role-browser-fixture",
      username: "memory-role-owner",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
  await page.route("**/api/v1/logout", (route) => answer(route, { ok: true }));
  let currentRoles = [...roles];
  let delayA = false;
  let holdBVerification = false;
  let revokeB = false;
  let releaseVerification: (() => void) | undefined;
  let bVerifications = 0;
  function releaseHeldVerification() {
    const release = releaseVerification;
    releaseVerification = undefined;
    if (!release) throw new Error("verification was not held");
    release();
  }
  const requests: { path: string; role: string }[] = [];
  await page.route("**/api/v1/memory/*", async (route) => {
    const path = new URL(route.request().url()).pathname.split("/").at(-1)!;
    if (path === "state")
      return answer(route, {
        available: true,
        code: "ready",
        actor_id: "actor:a",
        roles: currentRoles,
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    const body = route.request().postDataJSON() as {
      role_id: string;
      role_version: number;
      cursor?: string | null;
    };
    requests.push({ path, role: body.role_id });
    expect(body.role_version).toBe(body.role_id === "actor:a" ? 0 : 2);
    if (path === "groups")
      return answer(route, {
        available: true,
        groups: [
          {
            id: "registered-group",
            label: "已登记群",
            conversation_id: "conversation-b",
          },
        ],
      });
    if (path === "group-insights")
      return answer(route, {
        group: {
          id: "registered-group",
          label: "已登记群",
          conversation_id: "conversation-b",
        },
        window: { start_timestamp: 1792418400, end_timestamp: 1792504799 },
        stats: { state: "unavailable", code: "not_configured" },
        topics: {
          state: "available",
          data: { items: [], next_cursor: null, omitted_invalidated: false },
        },
        records: {
          state: "available",
          data: {
            items: [
              {
                semantic_group_id: "group-b",
                units: [
                  {
                    record_id: "record-b",
                    record_version: 1,
                    statement: "乙的记忆",
                  },
                ],
              },
            ],
            next_cursor: null,
          },
        },
      });
    if (path === "overview") {
      if (body.role_id === "actor:b" && holdBVerification) {
        bVerifications++;
        await new Promise<void>((resolve) => {
          releaseVerification = resolve;
        });
        if (revokeB)
          return route.fulfill({
            status: 403,
            contentType: "application/json",
            body: JSON.stringify({ code: "upstream_forbidden" }),
          });
      }
      if (body.role_id === "actor:a" && delayA)
        await new Promise((resolve) => setTimeout(resolve, 650));
      try {
        return await answer(route, {
          memory_group_count: body.role_id === "actor:a" ? 1 : 2,
          counts_truncated: false,
          verified_at: "2026-09-30T00:00:00Z",
          scope_version: 1,
        });
      } catch {
        return;
      } // An aborted old request has no response to deliver.
    }
    if (path === "subjects")
      return answer(route, {
        items: [
          {
            subject: {
              kind: "group",
              conversation_id:
                body.role_id === "actor:a"
                  ? "conversation-a"
                  : "conversation-b",
            },
            categories: ["偏好"],
            group_count: 1,
            group_count_truncated: false,
          },
        ],
        next_cursor: null,
        verified_at: "2026-09-30T00:00:00Z",
        scope_version: 1,
      });
    if (path === "records") {
      if (body.role_id === "actor:a" && delayA)
        await new Promise((resolve) => setTimeout(resolve, 650));
      return answer(route, {
        items: [
          {
            semantic_group_id:
              body.role_id === "actor:a" ? "group-a" : "group-b",
            category: "偏好",
            field_key: "drink",
            item_key: "tea",
            units: [
              {
                record_id: body.role_id === "actor:a" ? "record-a" : "record-b",
                record_version: 1,
                statement: body.role_id === "actor:a" ? "甲的记忆" : "乙的记忆",
                conditions: [],
                negations: [],
                valid_time: null,
                uncertainty: null,
                reality: "real",
              },
            ],
          },
        ],
        next_cursor: null,
        verified_at: "2026-09-30T00:00:00Z",
        scope_version: 1,
      });
    }
    throw new Error(`unexpected memory path ${path}`);
  });

  await page.goto("/#/people");
  await expect(page.getByLabel("查看哪位角色的记忆")).toHaveValue("actor:a");
  await expect(page.getByText("甲的记忆")).toBeVisible();
  await page.getByLabel("查看哪位角色的记忆").selectOption("actor:b");
  await expect(page.getByText("乙的记忆")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath(
      `memory-role-active-${testInfo.project.name}.png`,
    ),
    fullPage: true,
  });
  await page.getByRole("link", { name: "群画像", exact: true }).click();
  await expect(page.getByLabel("群选择")).toHaveValue("registered-group");
  await expect(page.getByText("乙的记忆")).toBeVisible();
  await page.getByRole("link", { name: "本人记忆", exact: true }).click();
  await expect(page.getByText("乙的记忆")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("查看哪位角色的记忆")).toHaveValue("actor:b");
  await expect(page.getByText("乙的记忆")).toBeVisible();

  holdBVerification = true;
  const recordsBeforeResume = requests.filter(
    (item) => item.path === "records" && item.role === "actor:b",
  ).length;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect.poll(() => bVerifications).toBe(1);
  await expect(page.getByText("正在重新核验角色记忆")).toBeVisible();
  await expect(page.getByText("乙的记忆")).toHaveCount(0);
  releaseHeldVerification();
  await expect(page.getByText("乙的记忆")).toBeVisible();
  await expect
    .poll(
      () =>
        requests.filter(
          (item) => item.path === "records" && item.role === "actor:b",
        ).length,
    )
    .toBeGreaterThan(recordsBeforeResume);

  revokeB = true;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect.poll(() => bVerifications).toBe(2);
  await expect(page.getByText("正在重新核验角色记忆")).toBeVisible();
  await expect(page.getByText("乙的记忆")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath(
      `memory-role-resume-pending-${testInfo.project.name}.png`,
    ),
    fullPage: true,
  });
  releaseHeldVerification();
  await expect(
    page.getByRole("heading", { name: "该角色暂不可读取" }),
  ).toBeVisible();
  await expect(page.getByText("乙的记忆")).toHaveCount(0);
  holdBVerification = false;
  revokeB = false;
  await page.getByRole("button", { name: "刷新角色" }).click();
  await expect(page.getByText("乙的记忆")).toBeVisible();

  await page.goto("/#/people");
  delayA = true;
  await page.getByLabel("查看哪位角色的记忆").selectOption("actor:a");
  await expect
    .poll(
      () =>
        requests.filter(
          (item) => item.path === "records" && item.role === "actor:a",
        ).length,
    )
    .toBeGreaterThan(1);
  await page.getByLabel("查看哪位角色的记忆").selectOption("actor:b");
  await expect(page.getByText("乙的记忆")).toBeVisible();
  await page.waitForTimeout(700);
  await expect(page.getByText("乙的记忆")).toBeVisible();

  currentRoles = [roles[0]];
  await page.getByRole("button", { name: "刷新角色" }).click();
  await expect(page.getByText("原角色已不在授权列表").last()).toBeVisible();
  await expect(page.getByText("乙的记忆")).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath(`memory-role-${testInfo.project.name}.png`),
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect
    .poll(() =>
      page.evaluate(() =>
        sessionStorage.getItem("tianshu-memory-role:memory-role-owner"),
      ),
    )
    .toBeNull();
});
