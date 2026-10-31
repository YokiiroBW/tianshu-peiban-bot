import { expect, test, type Page, type Route } from "@playwright/test";

async function answer(route: Route, value: unknown) {
  try {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(value),
    });
  } catch {
    // A page may abort the held request when its project or actor changes.
  }
}

async function session(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    answer(route, {
      authenticated: true,
      csrf: "synthetic-scope-session",
      username: "fixture",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
}

test("changing projects clears an in-flight document from the old project", async ({
  page,
}) => {
  await session(page);
  let release!: () => void;
  let markStarted!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  const started = new Promise<void>((resolve) => (markStarted = resolve));
  await page.route("**/api/v1/knowledge/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    if (path.endsWith("/state"))
      return answer(route, {
        available: true,
        code: "ready",
        projects: [
          { project_id: "project-a", label: "项目 A" },
          { project_id: "project-b", label: "项目 B" },
        ],
        peer: { configured: true, verified_at: null, code: "unverified" },
      });
    if (path.endsWith("/documents"))
      return answer(route, {
        project_id: body.project_id,
        operation: "documents",
        result: {
          project_id: body.project_id,
          project_revision: 1,
          items: [
            {
              document_id: body.project_id === "project-a" ? "doc-a" : "doc-b",
              kind: "file",
              version: 1,
              indexed_state: "ready",
              source_validation: "not_checked",
            },
          ],
          next_cursor: null,
          omissions: [],
        },
      });
    if (path.endsWith("/document")) {
      markStarted();
      await held;
      return answer(route, {
        project_id: "project-a",
        operation: "document",
        result: {
          project_id: "project-a",
          project_revision: 1,
          document_id: "doc-a",
          version: 1,
          hash: "a".repeat(64),
          blocks: [
            {
              text: "仅属于项目 A 的正文",
              reference: {
                block_id: "a",
                document_id: "doc-a",
                version: 1,
                hash: "a".repeat(64),
              },
              spans: [],
            },
          ],
          next_cursor: null,
          omissions: [],
        },
      });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/projects/knowledge");
  await expect(page.locator(".knowledge-list button")).toHaveCount(1);
  await page.locator(".knowledge-list button").click();
  await started;
  await page.getByLabel("选择项目").selectOption("project-b");
  await expect(page.getByText("doc-b")).toBeVisible();
  release();
  await expect(page.getByText("仅属于项目 A 的正文")).toHaveCount(0);
  await expect(page.getByText("doc-a")).toHaveCount(0);
});

test("changing actors clears an in-flight published revision from the old actor", async ({
  page,
}) => {
  await session(page);
  let release!: () => void;
  let markStarted!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  const started = new Promise<void>((resolve) => (markStarted = resolve));
  await page.route("**/api/v1/weather/current", (route) =>
    answer(route, {
      revision: 0,
      can_manage: true,
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
  await page.route("**/api/v1/life/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Record<string, unknown>;
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
        items: ["actor-a", "actor-b"].map((actor_id) => ({
          actor_id,
          actor_version: 1,
          world_id: "world",
          room_id: "room",
        })),
        next_after_actor_id: null,
      });
    if (path.endsWith("/snapshot"))
      return answer(route, {
        schema_version: 1,
        fictional: true,
        actor_id: body.actor_id,
        actor_version: 1,
        world_id: "world",
        room_id: "room",
        timezone: "Asia/Shanghai",
        activity: null,
        mood: body.actor_id === "actor-a" ? "平静" : "愉快",
        outfit_ref: null,
        changed_at: 1790470000,
        observed_at: 1790471000,
        state_basis: "last_persisted",
      });
    if (path.endsWith("/today"))
      return answer(route, {
        schema_version: 1,
        fictional: true,
        actor_id: body.actor_id,
        day: "2026-09-27",
        timezone: "Asia/Shanghai",
        enabled: true,
        observed_at: 1790471000,
        state_basis: "last_persisted",
        plan: {
          plan_id: `plan:${body.actor_id}`,
          version: 1,
          state: "active",
          generation_state: "completed",
          generated_by: "gateway",
          current_phase_id: null,
          entries: [],
        },
      });
    if (path.endsWith("/timeline"))
      return answer(route, {
        schema_version: 1,
        fictional: true,
        actor_id: body.actor_id,
        day: body.day,
        state_basis: "last_persisted",
        items: [],
        next_after: null,
      });
    if (path.endsWith("/diaries"))
      return answer(route, {
        schema_version: 1,
        fictional: true,
        actor_id: body.actor_id,
        items:
          body.actor_id === "actor-a"
            ? [
                {
                  diary_id: "diary-a",
                  actor_id: "actor-a",
                  day: "2026-09-27",
                  state: "published",
                  version: 1,
                  published_revision_id: "revision-a",
                  captured: {
                    recipe_id: "daily",
                    recipe_version: 1,
                    material_version: "v1",
                  },
                },
              ]
            : [],
        next_after: null,
      });
    if (path.endsWith("/revision")) {
      markStarted();
      await held;
      return answer(route, {
        schema_version: 1,
        fictional: true,
        actor_id: "actor-a",
        diary_id: "diary-a",
        diary_version: 1,
        revision_id: "revision-a",
        content: "仅属于角色 A 的日记正文",
        created_at: 1790470000,
        captured: {
          recipe_id: "daily",
          recipe_version: 1,
          material_version: "v1",
          config_version: 1,
        },
      });
    }
    throw new Error(`unexpected ${path}`);
  });
  await page.goto("/#/roles/life");
  await expect(page.locator(".life-list button")).toHaveCount(1);
  await page.locator("#life-diaries > summary").click();
  await page.locator(".life-list button").click();
  await started;
  await page.getByLabel("选择角色").selectOption("actor-b");
  await expect(page.getByText("愉快", { exact: true }).first()).toBeVisible();
  release();
  await expect(page.getByText("仅属于角色 A 的日记正文")).toHaveCount(0);
  await expect(page.locator(".life-list button")).toHaveCount(0);
});
