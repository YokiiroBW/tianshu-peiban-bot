import { test, expect, type Page, type Route } from "@playwright/test";

// UI-only fixture. The backend and peer services are separately verified by CONNECT-B/M;
// these responses check browser navigation and the visible distinction between states.
async function answer(route: Route, value: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

async function session(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    answer(route, {
      authenticated: true,
      csrf: "synthetic-integration-session",
      username: "fixture",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
}

test("project knowledge browses, pages, reads, searches and distinguishes an empty search", async ({
  page,
}, testInfo) => {
  await session(page);
  const sent: { path: string; body: Record<string, unknown> }[] = [];
  await page.route("**/api/v1/knowledge/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    sent.push({ path, body });
    if (path.endsWith("/state"))
      return answer(route, {
        available: true,
        code: "ready",
        projects: [{ project_id: "project-a", label: "测试项目" }],
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    if (path.endsWith("/documents"))
      return answer(route, {
        project_id: "project-a",
        operation: "documents",
        result: {
          project_id: "project-a",
          project_revision: 3,
          items: body.cursor
            ? [
                {
                  document_id: "doc-b",
                  kind: "url",
                  version: 1,
                  indexed_state: "ready",
                  source_validation: "not_checked",
                },
              ]
            : [
                {
                  document_id: "doc-a",
                  kind: "file",
                  version: 2,
                  indexed_state: "ready",
                  source_validation: "not_checked",
                },
              ],
          next_cursor: body.cursor ? null : "next-page",
          omissions: [],
        },
      });
    if (path.endsWith("/document"))
      return answer(route, {
        project_id: "project-a",
        operation: "document",
        result: {
          project_id: "project-a",
          project_revision: 3,
          document_id: "doc-a",
          version: 2,
          hash: "a".repeat(64),
          blocks: [
            {
              reference: {
                block_id: "block-a",
                document_id: "doc-a",
                version: 2,
                hash: "a".repeat(64),
              },
              text: "可读取的资料正文",
              spans: [],
            },
          ],
          next_cursor: null,
          omissions: [],
        },
      });
    if (path.endsWith("/query"))
      return answer(route, {
        project_id: "project-a",
        operation: "query",
        result: {
          project_id: "project-a",
          blocks: [],
          omissions: [],
          retrieval: "lexical",
          trust: "source_material_not_instructions",
        },
      });
    if (path.endsWith("/notes"))
      return answer(route, {
        project_id: "project-a",
        operation: "notes",
        result: {
          project_id: "project-a",
          notes: [
            {
              note_id: "note-a",
              question: "下一步是什么？",
              version: 1,
              state: "ready",
              current: true,
              citation_states: [],
              source_statements: [],
              inferences: ["先核实来源"],
              open_questions: [],
              decision: { summary: "保持来源核对", basis: [] },
            },
          ],
          omissions: [],
          retrieval: "lexical",
        },
      });
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/resources/knowledge");
  await expect(page.getByRole("heading", { name: "资料目录" })).toBeVisible();
  await expect(page.locator(".knowledge-list li")).toHaveCount(1);
  await expect(page.getByText("本页本次读取成功")).toBeVisible();
  await expect(page.getByText("尚无真实读取记录。")).toHaveCount(0);
  await page.getByRole("button", { name: "继续读取目录" }).click();
  await expect(page.locator(".knowledge-list li")).toHaveCount(2);
  await page.locator(".knowledge-list button").first().click();
  await expect(page.getByText("可读取的资料正文")).toBeVisible();
  await page.getByRole("button", { name: "资料搜索" }).click();
  await page.getByRole("searchbox", { name: "关键词" }).fill("不存在");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "没有匹配资料" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "研究笔记" }).click();
  await page.getByRole("searchbox", { name: "关键词" }).fill("下一步");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "下一步是什么？" }),
  ).toBeVisible();
  await expect(page.getByText("保持来源核对")).toBeVisible();
  expect(
    sent.every((item) => !JSON.stringify(item.body).includes("token")),
  ).toBe(true);
  expect(
    sent.find((item) => item.path.endsWith("/document"))?.body,
  ).toMatchObject({
    document_id: "doc-a",
    expected_revision: 2,
    expected_hash: null,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("knowledge.png"),
    fullPage: true,
  });
});

for (const mixed of [false, true]) {
  test(`life reads last persisted state and only opens a published diary (${mixed ? "mixed" : "legacy"})`, async ({
    page,
  }) => {
    const provenance = mixed
      ? {
          kind: "mixed",
          source_kinds: ["digital_experience", "interpretation", "fictional"],
          selection_complete: false,
          timezone: "Asia/Shanghai",
          occurred_from: "2026-09-26T16:00:00Z",
          occurred_before: "2026-09-27T16:00:00Z",
        }
      : undefined;
    await session(page);
    await page.route("**/api/v1/weather/current", (route) =>
      answer(route, {
        revision: 0,
        can_manage: false,
        configured: false,
        credential_configured: false,
        host: null,
        location: null,
        server_time: 1790471000,
        weather: null,
        fetched_at: null,
        stale: false,
        code: "weather_not_configured",
      }),
    );
    await page.route("**/api/v1/life/*", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/state"))
        return answer(route, {
          available: true,
          code: "ready",
          peer: { configured: true, verified_at: null, code: "unverified" },
        });
      if (path.endsWith("/actors"))
        return answer(route, {
          schema_version: 1,
          fictional: true,
          items: [
            {
              actor_id: "actor-a",
              actor_version: 2,
              world_id: "world",
              room_id: "room",
            },
          ],
          next_after_actor_id: null,
        });
      if (path.endsWith("/today"))
        return answer(route, {
          schema_version: 1,
          fictional: true,
          actor_id: "actor-a",
          day: "2026-09-27",
          timezone: "Asia/Shanghai",
          enabled: true,
          observed_at: 1790471000,
          state_basis: "last_persisted",
          plan: {
            plan_id: "plan-a",
            version: 1,
            state: "active",
            generation_state: "unavailable",
            generated_by: "baseline",
            entries: [
              {
                phase_id: "phase-a",
                minute: 0,
                activity: "安静休息",
                detail: null,
                state: "current",
                generation_state: "unavailable",
              },
            ],
            current_phase_id: "phase-a",
          },
        });
      if (path.endsWith("/timeline"))
        return answer(route, {
          schema_version: 1,
          fictional: true,
          actor_id: "actor-a",
          day: "2026-09-27",
          state_basis: "last_persisted",
          items: [],
          next_after: null,
        });
      if (path.endsWith("/snapshot"))
        return answer(route, {
          schema_version: 1,
          fictional: true,
          actor_id: "actor-a",
          actor_version: 2,
          world_id: "world",
          room_id: "room",
          timezone: "Asia/Shanghai",
          activity: null,
          mood: "平静",
          outfit_ref: null,
          changed_at: 1790470000,
          observed_at: 1790471000,
          state_basis: "last_persisted",
        });
      if (path.endsWith("/diaries"))
        return answer(route, {
          schema_version: 1,
          fictional: !mixed,
          actor_id: "actor-a",
          items: [
            {
              diary_id: "diary-a",
              actor_id: "actor-a",
              day: "2026-09-27",
              state: "draft",
              version: 4,
              published_revision_id: "revision-2",
              fictional: !mixed,
              provenance,
              captured: {
                recipe_id: "daily",
                recipe_version: 1,
                material_version: "v1",
              },
            },
          ],
          next_after: null,
        });
      if (path.endsWith("/revision"))
        return answer(route, {
          schema_version: 1,
          fictional: !mixed,
          actor_id: "actor-a",
          diary_id: "diary-a",
          diary_version: 4,
          revision_id: "revision-2",
          content: "这是一篇已发布的日记。",
          provenance,
          created_at: 1790470000,
          captured: {
            recipe_id: "daily",
            recipe_version: 1,
            material_version: "v1",
            config_version: 3,
          },
        });
      throw new Error(`unexpected ${path}`);
    });
    await page.goto("/#/roles/life");
    await expect(page.getByLabel("选择角色")).toHaveValue("actor-a");
    await page.locator("#life-diaries > summary").click();
    await expect(
      page.getByRole("heading", { name: "已发布日记", exact: true }),
    ).toBeVisible();
    await page.locator(".life-list button").click();
    await expect(page.getByText("这是一篇已发布的日记。")).toBeVisible();
    await expect(page.locator(".life-revision")).toContainText("2026-09-27");
    await expect(page.locator(".life-revision")).toContainText("虚构剧情");
    if (mixed) {
      await expect(page.locator(".life-revision")).toContainText(
        "实际数字经历 · 主观理解 · 虚构剧情",
      );
      await expect(page.locator(".life-revision")).toContainText(
        "材料未完整读取",
      );
      await expect(page.locator(".life-revision")).toContainText(
        "2026-09-26T16:00:00Z",
      );
    }
  });
}

