import { expect, test } from "@playwright/test";

// The server tests remove the installation's bootstrap from directory responses.
// This checks empty selection and old browser preferences across its consumers.
test("empty role directories stay blank without actor business requests", async ({
  page,
}) => {
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (cause) => errors.push(cause.message));
  await page.addInitScript(() => {
    for (const kind of ["life", "memory", "skills"])
      sessionStorage.setItem(
        `tianshu-${kind}-role:empty-roles-owner`,
        "actor:household",
      );
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/",
      "",
    );
    const body =
      route.request().method() === "POST" ? route.request().postDataJSON() : {};
    requests.push({ path, body });
    const peer = { configured: true, verified_at: null, code: "ok" };
    let result: unknown = {};
    if (path === "session")
      result = {
        authenticated: true,
        csrf: "empty-roles-fixture",
        username: "empty-roles-owner",
        conversations: [{ id: "web", label: "Private", actors: [] }],
        dialogue: { available: true, code: "ready", model: "configured" },
      };
    else if (path === "life/state")
      result = { available: true, code: "ready", peer };
    else if (path === "life/actors")
      result = {
        schema_version: 1,
        fictional: true,
        items: [],
        next_after_actor_id: null,
      };
    else if (path === "memory/state")
      result = {
        available: true,
        code: "ready",
        peer,
        actor_id: null,
        roles: [],
      };
    else if (path === "qq-admin/profiles")
      result = { items: [], next_cursor: null };
    else if (path === "bot-observation/view")
      result = { available: true, unlocked: true, connections: [] };
    await route.fulfill({ json: result });
  });

  await page.goto("/#/roles/chat");
  await expect(page.getByLabel("角色", { exact: true })).toHaveValue("");
  await expect(
    page.getByLabel("角色", { exact: true }).locator("option"),
  ).toHaveText([""]);
  await page.goto("/#/roles/life");
  await expect(
    page.getByRole("heading", { name: "没有可读角色" }),
  ).toBeVisible();
  await expect(page.getByLabel("选择角色")).toHaveValue("");
  await expect(page.getByLabel("选择角色")).toBeDisabled();
  await page.goto("/#/settings/skills");
  await expect(
    page.getByRole("heading", { name: "没有可管理角色" }),
  ).toBeVisible();
  await expect(page.getByLabel("选择角色")).toHaveValue("");
  await expect(page.getByLabel("选择角色")).toBeDisabled();
  await page.goto("/#/settings/weather");
  await expect(page.getByLabel("选择角色")).toHaveValue("");
  await expect(page.getByLabel("选择角色")).toBeDisabled();
  await expect(page.getByLabel("和风天气 API Host")).toHaveCount(0);
  await page.goto("/#/people");
  await expect(page.getByLabel("查看角色")).toHaveValue("");
  await expect(page.getByLabel("查看角色").locator("option")).toHaveText([""]);
  await page.goto("/#/people");
  await expect(page.getByLabel("查看哪位角色的记忆")).toHaveValue("");
  await expect(
    page.getByLabel("查看哪位角色的记忆").locator("option"),
  ).toHaveText([""]);
  await page.goto("/#/roles/room");
  await expect(page.getByLabel("小屋角色")).toHaveValue("");
  await expect(page.getByLabel("小屋角色").locator("option")).toHaveText([""]);

  expect(
    requests.filter(({ body }) => body.actor_id || body.role_id || body.actor),
  ).toEqual([]);
  expect(
    requests.filter(({ path }) =>
      ["snapshot", "skills/read", "life/image-backend/read"].includes(path),
    ),
  ).toEqual([]);
  expect(errors).toEqual([]);
});
