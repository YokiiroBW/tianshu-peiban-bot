import { test, expect } from "@playwright/test";
import { answer, apiPath, emptyPage, nativeSession } from "./native-fixture";

test("conversation loads a turn's recorded selection on demand and keeps unrecorded distinct from empty", async ({ page }) => {
  await nativeSession(page);
  await page.route(apiPath("conversation/messages"), (route) => answer(route, { ...emptyPage, items: [
    { id: 1, turn_id: "old-turn", speaker: "assistant", parts: [{ kind: "text", text: "旧回答" }], created_at: "2026-11-04T00:00:00Z" },
    { id: 2, turn_id: "new-turn", speaker: "assistant", parts: [{ kind: "text", text: "新回答" }], created_at: "2026-11-04T00:01:00Z" },
  ] }));
  const reads: string[] = [];
  const sourceReads: Record<string, unknown>[] = [];
  await page.route(apiPath("sources/read"), (route) => {
    sourceReads.push(route.request().postDataJSON());
    return answer(route, { content: "已授权原文", representation: "text", coverage: { complete: true, missing: [] }, next_position: null, media: null });
  });
  await page.route(apiPath("conversation/turn"), (route) => {
    const body = route.request().postDataJSON(); reads.push(body.turn_id);
    return answer(route, { turn_id: body.turn_id, state: "completed", revision: 1, receipt: null, missing: [], action_ids: [], expression_id: null, error_code: null,
      context_selection: body.turn_id === "old-turn" ? null : [], token_usage: null, provider_usage: null,
      flow: body.turn_id === "old-turn" ? null : { input: { state: "accepted" }, coverage: "recorded", rounds: [{ round: 0, attempt_id: "attempt-1", accepted: { state: "accepted" }, parsed: { state: "parsed" }, applied: { state: "not_applicable" }, expression: { state: "pending" }, delivered: { state: "unknown" } }] },
      input_status: body.turn_id === "old-turn" ? null : [
        { input_id: "input-1", event_id: "event-1", event_revision: 1, context: { selection: "selected", attempts: [{ attempt_id: "attempt-1", round: 0, dispatch: "submitted", selected: true }] }, learning: { state: "reviewed", revision_state: "unresolved" }, reply_compliance: "not_recorded" },
        { input_id: "input-2", event_id: "event-2", event_revision: 2, context: { selection: "not_recorded", attempts: [] }, learning: { state: "reviewed", revision_state: "applied", episode_id: "episode-2", episode_revision: 3, source_refs: [{ source_id: "source-2", revision: 2 }], revision_evidence: [{ rule_id: "rule-1", revision: 3, semantic_revision: 2, action: "revise", proposal_id: "proposal-1", attempt_id: "attempt-2" }] }, reply_compliance: "not_recorded" },
      ] });
  });
  await page.goto("/#/roles/chat");
  const old = page.locator("article.chat-message").filter({ hasText: "旧回答" });
  const recent = page.locator("article.chat-message").filter({ hasText: "新回答" });
  await expect(old).toBeVisible();
  expect(reads).toHaveLength(0);
  await old.getByText("查看本轮材料与状态").click();
  await expect(old).toContainText("本轮未记录选材清单");
  await expect(old).toContainText("历史记录没有处理阶段明细");
  await expect(old).toContainText("历史记录未保存输入与学习状态");
  await recent.getByText("查看本轮材料与状态").click();
  await expect(recent).toContainText("本轮记录的选材清单为空");
  await expect(recent).toContainText("业务应用：不适用");
  await expect(recent).toContainText("投递：未知");
  const status = recent.getByLabel("本轮输入与学习状态");
  await expect(status).toContainText("已选入上下文");
  await expect(status).toContainText("这条输入已进入该轮冻结请求");
  await expect(status).toContainText("原请求已提交模型");
  await expect(status).toContainText("已审阅 · 修订结果未决");
  await expect(status).toContainText("持久修订已应用");
  await expect(status).toContainText("规则修订依据");
  await expect(status).toContainText("proposal-1");
  await expect(status).toContainText("回复是否遵守：未记录");
  await status.getByText("学习状态记录").last().click();
  await status.getByText(/查看原文依据/).click();
  await expect(status).toContainText("已授权原文");
  expect(sourceReads[0]).toMatchObject({ actor_id: "actor", person_id: "person", conversation_id: "web-conversation", source_ref: { source_id: "source-2", revision: 2 } });
  expect(reads).toEqual(["old-turn", "new-turn"]);
});

test("a completed turn refreshes pending learning and stops when the result is known", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-11-05T00:00:00Z") });
  await nativeSession(page);
  await page.route(apiPath("conversation/messages"), (route) => answer(route, { ...emptyPage, items: [
    { id: 1, turn_id: "turn-1", speaker: "assistant", parts: [{ kind: "text", text: "完成的回答" }], created_at: "2026-11-05T00:00:00Z" },
  ] }));
  let reads = 0;
  await page.route(apiPath("conversation/turn"), (route) => {
    reads += 1;
    return answer(route, { turn_id: "turn-1", state: "completed", revision: 1, receipt: null, missing: [], action_ids: [], expression_id: null, error_code: null,
      input_status: [{ input_id: "input-1", event_id: "event-1", event_revision: 1,
        context: { selection: "selected", attempts: [{ attempt_id: "attempt-1", round: 0, dispatch: "submitted", selected: true }] },
        learning: { state: reads === 1 ? "waiting" : "reviewed", revision_state: reads === 1 ? "not_recorded" : "applied",
          revision_evidence: reads === 1 ? [] : [{ rule_id: "rule-1", revision: 2, semantic_revision: 2, action: "revise", proposal_id: "proposal-1", attempt_id: "attempt-1" }] }, reply_compliance: "not_recorded" }] });
  });
  await page.goto("/#/roles/chat");
  await page.getByText("查看本轮材料与状态").click();
  const status = page.getByLabel("本轮输入与学习状态");
  await expect(status).toContainText("学习处理：等待处理");
  await page.clock.fastForward(15000);
  await expect(status).toContainText("持久修订已应用");
  expect(reads).toBe(2);
  await page.clock.fastForward(30000);
  expect(reads).toBe(2);
});
