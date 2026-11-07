import { test, expect, type Page } from "@playwright/test";
import {
  answer,
  apiPath,
  command,
  coverage,
  emptyPage,
  nativeSession,
  conversation,
} from "./native-fixture";

const person = { person_id: "alice", display_name: "小明", revision: 1 };
const group = {
  ...conversation,
  conversation_id: "group-a",
  person_id: null,
  kind: "group",
  label: "猫咪讨论群",
  adapter: "qq",
  target_ref: "group:123",
};
const claim = {
  claim_id: "c",
  revision: 1,
  actor_id: "actor",
  subject_id: "group:group-a",
  speaker_id: "alice",
  predicate: "language.term_meaning",
  value: { term: "猫粮", meaning: "群里分享的猫照片" },
  assertion: "inferred",
  polarity: true,
  conditions: {},
  valid_from: null,
  valid_until: null,
  observed_at: null,
  recorded_at: null,
  state: "active",
  current_status: "current",
  source_refs: [],
  supersedes: [],
  contradictions: [],
  memory_kind: "hypothesis",
  relations: [],
};
const activity = {
  days: 7,
  timezone: "Asia/Shanghai",
  observed_at: "2026-11-07T04:00:00Z",
  message_count: 12,
  participant_count: 2,
  coverage,
  daily: [
    { date: "2026-11-06", count: 4 },
    { date: "2026-11-07", count: 8 },
  ],
  hourly: [{ hour: 12, count: 12 }],
  topics: [
    { id: "cat", label: "晒猫", count: 5, authors: 2, message_ids: ["1"] },
  ],
  topic_links: [],
  people: [
    { id: "alice", label: "小明", count: 6, message_ids: ["1"] },
    { id: "bob", label: "小红", count: 5, message_ids: [] },
  ],
  interactions: [{ from: "alice", to: "bob", count: 3 }],
  messages: {
    "1": {
      id: "1",
      text: "分享一张猫照片",
      author: "小明",
      created_at: "2026-11-07T04:00:00Z",
      source_refs: [],
    },
  },
};

async function session(page: Page) {
  await nativeSession(page);
  await page.route(apiPath("companion/roles/list"), (route) =>
    answer(route, {
      ...emptyPage,
      items: [
        { actor_id: "actor", name: "智乃", enabled: true },
        { actor_id: "other", name: "澄汐", enabled: true },
      ],
    }),
  );
  await page.route(apiPath("identity/directory"), (route) =>
    answer(route, {
      connections: [
        {
          connection_id: "qq",
          actor_id: "actor",
          namespace: "qq",
          self_external_id: "bot",
          enabled: true,
          revision: 1,
        },
      ],
      accounts: [],
      scopes: [],
      principals: [],
      conversation_links: [
        {
          connection_id: "qq",
          external_id: "group:123",
          conversation_id: group.conversation_id,
          mode: "reply",
          mention_required: true,
          reply_owner: "tianshu",
          reply_keywords: ["天枢"],
          revision: 4,
        },
      ],
    }),
  );
}

