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
  await page.route(apiPath("channels/group-names"), (route) =>
    answer(route, { names: {} }),
  );
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

test("private chat uses one disabled passive active control in both profile tabs", async ({
  page,
}) => {
  await session(page);
  await page.route(apiPath("identity/people"), (route) =>
    answer(route, { ...emptyPage, items: [person] }),
  );
  await page.route(apiPath("identity/conversations"), (route) =>
    answer(route, {
      ...emptyPage,
      items: [
        {
          ...conversation,
          person_id: person.person_id,
          kind: "private",
          adapter: "qq",
        },
      ],
    }),
  );
  let mode = "passive";
  let revision: number | null = null;
  const saves: string[] = [];
  const current = () => ({
    mode,
    token: "policy-" + String(revision),
    contact_revision: revision,
    can_manage: true,
    links: [
      { connection_id: "qq", external_id: "private:123", label: "小明私聊" },
    ],
  });
  await page.route(apiPath("life/private-contact-policy"), (route) =>
    answer(route, current()),
  );
  await page.route(apiPath("life/private-contact-policy.save"), (route) => {
    const body = route.request().postDataJSON();
    expect(body).toMatchObject({
      actor_id: "actor",
      person_id: "alice",
      conversation_id: null,
      expected_revision: revision,
      policy_token: current().token,
    });
    mode = body.mode;
    saves.push(mode);
    revision = (revision ?? 0) + 1;
    return answer(route, command(body, current()));
  });
  await page.route(apiPath("life/contact-preferences"), (route) =>
    answer(route, {
      revision: revision ?? 0,
      edit_revision: revision,
      timezone: "Asia/Shanghai",
      quiet_start: "23:00",
      quiet_end: "08:00",
      cooldown_seconds: 1800,
      daily_quota: 3,
      unanswered_limit: 3,
      enabled: mode === "active",
    }),
  );
  await page.goto("/#/people");
  await page.getByRole("button", { name: "会话权限", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "私聊模式", exact: true }),
  ).toHaveValue("passive");
  for (const selected of ["active", "passive", "disabled"]) {
    await page
      .getByRole("combobox", { name: "私聊模式", exact: true })
      .selectOption(selected);
    await page
      .getByRole("button", { name: "保存私聊模式", exact: true })
      .click();
    await expect
      .poll(() => saves.length)
      .toBe(["active", "passive", "disabled"].indexOf(selected) + 1);
    await expect(
      page.getByRole("button", { name: "保存私聊模式", exact: true }),
    ).toBeEnabled();
  }
  await expect(
    page.getByText(
      "不接收、不回复、不主动联系。新消息在入口过滤，不触发后续处理。",
      { exact: true },
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "主动联系", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "私聊模式", exact: true }),
  ).toHaveValue("disabled");
  await expect(page.getByText("允许普通主动联系", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("安静时段开始")).toHaveValue("23:00");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("group directory shows live names keeps group numbers and searches names", async ({
  page,
}) => {
  await session(page);
  await page.route(apiPath("identity/conversations"), (route) =>
    answer(route, {
      ...emptyPage,
      items: [{ ...group, label: "QQ 群 123" }],
    }),
  );
  const refreshes: boolean[] = [];
  await page.route(apiPath("channels/group-names"), (route) => {
    const query = new URL(route.request().url()).searchParams;
    expect(query.get("actor_id")).toBe("actor");
    expect(query.get("conversation_ids")).toBe("group-a");
    const fresh = !!query.get("refresh");
    refreshes.push(fresh);
    return answer(route, {
      names: { "group-a": fresh ? "群的新名字" : "真实猫咪群" },
    });
  });
  await page.route(apiPath("conversation/group-insights"), (route) =>
    answer(route, activity),
  );
  await page.goto("/#/groups");
  const list = page.locator(".group-directory-list");
  await expect(list.getByRole("button", { name: /真实猫咪群/ })).toBeVisible();
  await expect(list.getByText("qq · group:123", { exact: true })).toBeVisible();
  await page.getByLabel("查找本批群会话").fill("真实猫咪");
  await expect(list.getByRole("button", { name: /真实猫咪群/ })).toBeVisible();
  await page.getByLabel("查找本批群会话").fill("123");
  await expect(list.getByRole("button", { name: /真实猫咪群/ })).toBeVisible();
  await page.getByLabel("查找本批群会话").fill("");
  await page.getByRole("button", { name: "刷新群会话", exact: true }).click();
  await expect(list.getByRole("button", { name: /群的新名字/ })).toBeVisible();
  expect(refreshes).toContain(true);
});

test("role administrator binding is available after basic profile and can be revoked", async ({
  page,
}) => {
  await session(page);
  await page.route(apiPath("identity/people"), (route) =>
    answer(route, { ...emptyPage, items: [person] }),
  );
  let enabled = false;
  let revision = 0;
  const current = () => ({
    revision,
    can_manage: true,
    is_selected: enabled,
    current: enabled
      ? { person_name: "小明", account_name: "YOKI", enabled }
      : null,
    accounts: [
      { connection_id: "qq", external_id: "1248465005", namespace: "qq" },
    ],
    principals: [
      { principal_id: "owner", display_name: "YOKI", username: "owner" },
    ],
  });
  await page.route(apiPath("identity/role-administrator"), (route) => {
    expect(route.request().method()).toBe("GET");
    const query = new URL(route.request().url()).searchParams;
    expect(query.get("actor_id")).toBe("actor");
    expect(query.get("person_id")).toBe("alice");
    return answer(route, current());
  });
  await page.route(apiPath("identity/role-administrator.save"), (route) => {
    const body = route.request().postDataJSON();
    expect(body).toMatchObject({
      actor_id: "actor",
      person_id: "alice",
      principal_id: "owner",
      expected_revision: revision,
    });
    enabled = body.enabled;
    revision++;
    return answer(route, command(body, current()));
  });
  await page.goto("/#/people");
  await expect(
    page.getByRole("navigation", { name: "人物详情" }),
  ).toBeVisible();
  const tabs = await page
    .getByRole("navigation", { name: "人物详情" })
    .getByRole("button")
    .allTextContents();
  expect(tabs.slice(-2)).toEqual(["基本资料", "主要用户／管理员"]);
  await page
    .getByRole("button", { name: "主要用户／管理员", exact: true })
    .click();
  await expect(
    page.getByText("QQ · 1248465005", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "绑定为当前角色管理员", exact: true })
    .click();
  await expect(
    page.getByText("此人物已是当前角色的主要用户／管理员。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "解除管理员绑定", exact: true })
    .click();
  await expect(
    page.getByText("此角色尚未绑定渠道管理员。", { exact: true }),
  ).toBeVisible();
  expect(revision).toBe(2);
});

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