test("memory separates shared group portraits and own records", async ({
  page,
}) => {
  await session(page);
  const bodyOfRecords: unknown[] = [];
  await page.route("**/api/v1/memory/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/state"))
      return answer(route, {
        available: true,
        code: "ready",
        peer: { configured: true, verified_at: null, code: "unverified" },
        actor_id: "actor-a",
        roles: [
          {
            id: "actor-a",
            label: "默认角色",
            version: 0,
            available: true,
            reason: null,
          },
        ],
      });
    if (path.endsWith("/overview"))
      return answer(route, {
        schema_version: 1,
        verified_at: "2026-09-27T00:00:00Z",
        scope_version: 2,
        memory_group_count: 4,
        counts_truncated: false,
      });
    if (path.endsWith("/subjects"))
      return answer(route, {
        schema_version: 1,
        verified_at: "2026-09-27T00:00:00Z",
        scope_version: 2,
        items: [
          {
            subject: { kind: "group", conversation_id: "group-a" },
            categories: ["偏好"],
            group_count: 1,
            group_count_truncated: false,
          },
        ],
        next_cursor: null,
      });
    if (path.endsWith("/records")) {
      const body = route.request().postDataJSON();
      bodyOfRecords.push(body);
      return answer(route, {
        schema_version: 1,
        verified_at: "2026-09-27T00:00:00Z",
        scope_version: 2,
        items: [
          {
            semantic_group_id: "group-a",
            category: "偏好",
            field_key: "drink",
            item_key: "tea",
            units: [
              {
                record_id: "record-a",
                record_version: 1,
                statement: body.subject ? "共享的喜好" : "本人记得的事",
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
      });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/groups");
  await expect(page.locator(".memory-list button")).toHaveCount(1);
  await page.locator(".memory-list button").click();
  await expect(page.getByText("共享的喜好")).toBeVisible();
  await page.getByRole("link", { name: "本人记忆", exact: true }).click();
  await expect(page.getByText("本人记得的事")).toBeVisible();
  expect(bodyOfRecords).toEqual(
    expect.arrayContaining([
      {
        role_id: "actor-a",
        role_version: 0,
        subject: { kind: "group", conversation_id: "group-a" },
        limit: 20,
        cursor: null,
      },
      {
        role_id: "actor-a",
        role_version: 0,
        subject: null,
        limit: 20,
        cursor: null,
      },
    ]),
  );
});

test("missing deployment and forbidden reader are not shown as empty content", async ({
  page,
}) => {
  await session(page);
  await page.route("**/api/v1/knowledge/state", (route) =>
    answer(route, {
      available: false,
      code: "knowledge_not_configured",
      projects: [],
      peer: { configured: false, verified_at: null, code: "unverified" },
    }),
  );
  await page.route("**/api/v1/life/state", (route) =>
    answer(route, {
      available: false,
      code: "life_read_required",
      peer: { configured: true, verified_at: null, code: "unverified" },
    }),
  );
  await page.goto("/#/resources/knowledge");
  await expect(
    page.getByRole("heading", { name: "项目知识尚未配置" }),
  ).toBeVisible();
  await page.goto("/#/roles/life");
  await expect(
    page.getByRole("heading", { name: "当前账号未获角色生活读取权限" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "还没有已发布日记" }),
  ).toHaveCount(0);
});

test("external HA setup saves a scoped target then performs one read-only check", async ({
  page,
}) => {
  await session(page);
  let unlocked = false;
  let revision = 4;
  let configured = false;
  let lastTest: {
    state: string;
    code: string;
    checked_at: string;
    revision: number;
  } | null = null;
  const saves: Record<string, unknown>[] = [];
  let tests = 0;
  await page.route("**/api/v1/access/view", (route) =>
    answer(route, {
      available: false,
      active: { mode: "http", origin: "", certificate: null },
      saved: { mode: "http", origin: "", certificate: null },
      revision: 1,
      restart_required: false,
      certificates: [],
      listener_port: 80,
    }),
  );
  await page.route("**/api/v1/connections/view", (route) =>
    answer(route, { connections: [] }),
  );
  await page.route("**/api/v1/external/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (path.endsWith("/view"))
      return answer(route, {
        revision,
        unlocked,
        assets: {
          configured: false,
          enabled: false,
          url: null,
          credential_configured: false,
          ca_configured: false,
          last_test: null,
        },
        home: {
          configured,
          enabled: configured,
          url: configured ? "http://192.168.1.15:8123" : null,
          allow_private_http: configured,
          entities: configured
            ? [
                {
                  entity_id: "sensor.living_room_temperature",
                  label: "客厅温度",
                  kind: "sensor",
                },
              ]
            : [],
          credential_configured: configured,
          ca_configured: false,
          last_test: lastTest,
        },
      });
    if (path.endsWith("/unlock")) {
      unlocked = true;
      return answer(route, { unlocked: true, expires_in: 900 });
    }
    if (path.endsWith("/save")) {
      saves.push(body);
      revision++;
      configured = true;
      lastTest = null;
      return answer(route, {
        revision,
        state: "saved_unverified",
        applied: true,
        kind: "home",
      });
    }
    if (path.endsWith("/test")) {
      tests++;
      lastTest = {
        state: "connected",
        code: "ok",
        checked_at: "2026-09-27T00:00:00Z",
        revision,
      };
      return answer(route, { kind: "home", ...lastTest });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/settings/home");
  await page.getByLabel("管理员密码（二次验证）").fill("fixture-password");
  await page.getByRole("button", { name: "解锁连接管理" }).click();
  await page.getByLabel("启用此连接").check();
  await page
    .getByLabel("Home Assistant 基础地址")
    .fill("http://192.168.1.15:8123");
  await page.getByLabel("明确允许经部署审查的局域网 HTTP").check();
  await page.getByRole("button", { name: "添加实体" }).click();
  await page.getByLabel("实体 ID").fill("sensor.living_room_temperature");
  await page.getByLabel("显示名称").fill("客厅温度");
  await page.getByRole("radio", { name: "替换凭据" }).check();
  await page.getByLabel("新凭据").fill("fixture-ha-key-0123456789-example");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(
    page.getByText("连接设置已保存并生效，尚未验证业务读取。", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(page.getByText("尚未检测", { exact: true })).toBeVisible();
  expect(saves).toHaveLength(1);
  expect(saves[0]).toMatchObject({
    kind: "home",
    expected_revision: 4,
    value: {
      base_url: "http://192.168.1.15:8123",
      allow_private_http: true,
      entities: [
        {
          entity_id: "sensor.living_room_temperature",
          label: "客厅温度",
          kind: "sensor",
        },
      ],
    },
    credential: {
      action: "replace",
      value: "fixture-ha-key-0123456789-example",
    },
    ca: { action: "keep" },
  });
  expect(JSON.stringify(saves[0])).not.toContain("token_env");
  await page.getByRole("button", { name: "检测连接（只读一次）" }).click();
  await expect(
    page.getByText("一次真实只读检测已通过。", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("已真实读取", { exact: true })).toBeVisible();
  expect(tests).toBe(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("project lessons, reviewed experience and explicit continuation stay scoped", async ({
  page,
}, testInfo) => {
  await session(page);
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  let checks = 0;
  await page.route("**/api/v1/knowledge/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    calls.push({ path, body });
    if (path.endsWith("/state"))
      return answer(route, {
        available: true,
        code: "ready",
        projects: [
          {
            project_id: "project-a",
            label: "测试项目",
            checkouts: [{ id: "checkout-a", label: "主任务检出" }],
          },
        ],
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    if (path.endsWith("/lessons"))
      return answer(route, {
        project_id: "project-a",
        operation: "lessons",
        result: {
          lessons: [
            {
              lesson_id: "lesson-a",
              version: 2,
              trigger: "重复回执",
              symptom: "重复执行",
              cause: "未核请求号",
              correction: "先核请求号",
              verification: "夹具复核通过",
              scope: { platform: "平台", language: "Python", framework: "无" },
              evidence: [{ block_id: "b1" }],
            },
          ],
          omissions: [],
          retrieval: "lexical",
          trust: "operator_statement_with_source_evidence",
        },
      });
    if (path.endsWith("/experiences"))
      return answer(route, {
        project_id: "project-a",
        operation: "experiences",
        result: {
          entries: [
            {
              entry_id: "entry-a",
              version: 1,
              title: "回执处理准则",
              rule: "先检查请求号",
              applicability: ["重复提交"],
              excludes: [],
              counterexamples: [],
              recheck_after: null,
              evidence: "protected",
            },
          ],
          omissions: [],
          retrieval: "lexical",
          trust: "approved_operator_rule_with_protected_citations",
        },
      });
    if (path.endsWith("/continuation"))
      return answer(route, {
        project_id: "project-a",
        operation: "continuation",
        result: {
          handle: "opaque-session-handle",
          expires_in: 900,
          status: "recovered",
          checkout: {
            id: "checkout-a",
            branch: "codex/test",
            head: "a".repeat(40),
            dirty: false,
            collected_at: "2026-09-27T00:00:00Z",
          },
          index: { total: 3, listed: 3, truncated: false },
          state: {
            version: 2,
            current: true,
            stale_evidence: [],
            goal: "完成只读接入",
            constraints: ["使用登记检出"],
            unfinished: ["现场核对"],
          },
          omissions: [],
          budget: { limit_bytes: 16384, used_bytes: 1000, over_budget: false },
          revision: 5,
        },
      });
    if (path.endsWith("/continuation-check")) {
      checks++;
      if (checks === 2)
        return answer(route, { code: "continuation_handle_expired" }, 409);
      return answer(route, {
        project_id: "project-a",
        operation: "continuation-check",
        result: {
          valid: false,
          reason: "worktree_changed",
          differences: ["worktree_changed"],
          observed: true,
          checkout_id: "checkout-a",
          checked_at: "2026-09-27T00:01:00Z",
        },
      });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/projects/experience");
  await expect(
    page.getByRole("heading", { name: "检索项目错题" }),
  ).toBeVisible();
  expect(calls.map((call) => call.path)).toEqual(["/api/v1/knowledge/state"]);
  await page.getByLabel("检索关键词").fill("回执");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByRole("heading", { name: "重复回执" })).toBeVisible();
  await page.getByRole("button", { name: "已审阅经验" }).click();
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(
    page.getByRole("heading", { name: "回执处理准则" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "交接快照" }).click();
  expect(
    calls.filter((call) => call.path.includes("continuation")),
  ).toHaveLength(0);
  await page.getByLabel("已登记 checkout").selectOption("checkout-a");
  await page.getByLabel("交接线索关键词").fill("回执");
  await page.getByRole("button", { name: "读取此 checkout 的交接" }).click();
  await expect(page.getByText("完成只读接入")).toBeVisible();
  expect(calls.at(-1)).toMatchObject({
    path: "/api/v1/knowledge/continuation",
    body: {
      project_id: "project-a",
      checkout_id: "checkout-a",
      text: "回执",
      budget_bytes: 16384,
    },
  });
  expect(await page.locator("body").innerText()).not.toContain(
    "opaque-session-handle",
  );
  await page.getByRole("button", { name: "核对当前状态（只读一次）" }).click();
  await expect(page.getByText("当前状态有差异或无法确认")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("experience.png"),
    fullPage: true,
  });
  expect(calls.at(-1)).toMatchObject({
    path: "/api/v1/knowledge/continuation-check",
    body: { project_id: "project-a", handle: "opaque-session-handle" },
  });
  await page.getByRole("button", { name: "核对当前状态（只读一次）" }).click();
  await expect(
    page.getByText("交接快照的核对时限已过", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "交接快照 · checkout-a" }),
  ).toHaveCount(0);
  expect(JSON.stringify(calls)).not.toContain("C:\\");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("project experience distinguishes a disabled read from empty results and no checkout", async ({
  page,
}) => {
  await session(page);
  await page.route("**/api/v1/knowledge/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/state"))
      return answer(route, {
        available: true,
        code: "ready",
        projects: [
          { project_id: "project-a", label: "测试项目", checkouts: [] },
        ],
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    if (path.endsWith("/lessons"))
      return answer(route, { code: "knowledge_operation_not_enabled" }, 503);
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/projects/experience");
  await page.getByLabel("检索关键词").fill("回执");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(
    page.getByText("此项项目知识读取尚未由部署端单独启用", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "本次关键词没有匹配条目" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "交接快照" }).click();
  await expect(
    page.getByRole("heading", { name: "此项目未登记可读取的 checkout" }),
  ).toBeVisible();
});
