import { expect, test } from "@playwright/test";
import { answer, apiPath, nativeSession } from "./native-fixture";

test("a generation change discards stale state without replaying a command", async ({ page }) => {
  await nativeSession(page);
  let generation = "generation-one";
  const writes: { generation: string | undefined; body: Record<string, unknown> }[] = [];
  const incident = {
    incident_id: "old-incident", dependency: "model-broker", error_family: "broker_reload_pending",
    version: 2, severity: "warning", state: "open", revision: 4, occurrences: 2,
    first_seen: "2026-11-04T00:00:00Z", last_seen: "2026-11-04T00:02:00Z", resolved_at: null,
    summary: "原来的事件", affected: [], diagnostic_state: "pending",
    recovery_action: "model_configuration_refresh", recovery_lineage: null, last_observation: null,
  };
  await page.route(apiPath("session"), (route) => answer(route, {
    authenticated: true, initialized: true, setup_token_required: false,
    principal: { principal_id: "author", display_name: "测试作者", permissions: ["*"] },
    csrf_token: "synthetic-csrf", data_generation: generation,
  }));
  await page.route(apiPath("operations/incidents"), (route) => answer(route, {
    items: generation === "generation-one" ? [incident] : [], next_cursor: null, coverage: { complete: true },
  }));
  await page.route(apiPath("operations/incidents/get"), (route) => answer(route, {
    ...incident, diagnostics: [], recoveries: [], notifications: [],
  }));
  await page.route(apiPath("operations/settings"), (route) => answer(route, {
    revision: 1, value: { diagnosis: { primary_route_id: null, backup_route_id: null }, notifications: { qq: null, smtp: null } },
    diagnosis_configured: false, notification_configured: { qq: false, smtp: false }, smtp_credential_configured: null,
  }));
  await page.route(apiPath("operations/recover"), (route) => {
    writes.push({ generation: route.request().headers()["x-tianshu-data-generation"], body: route.request().postDataJSON() });
    generation = "generation-two";
    return route.fulfill({
      status: 409, headers: { "X-Tianshu-Data-Generation": generation },
      json: { error: { code: "data_generation_changed", message: "数据已切换", details: null, request_id: "request-1" } },
    });
  });

  await page.goto("/#/system/operations?incident_id=old-incident");
  const details = page.getByLabel("运维事件详情");
  await expect(details).toContainText("原来的事件");
  await details.getByLabel("原材料 ID（可选）").fill("old-draft");
  const appearance = await page.evaluate(() => {
    sessionStorage.setItem("tianshu-infra-control:generation-one:author:example", "old-receipt");
    return localStorage.getItem("tianshu.appearance");
  });
  await details.getByRole("button", { name: "请求 model_configuration_refresh" }).click();

  await expect(page.getByRole("status").filter({ hasText: "数据已切换" })).toBeVisible();
  await expect(page).toHaveURL(/#\/system\/operations$/);
  await expect(page.getByLabel("运维事件详情")).toHaveCount(0);
  await expect(page.getByText("当前范围没有运维事件。")).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ generation: "generation-one", body: { incident_id: "old-incident", expected_revision: 4 } });
  expect(await page.evaluate(() => sessionStorage.getItem("tianshu-infra-control:generation-one:author:example"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("tianshu.appearance"))).toBe(appearance);
});
