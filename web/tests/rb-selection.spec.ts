import { test, expect } from "@playwright/test";
import { answer, apiPath, emptyPage, nativeSession } from "./native-fixture";

test("conversation loads a turn's recorded selection on demand and keeps unrecorded distinct from empty", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("conversation/messages"), (route) => answer(route, { ...emptyPage, items: [
    { id: 1, turn_id: "old-turn", speaker: "assistant", parts: [{ kind: "text", text: "旧回答" }], created_at: "2026-11-04T00:00:00Z" },
    { id: 2, turn_id: "new-turn", speaker: "assistant", parts: [{ kind: "text", text: "新回答" }], created_at: "2026-11-04T00:01:00Z" },
  ] }));
  const reads: string[] = [];
  await page.route(apiPath("conversation/turn"), (route) => {
    const body = route.request().postDataJSON(); reads.push(body.turn_id);
    return answer(route, { turn_id: body.turn_id, state: "completed", revision: 1, receipt: null, missing: [], action_ids: [], expression_id: null, error_code: null,
      context_selection: body.turn_id === "old-turn" ? null : [], token_usage: null, provider_usage: null,
      flow: body.turn_id === "old-turn" ? null : { input: { state: "accepted" }, coverage: "recorded", rounds: [{ round: 0, attempt_id: "attempt-1", accepted: { state: "accepted" }, parsed: { state: "parsed" }, applied: { state: "not_applicable" }, expression: { state: "pending" }, delivered: { state: "unknown" } }] } });
  });
  await page.goto("/#/roles/chat");
  const old = page.locator("article.chat-message").filter({ hasText: "旧回答" });
  const recent = page.locator("article.chat-message").filter({ hasText: "新回答" });
  await expect(old).toBeVisible();
  expect(reads).toHaveLength(0);
  await old.getByText("查看本轮材料与状态").click();
  await expect(old).toContainText("本轮未记录选材清单");
  await expect(old).toContainText("历史记录没有处理阶段明细");
  await recent.getByText("查看本轮材料与状态").click();
  await expect(recent).toContainText("本轮记录的选材清单为空");
  await expect(recent).toContainText("业务应用：不适用");
  await expect(recent).toContainText("投递：未知");
  expect(reads).toEqual(["old-turn", "new-turn"]);
});
