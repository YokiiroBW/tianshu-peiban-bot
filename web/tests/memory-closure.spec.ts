import { test, expect } from "@playwright/test";
import {
  answer,
  command,
  coverage,
  emptyPage,
  nativeSession,
  conversation,
  state,
  apiPath,
} from "./native-fixture";

const claim = {
  claim_id: "claim",
  revision: 1,
  head_revision: 3,
  actor_id: "actor",
  subject_id: "person",
  speaker_id: "person",
  predicate: "preference",
  value: "周末散步",
  assertion: "self_confirmed",
  polarity: true,
  conditions: { weather: "晴天" },
  valid_from: null,
  valid_until: null,
  observed_at: "2026-09-01T00:00:00Z",
  recorded_at: null,
  state: "active",
  current_status: "historical",
  source_refs: [],
  supersedes: [],
  contradictions: [],
  memory_kind: "fact",
  change_kind: "correct",
  change_reason: "用户更正了日期",
  relations: [],
};
const unit = {
  kind: "claim",
  id: "claim",
  revision: 2,
  head_revision: 3,
  subject_id: "person",
  speaker_id: "person",
  assertion: "self_confirmed",
  polarity: true,
  content: "历史版本二",
  conditions: { weather: "晴天" },
  occurred_at: "2026-09-01T00:00:00Z",
  recorded_at: null,
  valid_from: null,
  valid_until: null,
  matter_id: null,
  state: "active",
  current_status: "historical",
  source_refs: [],
  coverage,
  relations: [],
  change_reason: "日期更正",
};

test.beforeEach(async ({ page }) => nativeSession(page));

test("memory status separates committed material from missing context and unknown indexing", async ({
  page,
}) => {
  const pending = {
    batch_id: "batch",
    status: "pending",
    target_results: [],
    unresolved: [],
  };
  let indexing: {
    state: string;
    objects: {
      kind: string;
      id: string;
      revision: number;
      state?: string;
      profile_key?: string | null;
      generation?: string | null;
    }[];
  } = {
    state: "unknown",
    objects: [{ kind: "claim", id: "claim-1", revision: 1 }],
  };
  await page.route(apiPath("memory/episodes"), (route) =>
    answer(route, {
      ...emptyPage,
      watermark: "1",
      items: [
        {
          episode_id: "episode",
          revision: 1,
          event_id: "event",
          origin_kind: "actor",
          occurred_at: "2026-11-04T00:00:00Z",
          summary: "一条经历",
          source_refs: [],
          actual_result: null,
          coverage,
          extraction_state: "pending",
          consolidation: pending,
        },
      ],
    }),
  );
  await page.route(apiPath("memory/extraction/status"), (route) =>
    answer(route, {
      ...pending,
      status: "applied",
      revision: 1,
      commit_state: "applied",
      required_context_missing: ["原图缺失"],
      indexing,
    }),
  );
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "经历与反思", exact: true }).click();
  await page
    .getByRole("button", { name: "查看经历与记忆处理", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "读取最新处理状态" }).click();
  await expect(
    page.getByText("提交状态：记忆已应用。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("必要上下文仍有 1 项缺失")).toBeVisible();
  await expect(page.getByText("索引状态：尚未确认")).toBeVisible();
  indexing = {
    ...indexing,
    state: "pending",
    objects: [
      {
        ...indexing.objects[0],
        state: "pending",
        profile_key: "profile-1",
        generation: "generation-1",
      },
    ],
  };
  await page.getByRole("button", { name: "读取最新处理状态" }).click();
  await page.getByText("索引状态：等待索引").click();
  await expect(
    page.getByText("记忆已应用，部分对象仍在等待索引。"),
  ).toBeVisible();
  await expect(
    page.getByText(/已关联索引档案 · 数据代际 generation-1/),
  ).toBeVisible();
});

