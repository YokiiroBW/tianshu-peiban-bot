import { test, expect, type Page } from "@playwright/test";
import { answer, apiPath, command, nativeSession } from "./native-fixture";
import type {
  FishConnection,
  VoiceProfile,
} from "../src/features/multimodal/types";

const connection: FishConnection = {
  id: "我的 Fish Plus",
  revision: 2,
  transport: "mcp",
  enabled: true,
  configured: true,
  account_id: "my-account",
  team_id: null,
  workspace_id: null,
  quota_group: "my-plus",
  billing_source: "plus",
  state: "connected",
  reason: null,
  balance: null,
  capabilities: {
    tts: true,
    asr: true,
    stream: false,
    timestamps: false,
    voices: true,
    clone: true,
    dictionaries: false,
  },
};
const voice = {
  id: "my-voice-id",
  name: "轻声中文",
  preview: null,
  visibility: "private",
  owner_id: "me",
};

async function speechFixture(
  page: Page,
  options: {
    connections?: FishConnection[];
    profiles?: VoiceProfile[];
  } = {},
) {
  await nativeSession(page);
  const state = {
    connections: options.connections ?? [connection],
    profiles: options.profiles ?? [],
    searches: [] as Record<string, unknown>[],
    saves: [] as Record<string, unknown>[],
    previews: [] as Record<string, unknown>[],
  };
  await page.route(apiPath("companion/roles/list"), (route) =>
    answer(route, {
      items: [
        { actor_id: "actor", name: "小屋角色", revision: 1, enabled: true },
        { actor_id: "other", name: "另一位角色", revision: 1, enabled: true },
      ],
      next_cursor: null,
    }),
  );
  await page.route(apiPath("models/catalog"), (route) =>
    answer(route, {
      functions: [],
      providers: [],
      routes: [],
      bindings: [],
    }),
  );
  await page.route(apiPath("speech/view"), (route) => {
    const actor = new URL(route.request().url()).searchParams.get("actor_id");
    return answer(route, {
      connections: state.connections,
      profiles: state.profiles.filter((item) => item.actor_id === actor),
      dictionaries: [],
      default_route: null,
    });
  });
  await page.route(apiPath("speech/voices.search"), (route) => {
    const body = route.request().postDataJSON();
    state.searches.push(body);
    return answer(
      route,
      command(body, {
        items: body.cursor
          ? [{ ...voice, id: "second-voice", name: "第二条声线" }]
          : body.owned
            ? [voice]
            : [{ ...voice, id: "public-voice", name: "公开声线" }],
        next_cursor: body.cursor ? null : "page-two",
      }),
    );
  });
  await page.route(apiPath("speech/profiles.save"), (route) => {
    const body = route.request().postDataJSON();
    const actor = new URL(route.request().url()).searchParams.get("actor_id");
    const saved = {
      ...body,
      actor_id: actor,
      revision: body.expected_revision + 1,
    } as VoiceProfile;
    state.saves.push({ ...body, target_actor: actor });
    state.profiles = [
      ...state.profiles.filter((item) => item.id !== saved.id),
      saved,
    ];
    return answer(route, command(body, saved, saved.revision));
  });
  await page.route(apiPath("speech/preview"), (route) => {
    const body = route.request().postDataJSON();
    state.previews.push(body);
    return answer(
      route,
      command(body, {
        artifacts: [],
        alignment: [],
        spoken_text: body.text,
        model: body.model,
        billing_source: "plus",
        usage: null,
      }),
    );
  });
  return state;
}

async function openVoices(page: Page) {
  await page.goto("/#/roles/settings");
  await page
    .getByRole("navigation", { name: "角色设置分类" })
    .getByRole("button", { name: "声音与语音", exact: true })
    .click();
  return page.getByRole("region", { name: "角色声音设置" });
}

test("role settings reads account voices, binds the selected ID and reads the binding back", async ({
  page,
}) => {
  const state = await speechFixture(page);
  const panel = await openVoices(page);
  await expect(
    panel.getByText("音色 ID：my-voice-id", { exact: true }),
  ).toBeVisible();
  expect(state.searches[0]).toMatchObject({
    connection_id: connection.id,
    owned: true,
  });
  expect(state.saves).toHaveLength(0);
  expect(state.previews).toHaveLength(0);
  await panel.getByRole("button", { name: "选择此音色", exact: true }).click();
  await panel
    .getByRole("button", { name: "绑定到当前角色", exact: true })
    .click();
  await expect(
    panel.getByText("已绑定：轻声中文", { exact: true }),
  ).toBeVisible();
  expect(state.saves[0]).toMatchObject({
    id: "actor",
    target_actor: "actor",
    voice_id: voice.id,
    voice_name: voice.name,
    output_mode: "both",
    expected_revision: 0,
    connection_id: connection.id,
    language: "zh-CN",
    vocal_defaults: { speed: 1, tone: "" },
    person_id: null,
    conversation_id: null,
  });
  await page.reload();
  await page
    .getByRole("navigation", { name: "角色设置分类" })
    .getByRole("button", { name: "声音与语音", exact: true })
    .click();
  await expect(
    page.getByText("已绑定：轻声中文", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "试听所选音色", exact: true }).click();
  await expect.poll(() => state.previews.length).toBe(1);
  expect(state.previews[0]).toMatchObject({
    voice_id: voice.id,
    profile_id: "actor",
    profile_revision: 1,
  });
});

