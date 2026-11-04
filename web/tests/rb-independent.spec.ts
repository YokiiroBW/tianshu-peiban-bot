import { test, expect } from "@playwright/test";
import { answer, apiPath, command, emptyPage, nativeSession } from "./native-fixture";

test("independent projects are available without a selected character or conversation", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("companion/roles/list"), (route) => answer(route, emptyPage));
  const reads: Record<string, unknown>[] = [];
  const card = { project_id: "p", conversation_id: null, label: "个人项目", revision: 1, content_revision: 0, enabled: true };
  await page.route(apiPath("projects/view"), (route) => {
    reads.push(route.request().postDataJSON());
    return answer(route, { ...emptyPage, items: [card], can_manage: true, can_write: true, can_share: true, allowed_roots: ["/projects"] });
  });
  await page.route(apiPath("projects/registry/get"), (route) => answer(route, { ...card, actor_id: null, root: "/projects/p", directories: [], allowed_urls: [], worktrees: [], scope_ids: ["system-1"] }));
  await page.route(apiPath("projects/documents"), (route) => answer(route, emptyPage));
  await page.goto("/#/projects/knowledge");
  await expect(page.getByRole("option", { name: "个人项目" })).toBeAttached();
  expect(reads[0]).toMatchObject({ actor_id: null, person_id: null, conversation_id: null });
  await page.getByRole("button", { name: "登记项目" }).click();
  await expect(page.getByRole("button", { name: "保存登记" })).toBeEnabled();
  await expect(page.getByText("请先在角色工作区选择项目所属的真实会话范围。")).toHaveCount(0);
});

test("workspace plugin execution uses the selected authorized binding without a character", async ({ page }) => {
  await nativeSession(page);
  const action = { id: "lookup", title: "查找", effect: "read", input_schema: { type: "object", properties: {} }, output_schema: {}, resumable: false, availability: { available: true, missing_capabilities: [] } };
  const binding = { kind: "binding", schema_version: 1, binding_id: "workspace-binding", instance_id: "instance", revision: 1, enabled: true, priority: 0, overrides: {}, scope: { kind: "workspace", workspace_id: "system-1" }, entries: [{ kind: "action", id: "lookup" }] };
  const instance = { id: "instance", name: "实例", package_id: "example", version: "1", revision: 1, desired_state: "enabled", state: "ready", problem: null, config: {}, config_schema: { properties: {} }, secret_configured: {}, bindings: [binding], binding_overrides: [], missing_capabilities: [], contributions: { actions: [action], views: [] } };
  await page.route(apiPath("plugins/view"), (route) => answer(route, { packages: [{ id: "package", manifest: { id: "example", name: "插件", version: "1", description: "", author: "", capabilities: { required: [], optional: [] } }, digest: "x", installed: true, source: {} }], instances: [instance], catalog: [], installations: [], capabilities: [], notifications: [] }));
  for (const path of ["plugins/invocations", "plugins/logs", "plugins/schedules"]) await page.route(apiPath(path), (route) => answer(route, { items: [] }));
  let invoked: Record<string, unknown> | null = null;
  await page.route(apiPath("plugins/invoke"), (route) => { const body = route.request().postDataJSON(); invoked = body; return answer(route, command(body, { id: "call" })); });
  await page.goto("/#/plugins");
  await page.getByRole("button", { name: /实例/ }).click();
  await page.getByLabel("使用范围").selectOption("workspace-binding");
  await page.getByRole("button", { name: "执行", exact: true }).click();
  await expect.poll(() => invoked).not.toBeNull();
  expect(invoked).toMatchObject({ actor_id: null, conversation_id: null, binding_id: "workspace-binding", action: "lookup" });
});