test("reflections use the companion authority with scoped time and cursor", async ({
  page,
}) => {
  const reads: Record<string, unknown>[] = [];
  let oldReads = 0;
  await page.route(apiPath("memory/reflections"), (route) => {
    oldReads++;
    return answer(route, emptyPage);
  });
  await page.route(apiPath("companion/growth/reflections"), (route) => {
    const body = route.request().postDataJSON();
    reads.push(body);
    return answer(route, {
      items: [
        {
          proposal_id: body.cursor ? "old" : "current",
          revision: 1,
          content: body.cursor ? "更早的反思" : "当前反思",
          kind: "reflection",
          occurred_at: "2026-11-04T00:00:00Z",
          recorded_at: "2026-11-04T00:01:00Z",
          source_refs: [],
          conditions: {},
          counterexamples: [],
          interpretation: true,
          state: "active",
        },
      ],
      next_cursor: body.cursor ? null : "next-reflection",
      coverage,
    });
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "经历与反思", exact: true }).click();
  await page.getByRole("button", { name: "反思与推测", exact: true }).click();
  await expect(page.getByText("当前反思", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一批", exact: true }).click();
  await expect(page.getByText("更早的反思", { exact: true })).toBeVisible();
  expect(reads[0]).toMatchObject({
    actor_id: "actor",
    time_mode: "current",
    cursor: null,
  });
  expect(reads[1]).toMatchObject({
    actor_id: "actor",
    time_mode: "current",
    cursor: "next-reflection",
  });
  expect(oldReads).toBe(0);
});

test("time queries and bounded history keep corrections on the latest claim head", async ({
  page,
}) => {
  const reads: Record<string, unknown>[] = [];
  const writes: Record<string, unknown>[] = [];
  const historyCursors: unknown[] = [];
  await page.route(apiPath("memory/claims"), (route) => {
    const body = route.request().postDataJSON();
    expect(new URL(route.request().url()).searchParams.get("actor_id")).toBe(
      body.actor_id,
    );
    reads.push(body);
    return answer(route, {
      ...emptyPage,
      items: [
        claim,
        {
          ...claim,
          claim_id: "hypothesis",
          memory_kind: "hypothesis",
          assertion: "inferred",
          value: "可修订的推断",
        },
      ],
    });
  });
  await page.route(apiPath("memory/claims/history"), (route) => {
    const body = route.request().postDataJSON();
    historyCursors.push(body.cursor);
    return answer(route, {
      ...emptyPage,
      items: [
        {
          ...unit,
          content: body.cursor ? "最早记录" : unit.content,
          revision: body.cursor ? 1 : 2,
        },
      ],
      next_cursor: body.cursor ? null : "history-page-2",
    });
  });
  await page.route(apiPath("memory/claims/get"), (route) =>
    answer(route, {
      ...claim,
      revision: 3,
      current_status: "current",
      value: "当前认识",
    }),
  );
  await page.route(apiPath("memory/claims/correct"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    return answer(route, command(body, { ...claim, revision: 4 }, 4));
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await expect(
    page.getByRole("heading", { name: "可修订的人物认识", exact: true }),
  ).toBeVisible();
  await page.getByLabel("时间查询").selectOption("known_at");
  await page.getByLabel("已知截止时点").fill("2026-10-01T10:00");
  await page.getByRole("button", { name: "按时间读取", exact: true }).click();
  await expect.poll(() => reads.at(-1)?.time_mode).toBe("known_at");
  expect(reads.at(-1)?.known_at).toBeTruthy();
  const card = page.locator(".memory-record").filter({ hasText: "周末散步" });
  await card
    .getByRole("button", { name: "查看依据与更正", exact: true })
    .click();
  await expect(
    card.getByText("未知（未记录）", { exact: true }).first(),
  ).toBeVisible();
  await card.getByRole("button", { name: "查看修订历史", exact: true }).click();
  await expect(card.getByText("历史版本二", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "下一批修订", exact: true }).click();
  await expect(card.getByText("最早记录", { exact: true })).toBeVisible();
  expect(historyCursors).toEqual([null, "history-page-2"]);
  await card
    .getByRole("button", { name: "读取当前版本并更正", exact: true })
    .click();
  await expect(card.getByLabel("更正后的内容", { exact: true })).toHaveValue(
    "当前认识",
  );
  await card
    .getByLabel("更正后的内容", { exact: true })
    .fill("当前版本的明确修订");
  await card
    .getByRole("button", { name: "保存本次明确更正", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    claim_id: "claim",
    expected_revision: 3,
    value: "当前版本的明确修订",
  });
  await page.getByRole("button", { name: "关闭记忆详情", exact: true }).click();
  await page.getByLabel("时间查询").selectOption("valid_at");
  await page.getByLabel("适用时点", { exact: true }).fill("2026-09-02T10:00");
  await page.getByRole("button", { name: "按时间读取", exact: true }).click();
  await expect.poll(() => reads.at(-1)?.time_mode).toBe("valid_at");
  expect(reads.at(-1)?.valid_at).toBeTruthy();
});

test("matter completion basis and history remain distinct from the current editable head", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const historyCursors: unknown[] = [];
  const matter = {
    matter_id: "repair",
    actor_id: "actor",
    revision: 1,
    head_revision: 4,
    subject_id: "person",
    title: "维修",
    summary: "用户报告维修完成",
    state: "completed",
    due_at: null,
    source_refs: [],
    claim_ids: [],
    conditions: {},
    interpretation: false,
    recorded_at: null,
    completion_basis: { kind: "human_report", source_refs: [] },
    relations: [],
  };
  await page.route(apiPath("memory/matters"), (route) =>
    answer(route, { ...emptyPage, items: [matter] }),
  );
  await page.route(apiPath("memory/matters/history"), (route) => {
    const body = route.request().postDataJSON();
    historyCursors.push(body.cursor);
    return answer(route, {
      ...emptyPage,
      items: [
        {
          ...unit,
          kind: "matter",
          id: "repair",
          state: "completed",
          completion_basis: {
            kind: body.cursor ? "human_report" : "action_receipt",
            receipt_id: body.cursor ? null : "real-receipt",
          },
          content: body.cursor ? "用户报告历史" : "执行回执历史",
        },
      ],
      next_cursor: body.cursor ? null : "matter-history-2",
    });
  });
  await page.route(apiPath("memory/matters/get"), (route) =>
    answer(route, {
      ...matter,
      revision: 4,
      state: "active",
      title: "当前维修事项",
      completion_basis: null,
    }),
  );
  await page.route(apiPath("memory/matters/save"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    return answer(route, command(body, { ...matter, revision: 5 }, 5));
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByLabel("时间查询").selectOption("known_at");
  await page.getByLabel("已知截止时点").fill("2026-10-01T10:00");
  await page.getByRole("button", { name: "按时间读取", exact: true }).click();
  await page.getByRole("button", { name: "事项与约定", exact: true }).click();
  await expect(
    page.getByText("完成依据：人类报告完成；未据此宣称天枢执行成功。", {
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看修订历史", exact: true }).click();
  await expect(
    page.getByText("完成依据：真实动作执行回执。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一批修订", exact: true }).click();
  await expect(page.getByText("用户报告历史", { exact: true })).toBeVisible();
  expect(historyCursors).toEqual([null, "matter-history-2"]);
  await page
    .getByRole("button", { name: "读取当前事项并更正", exact: true })
    .click();
  await expect(page.getByLabel("事项标题", { exact: true })).toHaveValue(
    "当前维修事项",
  );
  await page
    .getByRole("button", { name: "保存这次明确确认", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    expected_revision: 4,
    state: "active",
    title: "当前维修事项",
  });
});

test("episode recovery follows server actions and unknown only observes the original execution", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const pending = {
    batch_id: "original-batch",
    status: "quarantined",
    isolated: true,
    blocks_following: false,
    action: "observe_original_attempt",
    target_results: [],
    unresolved: [],
    fragment_cursor: { source_id: "original", position: 120 },
    coverage: { missing: ["video_understanding_pending"] },
  };
  const episode = {
    episode_id: "pending",
    revision: 2,
    event_id: "event",
    origin_kind: "actor",
    occurred_at: "2026-11-01T00:00:00Z",
    summary: "角色想象的休息",
    source_refs: [],
    actual_result: { kind: "imagined" },
    coverage,
    extraction_state: "unknown",
    consolidation: pending,
  };
  await page.route(apiPath("memory/episodes"), (route) =>
    answer(route, {
      ...emptyPage,
      watermark: "2",
      items: [
        episode,
        {
          ...episode,
          episode_id: "wait",
          summary: "材料缺项",
          extraction_state: "failed",
          consolidation: {
            ...pending,
            status: "failed",
            action: "wait_for_new_evidence",
          },
        },
      ],
    }),
  );
  await page.route(apiPath("memory/extraction/status"), (route) =>
    answer(route, { ...pending, episode_id: "pending", revision: 2 }),
  );
  await page.route(apiPath("memory/extraction/resume"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    return answer(
      route,
      command(
        body,
        {
          action: "observe_original_attempt",
          queued: false,
          batch_id: "original-batch",
        },
        2,
      ),
    );
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "经历与反思", exact: true }).click();
  await page
    .getByRole("button", { name: "查看经历与记忆处理", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("button", { name: "观察原执行", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByText(/原请求结果未知，已隔离，不阻塞后续整理/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "重新准备当前材料", exact: true }),
  ).toHaveCount(0);
  await expect(
    page
      .getByText(
        "角色想象活动：完成记录支持主观反思，不能证明真实身体活动或工具执行成功。",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "观察原执行", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    episode_id: "pending",
    expected_revision: 2,
  });
  await expect(page.getByText(/当前未派发新整理任务/)).toBeVisible();
});

test("retry computation uses the existing resume command and waits for actual results", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const retry = {
    batch_id: "original-retry-batch",
    status: "failed",
    action: "retry_computation",
    target_results: [],
    unresolved: [],
    coverage,
  };
  let releaseResume = () => {};
  const resumeResponse = new Promise<void>((resolve) => {
    releaseResume = resolve;
  });
  await page.route(apiPath("memory/episodes"), (route) =>
    answer(route, {
      ...emptyPage,
      watermark: "7",
      items: [
        {
          episode_id: "retry-computation",
          revision: 7,
          event_id: "retry-event",
          origin_kind: "human",
          occurred_at: "2026-11-03T00:00:00Z",
          summary: "暂时失败后等待继续整理的材料",
          source_refs: [],
          coverage,
          extraction_state: "consolidation_failed",
          consolidation: retry,
        },
      ],
    }),
  );
  await page.route(apiPath("memory/extraction/status"), (route) =>
    answer(route, { ...retry, episode_id: "retry-computation", revision: 7 }),
  );
  await page.route(apiPath("memory/extraction/resume"), async (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    await resumeResponse;
    return answer(
      route,
      command(
        body,
        {
          action: "retry_computation",
          queued: true,
          batch_id: "original-retry-batch",
        },
        7,
      ),
    );
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "经历与反思", exact: true }).click();
  await page
    .getByRole("button", { name: "查看经历与记忆处理", exact: true })
    .first()
    .click();
  const resume = page.getByRole("button", {
    name: "重试记忆整理",
    exact: true,
  });
  await expect(resume).toBeVisible();
  await expect(
    page.getByText(/重新核对材料与权限后安排独立重试/),
  ).toBeVisible();
  expect(writes).toHaveLength(0);
  try {
    await resume.click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toMatchObject({
      actor_id: "actor",
      episode_id: "retry-computation",
      expected_revision: 7,
    });
    await expect(resume).toBeDisabled();
    await expect(
      page.getByText("正在提交并读取回执…", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/本批已提交|已完成本次材料|执行完成/),
    ).toHaveCount(0);
  } finally {
    releaseResume();
  }
  await expect(page.getByText(/已排队，尚未表示整理成功/)).toBeVisible();
  await expect(
    page.getByText(/本批已提交|已完成本次材料|执行完成/),
  ).toHaveCount(0);
  expect(writes).toHaveLength(1);
});

test("a known stale late result holds its original material for explicit refresh", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const held = {
    batch_id: "late-original",
    status: "stale",
    held_for_refresh: true,
    action: "refresh_material",
    target_results: [],
    unresolved: [],
    coverage: { missing: [] },
  };
  await page.route(apiPath("memory/episodes"), (route) =>
    answer(route, {
      ...emptyPage,
      watermark: "2",
      items: [
        {
          episode_id: "held-stale",
          revision: 2,
          event_id: "stale-event",
          origin_kind: "human",
          occurred_at: "2026-11-02T00:00:00Z",
          summary: "迟到前已变化的材料",
          source_refs: [],
          coverage,
          extraction_state: "consolidation_stale",
          consolidation: held,
        },
      ],
    }),
  );
  await page.route(apiPath("memory/extraction/status"), (route) =>
    answer(route, { ...held, episode_id: "held-stale", revision: 2 }),
  );
  await page.route(apiPath("memory/extraction/resume"), (route) => {
    const body = route.request().postDataJSON();
    writes.push(body);
    return answer(
      route,
      command(
        body,
        {
          action: "refresh_material",
          queued: true,
          batch_id: "late-original",
        },
        2,
      ),
    );
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "经历与反思", exact: true }).click();
  await page
    .getByRole("button", { name: "查看经历与记忆处理", exact: true })
    .first()
    .click();
  await expect(
    page.getByText(/迟到结果与当前材料或记忆版本不一致，未写入/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "观察原执行", exact: true }),
  ).toHaveCount(0);
  const refresh = page.getByRole("button", {
    name: "重新准备当前材料",
    exact: true,
  });
  await expect(refresh).toBeVisible();
  expect(writes).toHaveLength(0);
  await refresh.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    episode_id: "held-stale",
    expected_revision: 2,
  });
});

test("group processing shows authorized targets separately from committed no-change results", async ({
  page,
}) => {
  const reads: Record<string, unknown>[] = [];
  const group = {
    ...conversation,
    conversation_id: "group-conversation",
    person_id: null,
    kind: "group",
    target_ref: "qq:group",
    label: "群聊",
  };
  await page.route(apiPath("identity/conversations"), (route) =>
    answer(route, { ...emptyPage, items: [group] }),
  );
  await page.route(apiPath("memory/projection"), (route) =>
    answer(route, {
      person_id: null,
      group_id: group.conversation_id,
      group: { subject_id: "verified-shared-subject" },
      claims: [],
      hypotheses: [],
      matters: [],
      reflections: [],
      coverage,
      group_consolidation: {
        view: "current_processing",
        subjects: [
          { person_id: "one", display_name: "已授权人物甲" },
          { person_id: "two", display_name: "已授权人物乙" },
        ],
        coverage: {
          ...coverage,
          complete: false,
          missing: ["recent_processing_window"],
        },
        items: [
          {
            episode_id: "episode",
            revision: 1,
            batch_id: "shared-batch",
            status: "applied",
            action: "wait_for_new_evidence",
            targets: [{ subject_id: "one" }, { subject_id: "two" }],
            target_results: [
              {
                subject_id: "one",
                status: "no_change",
                processed_material_ids: ["material"],
                operation_ids: [],
              },
            ],
            unresolved: [{ subject_id: "two", reason: "尚缺本人说明" }],
          },
        ],
      },
    }),
  );
  await page.route(apiPath("memory/matters"), (route) => {
    reads.push(route.request().postDataJSON());
    return answer(route, emptyPage);
  });
  await page.goto("/#/groups");
  await page.getByRole("button", { name: "记录与依据", exact: true }).click();
  await page
    .getByRole("button", { name: "查看整理进展与诊断", exact: true })
    .click();
  await expect(
    page.getByText("已处理，无需变化", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("固定目标：已授权人物甲、已授权人物乙", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/尚缺本人说明/)).toBeVisible();
  await page
    .getByRole("button", { name: "关闭群材料处理记录", exact: true })
    .click();
  await page.getByRole("button", { name: "群共同事项", exact: true }).click();
  await expect.poll(() => reads.length).toBe(1);
  expect(reads[0]).toMatchObject({
    subject_id: "verified-shared-subject",
    conversation_id: group.conversation_id,
  });
});

test("index rebuild keeps all five kinds and follows the opaque server cursor", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  await page.route(apiPath("memory/index/rebuild"), (route) => {
    const body = route.request().postDataJSON();
    expect(new URL(route.request().url()).searchParams.get("actor_id")).toBe(
      body.actor_id,
    );
    writes.push(body);
    return answer(
      route,
      command(body, {
        queued: body.cursor ? 1 : 200,
        kinds: body.kinds,
        next_cursor: body.cursor ? null : "i1:opaque-five-kinds",
        coverage: { ...coverage, complete: !!body.cursor },
      }),
    );
  });
  await page.goto("/#/settings/retrieval");
  await expect(
    page.getByRole("heading", { name: "记忆索引重建", exact: true }),
  ).toBeVisible();
  expect(writes).toHaveLength(0);
  await page.getByRole("button", { name: "重建所选索引", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "继续下一批索引", exact: true }),
  ).toBeVisible();
  expect(writes[0].kinds).toEqual([
    "claim",
    "episode",
    "hypothesis",
    "matter",
    "reflection",
  ]);
  await page
    .getByRole("button", { name: "继续下一批索引", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].cursor).toBe("i1:opaque-five-kinds");
  expect(writes[1].kinds).toEqual(writes[0].kinds);
});

test("evidence queries preserve both relation endpoints and distinguish ranked candidates from list pages", async ({
  page,
}) => {
  const reads: Record<string, unknown>[] = [];
  await page.route(apiPath("memory/query"), (route) => {
    const body = route.request().postDataJSON();
    reads.push(body);
    const ranked = body.plan.coverage_mode === "ranked";
    return answer(route, {
      items: [
        {
          ...unit,
          content: body.cursor ? "后续完整证据" : "带分歧的结论",
          relations: body.cursor
            ? []
            : [
                {
                  link_id: "disagreement",
                  relation_kind: "contradicts",
                  state: "active",
                  from_ref: { kind: "claim", id: "one", revision: 1 },
                  to_ref: { kind: "claim", id: "two", revision: 2 },
                  from_evidence: {
                    ...unit,
                    id: "one",
                    content: "原陈述及其条件",
                  },
                  to_evidence: {
                    ...unit,
                    id: "two",
                    revision: 2,
                    content: "相反陈述及其条件",
                    polarity: false,
                    conditions: { season: "winter" },
                  },
                  source_refs: [],
                  valid_from: null,
                  valid_until: null,
                  recorded_at: null,
                },
              ],
        },
      ],
      next_cursor: !ranked && !body.cursor ? "query-list-page-2" : null,
      coverage: ranked
        ? { ...coverage, complete: false, missing: ["ranked_selection"] }
        : coverage,
    });
  });
  await page.goto("/#/memory");
  await page.getByLabel("读取范围").selectOption("actor");
  await page.getByRole("button", { name: "完整证据检索", exact: true }).click();
  await expect(page.getByText("带分歧的结论", { exact: true })).toBeVisible();
  expect((reads[0].plan as Record<string, unknown>).evidence_kinds).toEqual([
    "claim",
    "hypothesis",
    "matter",
    "episode",
    "reflection",
  ]);
  await page.getByText("分歧、替代与关联（1）", { exact: true }).click();
  await expect(page.getByText("原陈述及其条件", { exact: true })).toBeVisible();
  await expect(
    page.getByText("相反陈述及其条件", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "下一批证据", exact: true }).click();
  await expect(page.getByText("后续完整证据", { exact: true })).toBeVisible();
  expect(reads.at(-1)?.cursor).toBe("query-list-page-2");
  await page.getByLabel("读取方式", { exact: true }).selectOption("ranked");
  await page.getByRole("button", { name: "读取证据", exact: true }).click();
  await expect(
    page.getByText("这是相关候选，不能视为完整列表", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "下一批证据", exact: true }),
  ).toHaveCount(0);
  expect(reads.at(-1)?.cursor).toBeNull();
});

test("Growth exposes fixed versions and observes unknown attempts without offering replacement", async ({
  page,
}) => {
  let writes = 0;
  await page.route(apiPath("companion/growth/view"), (route) =>
    answer(route, {
      items: [],
      reflections: [],
      next_cursor: null,
      missing: [],
      can_review: true,
      review: {
        revision: 9,
        last_error: "reflection_outcome_unknown",
        can_request_review: false,
        pending_inputs: 32,
        pending_original_requests: 1,
        unavailable_inputs: 1,
        input_missing: ["source_unavailable"],
        recovery: "observe_original_attempt",
        attempt_id: "original-growth-attempt",
        blocked_material: {
          episode_id: "blocked",
          revision: 7,
          input_budget: 13000,
        },
        batch: {
          material_key: "fixed-material",
          inputs: [["episode-a", 2]],
          has_more: true,
        },
      },
    }),
  );
  await page.route(apiPath("companion/growth/review"), (route) => {
    writes += 1;
    return answer(route, {});
  });
  await page.goto("/#/roles/growth");
  await expect(page.getByText(/待处理经历版本：32/)).toBeVisible();
  await expect(page.getByText(/待观察原请求：1/)).toBeVisible();
  await expect(
    page.getByText("1 条材料当前不可用。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("episode-a · v2", { exact: true })).toBeVisible();
  await expect(
    page.getByText(/原 attempt：original-growth-attempt/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "重新读取材料并整理", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "读取原执行状态", exact: true })
    .click();
  expect(writes).toBe(0);
});

test("media runtime describes PostgreSQL schema bytes and stale worker observations honestly", async ({
  page,
}) => {
  await page.route(apiPath("media/view"), (route) =>
    answer(route, {
      configured: true,
      capabilities: { provider: "bilibili", source_kinds: [] },
      engine: {
        state: "ready",
        version: "1",
        code: "media_worker_observation_stale",
        observed_at: "2026-11-01T00:00:00Z",
        owner_live: false,
        stale: true,
      },
      background: [
        {
          name: "worker_0",
          state: "running",
          code: "media_worker_observation_missing",
          owner_live: false,
          observed_at: null,
        },
      ],
      accounts: [],
      targets: [],
      subscriptions: [],
      jobs: [],
      overview: {
        database: {
          state: "ready",
          engine: "PostgreSQL",
          schema_version: 1,
          query_ms: 2,
          size_bytes: 4096,
          size_scope: "schema",
          size_scope_name: "capability_media",
        },
        storage: { state: "not_configured", volumes: [] },
      },
    }),
  );
  await page.goto("/#/media/runtime");
  await expect(
    page.getByText("媒体数据库 · PostgreSQL", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("媒体 schema（capability_media）的表、索引与 TOAST", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("订阅数据库 · SQLite", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("数据库主文件", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/worker_0 · 工作进程状态未知/)).toBeVisible();
  await expect(
    page.getByText(
      "工作进程观测已过期或原所有者已失活，当前运行状态尚未确认。",
      { exact: true },
    ),
  ).toBeVisible();
});

test("unbound Fish voice is a role readiness state and never saves a profile automatically", async ({
  page,
}) => {
  let writes = 0;
  await page.route(apiPath("speech/view"), (route) =>
    answer(route, {
      connections: [],
      profiles: [],
      dictionaries: [],
      default_route: null,
    }),
  );
  await page.route(apiPath("speech/profiles.save"), (route) => {
    writes += 1;
    return answer(route, {});
  });
  await page.route(apiPath("capabilities/catalog/view"), (route) =>
    answer(route, {
      actor_id: "actor",
      revision: 1,
      sources: [],
      items: [
        {
          definition: {
            id: "speech.synthesize",
            title: "说话",
            description: "角色语音",
            effect: "write",
            version: 1,
            model_tool: null,
            public_command: null,
            long_running: false,
            permissions: [],
          },
          availability: {
            implemented: true,
            enabled: true,
            configured: true,
            provider_supported: null,
            verified_at: null,
            reason: "voice_profile_not_configured",
          },
          can_execute: false,
          enabled: true,
          source_ids: [],
        },
      ],
    }),
  );
  await page.goto("/#/settings/speech");
  const readiness = page.getByRole("region", {
    name: "当前角色语音表达",
    exact: true,
  });
  await expect(readiness.getByText(/角色未绑定音色/)).toBeVisible();
  await expect(
    readiness.getByText(/连接、音色查询和转写可分别查看状态/),
  ).toBeVisible();
  expect(writes).toBe(0);
});

test("weather keeps stale readings and only queries the unresolved original request", async ({
  page,
}) => {
  let observations = 0;
  await page.route(apiPath("life/state"), (route) =>
    answer(route, { ...state, weather_location: "location" }),
  );
  await page.route(apiPath("weather/current"), (route) => {
    observations += 1;
    return answer(route, {
      location: {
        id: "location",
        name: "杭州",
        adm1: "浙江",
        adm2: "杭州",
        country: "中国",
        tz: "Asia/Shanghai",
        utc_offset: "+08:00",
      },
      weather: {
        temp: "22",
        feels_like: "23",
        text: "晴",
        icon: "100",
        wind_scale: "2",
        attributions: [],
        observed_at: "2026-10-31T00:00:00Z",
      },
      observed_at: "2026-10-31T00:00:00Z",
      fetched_at: "2026-10-31T00:10:00Z",
      expires_at: "2026-10-31T00:15:00Z",
      stale: true,
      code: "weather_read_result_unknown",
      recovery: "observe_original_attempt",
      retry_after: "2026-11-01T00:00:00Z",
    });
  });
  await page.goto("/#/roles/life");
  await expect(
    page.getByText("当前展示上次保留的过期读数。", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/原天气读取仍在等待或结果未知/)).toBeVisible();
  await page
    .getByRole("button", { name: "查询原读取状态", exact: true })
    .click();
  await expect.poll(() => observations).toBe(2);
  await expect(
    page.getByRole("link", { name: "天气连接设置", exact: true }),
  ).toHaveAttribute("href", "#/settings/weather");
});