test("voice browsing paginates and public search does not change the bound voice", async ({
  page,
}) => {
  const state = await speechFixture(page);
  const panel = await openVoices(page);
  await expect(panel.getByText("轻声中文", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "加载更多音色" }).click();
  await expect(panel.getByText("第二条声线", { exact: true })).toBeVisible();
  expect(state.searches.at(-1)).toMatchObject({
    owned: true,
    cursor: "page-two",
  });
  await panel
    .getByRole("combobox", { name: "音色范围", exact: true })
    .selectOption("public");
  await expect(panel.getByText("公开声线", { exact: true })).toBeVisible();
  await expect(panel.getByText("第二条声线", { exact: true })).toHaveCount(0);
  expect(state.searches.at(-1)).toMatchObject({ owned: false, cursor: null });
  expect(state.saves).toHaveLength(0);
});

test("manual IDs preserve the existing profile identity, revision and output preference", async ({
  page,
}) => {
  const state = await speechFixture(page, {
    profiles: [
      {
        id: "legacy-profile",
        actor_id: "actor",
        revision: 7,
        voice_id: "old-id",
        voice_name: "旧声线",
        connection_id: connection.id,
        model: "drama-3",
        language: "ja-JP",
        output_mode: "natural",
        dictionary_ids: [],
        vocal_defaults: { speed: 1.2, tone: "轻声", volume: 0.8 },
      },
    ],
  });
  const panel = await openVoices(page);
  await expect(
    panel.getByText("已绑定：旧声线", { exact: true }),
  ).toBeVisible();
  await panel.getByText("手动填写音色 ID", { exact: true }).click();
  await panel.getByLabel("音色 ID", { exact: true }).fill("manual-voice");
  await panel.getByRole("button", { name: "绑定到当前角色" }).click();
  await expect(
    panel.getByText("已绑定：manual-voice", { exact: true }),
  ).toBeVisible();
  expect(state.saves[0]).toMatchObject({
    id: "legacy-profile",
    expected_revision: 7,
    voice_id: "manual-voice",
    voice_name: null,
    model: "drama-3",
    language: "ja-JP",
    output_mode: "natural",
    vocal_defaults: { speed: 1.2, tone: "轻声", volume: 0.8 },
  });
});

test("switching accounts clears selection and switching roles saves only to the active role", async ({
  page,
  isMobile,
}) => {
  const second = {
    ...connection,
    id: "另一个 Fish 账户",
    account_id: "second-account",
  };
  const state = await speechFixture(page, {
    connections: [connection, second],
  });
  const panel = await openVoices(page);
  await panel.getByRole("button", { name: "选择此音色", exact: true }).click();
  await panel
    .getByRole("combobox", { name: "语音账户", exact: true })
    .selectOption(second.id);
  await expect(
    panel.getByRole("button", { name: "绑定到当前角色" }),
  ).toBeDisabled();
  await expect.poll(() => state.searches.at(-1)?.connection_id).toBe(second.id);
  if (isMobile) {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page
      .getByRole("dialog", { name: "导航", exact: true })
      .getByLabel("当前角色", { exact: true })
      .selectOption("other");
    await page.getByRole("button", { name: "关闭导航", exact: true }).click();
  } else {
    await page.getByLabel("当前角色", { exact: true }).selectOption("other");
  }
  await panel.getByRole("button", { name: "选择此音色", exact: true }).click();
  await panel.getByRole("button", { name: "绑定到当前角色" }).click();
  await expect.poll(() => state.saves.length).toBe(1);
  expect(state.saves[0]).toMatchObject({ id: "other", target_actor: "other" });
});

test("missing connections give a direct settings link without saving or synthesizing", async ({
  page,
}) => {
  const state = await speechFixture(page, { connections: [] });
  const panel = await openVoices(page);
  await expect(panel.getByText(/尚无可用的语音连接/)).toBeVisible();
  await expect(
    panel.getByRole("link", { name: "声音与语音设置", exact: true }),
  ).toHaveAttribute("href", "#/settings/speech");
  await expect(
    panel.getByRole("button", { name: "绑定到当前角色" }),
  ).toBeDisabled();
  expect(state.searches).toHaveLength(0);
  expect(state.saves).toHaveLength(0);
  expect(state.previews).toHaveLength(0);
});
