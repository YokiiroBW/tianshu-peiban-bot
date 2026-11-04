import { test, expect } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";

test("operations records a recovery request without claiming verified recovery", async ({ page }) => {
  await nativeSession(page);
  const incident = { incident_id: "incident-1", dependency: "model-broker", error_family: "broker_reload_pending", version: 2, severity: "warning", state: "open", revision: 4, occurrences: 2, first_seen: "2026-11-04T00:00:00Z", last_seen: "2026-11-04T00:02:00Z", resolved_at: null, summary: "配置待加载", affected: [], diagnostic_state: "pending", recovery_action: "model_configuration_refresh", recovery_lineage: null, last_observation: null };
  await page.route(apiPath("operations/incidents"), (route) => answer(route, { items: [incident], next_cursor: null, coverage: { complete: true } }));
  await page.route(apiPath("operations/incidents/get"), (route) => answer(route, { ...incident, diagnostics: [], recoveries: [], notifications: [] }));
  await page.route(apiPath("operations/settings"), (route) => answer(route, { revision: 1, value: { diagnosis: { primary_route_id: null, backup_route_id: null }, notifications: { qq: null, smtp: null } }, diagnosis_configured: false, notification_configured: { qq: false, smtp: false }, smtp_credential_configured: null }));
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("operations/recover"), (route) => { const body = route.request().postDataJSON(); writes.push(body); return answer(route, command(body, { status: "requested" }, 5)); });
  await page.goto("/#/system/operations?incident_id=incident-1");
  const details = page.getByLabel("运维事件详情");
  await expect(details).toContainText("配置待加载");
  await details.getByLabel("原材料 ID（可选）").fill("original-config");
  await details.getByLabel("材料版本（可选）").fill("2");
  await details.getByRole("button", { name: "请求 model_configuration_refresh" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ incident_id: "incident-1", expected_revision: 4, action: "model_configuration_refresh", target: { material_id: "original-config", version: 2 } });
  await expect(details).toContainText("恢复请求已受理，尚未证明业务恢复");
});

test("SMTP credentials are sent only on explicit replacement and never prefilled", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("operations/incidents"), (route) => answer(route, { items: [], next_cursor: null, coverage: { complete: true } }));
  await page.route(apiPath("operations/settings"), (route) => answer(route, { revision: 8, value: { diagnosis: { primary_route_id: null, backup_route_id: null }, notifications: { qq: null, smtp: null } }, diagnosis_configured: false, notification_configured: { qq: false, smtp: false }, smtp_credential_configured: null }));
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("operations/settings/save"), (route) => { const body = route.request().postDataJSON(); writes.push(body); return answer(route, command(body, { saved: true }, 9)); });
  await page.goto("/#/system/operations");
  await page.getByLabel("配置 SMTP 通知").check();
  await page.getByLabel("SMTP 主机").fill("smtp.example.test");
  await page.getByLabel("发件人").fill("sender@example.test");
  await page.getByLabel("接收邮箱").fill("admin@example.test");
  await page.getByLabel("更换 SMTP 密码（留空保留）").fill("synthetic-password");
  await page.getByRole("button", { name: "保存运维设置" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({ expected_revision: 8, smtp_password: "synthetic-password", value: { notifications: { smtp: { credential_ref: "sealed:operations:smtp" } } } });
});
