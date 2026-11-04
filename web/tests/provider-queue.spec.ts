import { test, expect } from "@playwright/test";
import type { QuotaGroup } from "../src/features/settings/providerTypes";
import { answer, apiPath, command, nativeSession } from "./native-fixture";

test("quota queue expiry stays optional and saves an explicit limit or null", async ({
  page,
}) => {
  await nativeSession(page);
  let quotas: QuotaGroup[] = [];
  const writes: Record<string, unknown>[] = [];
  const catalog = () => ({
    providers: [],
    routes: [],
    quota_groups: quotas,
    bindings: [],
    functions: [],
    protocols: [],
    embedding_profile: null,
  });
  await page.route(apiPath("models/catalog"), (route) => answer(route, catalog()));
  await page.route(apiPath("models/quota-groups"), async (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    quotas = [{ ...body, revision: writes.length }];
    await answer(route, command(body, catalog(), writes.length));
  });
  await page.goto("/#/settings/models");
  await expect(
    page.getByText("尚无模型供应商，先添加连接和共享容量组。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "共享账号容量组", exact: true }).click();
  const quotaDialog = page.getByRole("dialog", {
    name: "共享账号容量组",
    exact: true,
  });
  const queue = quotaDialog.getByRole("spinbutton", {
    name: "排队过期秒数（可留空）",
  });
  await expect(queue).toHaveValue("");
  await expect(queue).toHaveJSProperty("required", false);
  await expect(
    page.getByText(/模型执行超时从获得额度并开始执行后计算/),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "容量组标识" }).fill("synthetic-quota");
  await page.getByRole("button", { name: "保存容量组", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].max_wait_seconds).toBeNull();

  // The saved author value remains visible, and clearing it removes only the
  // optional queue expiry rather than silently reinstating a short default.
  await expect(
    page.getByRole("textbox", { name: "容量组标识" }),
  ).toBeEditable({ editable: false });
  await queue.fill("75");
  await page.getByRole("button", { name: "保存容量组", exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].max_wait_seconds).toBe(75);
  await page.reload();
  await page.getByRole("button", { name: "共享账号容量组", exact: true }).click();
  await quotaDialog
    .getByRole("combobox", { name: "容量组", exact: true })
    .selectOption("synthetic-quota");
  await expect(queue).toHaveValue("75");
  await queue.clear();
  await page.getByRole("button", { name: "保存容量组", exact: true }).click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2].max_wait_seconds).toBeNull();
  await expect(queue).toHaveValue("");
});

test("model binding saves ordered fallback routes with the current revision", async ({ page }) => {
  await nativeSession(page);
  const provider = { provider_id: "p", name: "测试供应商", base_url: "https://example.test", protocol: "openai.chat", enabled: true, revision: 1 };
  const route = (id: string) => ({ route_id: id, provider_id: "p", model: id, quota_group: id, enabled: true, revision: 1, verified_capabilities: ["text"], verification: null, parameters: {} });
  const catalog = { providers: [provider], routes: [route("primary"), route("backup-a"), route("backup-b")], quota_groups: [],
    bindings: [{ function: "chat", service_class: "interactive", actor_id: "", route_id: "primary", fallback_routes: ["backup-a"], revision: 3 }],
    functions: ["chat"], protocols: ["openai.chat"], embedding_profile: null };
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("models/catalog"), (route) => answer(route, catalog));
  await page.route(apiPath("models/bindings"), (route) => {
    const body = route.request().postDataJSON(); writes.push(body);
    return answer(route, command(body, catalog, 4));
  });
  await page.goto("/#/settings/models");
  const binding = page.getByRole("heading", { name: "对话", exact: true }).locator("..");
  await expect(binding.getByLabel("备用路由 1")).toHaveValue("backup-a");
  await binding.getByRole("button", { name: "添加备用路由" }).click();
  await binding.getByRole("button", { name: "上移" }).nth(1).click();
  await binding.getByRole("button", { name: "保存绑定" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ function: "chat", route_id: "primary", fallback_routes: ["backup-b", "backup-a"], expected_revision: 3 });
});
