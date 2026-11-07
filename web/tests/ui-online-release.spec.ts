import { expect, test } from "@playwright/test";
import {
  UI_BUILD_ID,
  evaluateUiVersionChange,
  publicAssetUrl,
} from "../src/app/uiRelease";
import { answer, apiPath, nativeSession } from "./native-fixture";

test("publicAssetUrl and evaluateUiVersionChange follow the frozen release contract", () => {
  expect(publicAssetUrl("room-illustration-assets/day.png", "/")).toBe(
    "/room-illustration-assets/day.png",
  );
  expect(publicAssetUrl("/life-evening.png", "/")).toBe("/life-evening.png");
  expect(
    publicAssetUrl(
      "room-illustration-assets/day.png",
      "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/",
    ),
  ).toBe(
    "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/room-illustration-assets/day.png",
  );
  expect(
    publicAssetUrl(
      "/life-evening.png",
      "/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBe("/_ui/releases/ui-20261108t010000z-aaaaaaaaaaaa/life-evening.png");

  // Same build_id -> no notice
  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t010000z-aaaaaaaaaaaa",
        served_mode: "external",
        compatible: true,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBeNull();

  // Incompatible release -> no notice
  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t020000z-bbbbbbbbbbbb",
        served_mode: "external",
        compatible: false,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBeNull();

  // Different compatible external release -> returns new build_id
  expect(
    evaluateUiVersionChange(
      {
        schema_version: "tianshu.ui.status.v1",
        served_build_id: "ui-20261108t020000z-bbbbbbbbbbbb",
        served_mode: "external",
        compatible: true,
      },
      "ui-20261108t010000z-aaaaaaaaaaaa",
    ),
  ).toBe("ui-20261108t020000z-bbbbbbbbbbbb");
});

test("version monitor shows non-blocking notice on new release, preserves input and CommandMonitor unknown state, and clears on rollback", async ({
  page,
}) => {
  await nativeSession(page);
  let servedBuildId = UI_BUILD_ID;
  let versionStatus = 200;

  await page.route("/**/_ui/version", async (route) => {
    if (versionStatus !== 200) {
      return route.fulfill({ status: versionStatus, body: "error" });
    }
    return answer(route, {
      schema_version: "tianshu.ui.status.v1",
      served_build_id: servedBuildId,
      served_source_sha: "b".repeat(40),
      served_mode: "external",
      requested_build_id: servedBuildId,
      activation_sequence: 1,
      compatible: true,
      degraded: false,
      degraded_reason: null,
      contract_id: "tianshu.web_api.v1",
      contract_version: 1,
    });
  });

  const incident = {
    incident_id: "inc-1",
    dependency: "model-broker",
    error_family: "broker_reload_pending",
    version: 1,
    severity: "warning",
    state: "open",
    revision: 1,
    occurrences: 1,
    first_seen: "2026-11-08T00:00:00Z",
    last_seen: "2026-11-08T00:01:00Z",
    resolved_at: null,
    summary: "测试事件",
    affected: [],
    diagnostic_state: "pending",
    recovery_action: "model_configuration_refresh",
    recovery_lineage: null,
    last_observation: null,
  };
  await page.route(apiPath("operations/incidents"), (route) =>
    answer(route, {
      items: [incident],
      next_cursor: null,
      coverage: { complete: true },
    }),
  );
  await page.route(apiPath("operations/incidents/get"), (route) =>
    answer(route, {
      ...incident,
      diagnostics: [],
      recoveries: [],
      notifications: [],
    }),
  );
  await page.route(apiPath("operations/settings"), (route) =>
    answer(route, {
      revision: 1,
      value: {
        diagnosis: { primary_route_id: null, backup_route_id: null },
        notifications: { qq: null, smtp: null },
      },
      diagnosis_configured: false,
      notification_configured: { qq: false, smtp: false },
      smtp_credential_configured: null,
    }),
  );

  let writeCount = 0;
  await page.route(apiPath("operations/recover"), async (route) => {
    writeCount += 1;
    return route.abort("failed");
  });

  await page.goto("/#/system/operations?incident_id=inc-1");
  await expect(page.locator("html")).toHaveAttribute(
    "data-ui-build-id",
    UI_BUILD_ID,
  );
  await expect(page.getByLabel("界面版本更新提示")).toHaveCount(0);

  // Fill an input and trigger an unknown command outcome
  const input = page.getByLabel("原材料 ID（可选）");
  await input.fill("keep-my-unsent-input");
  await page
    .getByRole("button", { name: "请求 model_configuration_refresh" })
    .click();
  await expect(
    page.getByLabel("原操作状态").getByText("有一项提交尚未确认"),
  ).toBeVisible();
  expect(writeCount).toBe(1);

  // Switch server to new UI build B and trigger visibility/focus recheck
  servedBuildId = "ui-20261108t020000z-bbbbbbbbbbbb";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));

  const notice = page.getByLabel("界面版本更新提示");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("ui-20261108t020000z-bbbbbbbbbbbb");

  // Page did NOT auto-reload, compiled build_id on html did NOT get spoofed, input and CommandMonitor unknown state remain intact, no write replayed
  await expect(page.locator("html")).toHaveAttribute(
    "data-ui-build-id",
    UI_BUILD_ID,
  );
  await expect(input).toHaveValue("keep-my-unsent-input");
  await expect(
    page.getByLabel("原操作状态").getByText("有一项提交尚未确认"),
  ).toBeVisible();
  expect(writeCount).toBe(1);

  // Server error on /_ui/version does not break the page
  versionStatus = 500;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(input).toHaveValue("keep-my-unsent-input");

  // Server rolls back to original UI_BUILD_ID -> notice disappears automatically
  versionStatus = 200;
  servedBuildId = UI_BUILD_ID;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(notice).toHaveCount(0);
});