test("profiles follow the visible role and keep experience diagnostics inside the card", async ({
  page,
}) => {
  await session(page);
  const reads: string[] = [];
  await page.route(apiPath("identity/people"), (route) => {
    const actor = new URL(route.request().url()).searchParams.get("actor_id");
    expect(actor).toBeTruthy();
    reads.push(actor!);
    return answer(route, {
      ...emptyPage,
      items: [
        actor === "actor"
          ? person
          : { ...person, person_id: "bob", display_name: "小红" },
      ],
    });
  });
  await page.route(apiPath("memory/episodes"), (route) =>
    answer(route, {
      ...emptyPage,
      items: [
        {
          episode_id: "episode",
          revision: 1,
          origin_kind: "human",
          summary: "一起讨论了新养的小猫",
          person_id: "alice",
          speaker_id: "alice",
          occurred_at: "2026-11-07T04:00:00Z",
          recorded_at: "2026-11-07T04:00:00Z",
          source_refs: [],
          coverage,
          extraction_state: "applied",
          actual_result: null,
          consolidation: {
            batch_id: "batch",
            status: "applied",
            commit_state: "applied",
            target_results: [],
            unresolved: [],
          },
        },
      ],
    }),
  );
  await page.goto("/#/people");
  await expect(
    page.getByRole("heading", { name: "小明", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "相遇与反思", exact: true }).click();
  await expect(
    page.getByText("一起讨论了新养的小猫", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("提交状态：记忆已应用。", { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "查看经历与记忆处理", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "经历详情", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("提交状态：记忆已应用。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭经历详情", exact: true }).click();
  await page.getByLabel("当前角色", { exact: true }).selectOption("other");
  await expect(
    page.getByRole("heading", { name: "小红", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "小明", exact: true }),
  ).toHaveCount(0);
  expect(reads).toEqual(["actor", "other"]);
});

test("group heat topics slang impressions and interactions stay with the selected role", async ({
  page,
}, info) => {
  await session(page);
  await page.route(apiPath("identity/conversations"), (route) => {
    const actor = new URL(route.request().url()).searchParams.get("actor_id");
    return answer(route, {
      ...emptyPage,
      items: [
        actor === "other"
          ? {
              ...group,
              actor_id: "other",
              conversation_id: "group-b",
              label: "游戏群",
            }
          : group,
      ],
    });
  });
  await page.route(apiPath("conversation/group-insights"), (route) => {
    const body = route.request().postDataJSON();
    expect(body).toMatchObject(
      body.actor_id === "other"
        ? { actor_id: "other", conversation_id: "group-b" }
        : { actor_id: "actor", conversation_id: "group-a" },
    );
    return answer(route, { ...activity, days: body.days });
  });
  await page.route(apiPath("memory/projection"), (route) =>
    answer(route, {
      group: { subject_id: "group:group-a" },
      claims: [],
      hypotheses: [],
      matters: [],
      reflections: [],
      coverage,
      group_consolidation: { items: [], subjects: [], coverage },
    }),
  );
  const claims: Record<string, unknown>[] = [];
  await page.route(apiPath("memory/claims"), (route) => {
    const body = route.request().postDataJSON();
    claims.push(body);
    return answer(route, {
      ...emptyPage,
      items: [
        body.predicate
          ? claim
          : {
              ...claim,
              predicate: "group.impression",
              value: "大家常分享养猫经验",
            },
      ],
    });
  });
  await page.goto("/#/groups");
  await expect(
    page.getByRole("heading", { name: "群热度", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "晒猫，5 条消息，查看依据", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "晒猫", exact: true }),
  ).toContainText("分享一张猫照片");
  await page.getByRole("button", { name: "关闭晒猫", exact: true }).click();
  await page.getByRole("button", { name: "群黑话", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "猫粮", exact: true }),
  ).toBeVisible();
  expect(claims.at(-1)).toMatchObject({
    actor_id: "actor",
    conversation_id: "group-a",
    subject_id: "group:group-a",
    predicate: "language.term_meaning",
  });
  await page.getByRole("button", { name: "群印象", exact: true }).click();
  await expect(
    page.getByText("大家常分享养猫经验", { exact: true }),
  ).toBeVisible();
  expect(claims.at(-1)).toMatchObject({
    exclude_predicate: "language.term_meaning",
  });
  await page.getByRole("button", { name: "群关系", exact: true }).click();
  await expect(
    page.getByRole("group", { name: "群成员互动关系", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "群成员互动关系", exact: true })
    .scrollIntoViewIfNeeded();
  await page
    .getByRole("button", { name: "小明，6 条消息，查看依据", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "小明", exact: true }),
  ).toContainText("分享一张猫照片");
  await page.getByRole("button", { name: "关闭小明", exact: true }).click();
  await page.screenshot({
    animations: "disabled",
    path: `../.runtime/people-groups-ui/people-groups-${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByLabel("当前角色", { exact: true }).selectOption("other");
  await expect(page.getByRole("button", { name: /游戏群/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /猫咪讨论群/ })).toHaveCount(0);
});

test("group permissions save in place without erasing reply keywords", async ({
  page,
}) => {
  await session(page);
  await page.route(apiPath("identity/conversations"), (route) =>
    answer(route, { ...emptyPage, items: [group] }),
  );
  await page.route(apiPath("conversation/group-insights"), (route) =>
    answer(route, activity),
  );
  await page.route(apiPath("conversation/participation"), (route) =>
    answer(route, { enabled: false, revision: 2, can_manage: true }),
  );
  const writes: Record<string, unknown>[] = [];
  for (const path of [
    "identity/conversations.link",
    "conversation/participation.save",
  ])
    await page.route(apiPath(path), (route) => {
      const body = route.request().postDataJSON();
      writes.push({ ...body, path });
      return answer(route, command(body, {}, 5));
    });
  await page.goto("/#/groups");
  await page.getByRole("button", { name: "群聊权限", exact: true }).click();
  await expect(page.getByLabel("允许普通回复", { exact: true })).toBeChecked();
  await page.getByLabel("允许普通回复", { exact: true }).uncheck();
  await page
    .getByRole("button", { name: "保存此会话策略", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    actor_id: "actor",
    connection_id: "qq",
    external_id: "group:123",
    mode: "observe",
    mention_required: true,
    reply_keywords: ["天枢"],
    reply_owner: "tianshu",
    expected_revision: 4,
  });
  await page.getByLabel("允许角色在适合的实时群话题中主动发起参与").check();
  await page.getByRole("button", { name: "保存参与策略", exact: true }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toMatchObject({
    actor_id: "actor",
    target_conversation_id: "group-a",
    enabled: true,
    expected_revision: 2,
  });
});
