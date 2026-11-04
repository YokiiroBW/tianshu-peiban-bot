import { test, expect } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";

test("growth exposes current-rule history, revision and explicit example withdrawal", async ({ page }) => {
  await nativeSession(page);
  const rule = { id: "rule-1", revision: 4, kind: "style", title: "表达习惯", content: "先说明结论", state: "active", occurred_at: "2026-11-04T00:00:00Z", reason: null, evidence: [], can_modify: true, conditions: {}, counterexamples: [] };
  await page.route(apiPath("companion/growth/view"), (route) => answer(route, { review: null, can_review: true, items: [rule], reflections: [], next_cursor: null, missing: [] }));
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  await page.route(apiPath("companion/growth/history"), (route) => answer(route, { id: rule.id, current_state: "active", current_reason: null,
    revisions: [{ rule_id: rule.id, revision: 3, semantic_revision: 2, action: "revise", snapshot: { content: "旧版本" }, source_refs: [], proposal_id: null, attempt_id: null, created_at: "2026-11-03T00:00:00Z" }] }));
  await page.route(apiPath("companion/growth/examples/view"), (route) => answer(route, { items: [{ id: "example-1", rule_id: rule.id, rule_revision: 4, semantic_revision: 3, turn_id: "turn", expression_id: "expression", input_message_ids: [11], output_message_ids: [12], state: "active", reason: null, revision: 2, exposures: 3, delivered_uses: 1, source_refs: [], created_at: "2026-11-04T00:00:00Z" }], next_cursor: null, missing: [] }));
  for (const path of ["companion/growth/revise", "companion/growth/examples/reject"]) await page.route(apiPath(path), (route) => {
    const body = route.request().postDataJSON(); requests.push({ path, body });
    return answer(route, command(body, { id: body.id, state: "active", semantic_revision: 4 }, 5));
  });
  await page.goto("/#/roles/growth");
  await page.getByText("规则历史与依据").click();
  await expect(page.getByText(/旧版本/)).toBeVisible();
  await page.getByRole("button", { name: "修订这项变化" }).click();
  await page.getByRole("textbox", { name: "正文", exact: true }).fill("先讲结论，再说明依据");
  await page.getByRole("button", { name: "保存修订" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toMatchObject({ path: "companion/growth/revise", body: { id: "rule-1", expected_revision: 4, content: "先讲结论，再说明依据" } });
  const examples = page.getByLabel("表达样例与使用记录");
  await expect(examples).toContainText("进入上下文 3 次 · 已投递 1 次");
  await examples.getByRole("button", { name: "撤回此表达样例" }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toMatchObject({ path: "companion/growth/examples/reject", body: { id: "example-1", expected_revision: 2 } });
});
