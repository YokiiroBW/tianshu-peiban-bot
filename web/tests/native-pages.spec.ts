import { test, expect } from "@playwright/test";
import {
  answer,
  command,
  nativeSession,
  receipt,
  room,
  state,
} from "./native-fixture";

test.beforeEach(async ({ page }) => nativeSession(page));

test("life summaries show recorded feelings without exposing expression guidance", async ({
  page,
}) => {
  const guidance =
    "Use the authored character's natural expression and the current person's explicit preference.";
  let appraisal: string | null = null;
  await page.route("**/api/v1/life/state**", (route) =>
    answer(route, {
      ...state,
      affect: {
        ...state.affect,
        regulation: { mode: "steady", expression_direction: guidance },
        feelings: appraisal
          ? [
              {
                event_id: "feeling",
                appraisal,
                attribution: "self",
                control: 0.5,
                goal_relevance: 0,
              },
            ]
          : [],
      },
    }),
  );
  for (const recorded of [null, "读完这一章，想把刚才的灵感记下来。"]) {
    appraisal = recorded;
    for (const route of ["workbench", "roles/life"]) {
      await page.goto(`/#/${route}`);
      const summary =
        route === "roles/life"
          ? page.locator("section").filter({
              has: page.getByRole("heading", {
                name: "心情与感想",
                exact: true,
              }),
            })
          : page.locator("section").filter({
              has: page.getByRole("heading", {
                name: "小屋角色的近况",
                exact: true,
              }),
            });
      await expect(
        summary.getByText(recorded ?? "暂时没有新的主观记录。", {
          exact: true,
        }),
      ).toBeVisible();
      await expect(page.getByText(guidance, { exact: true })).toHaveCount(0);
    }
  }
});

test("device control uses the observed revision and unknown never resends the action", async ({
  page,
}) => {
  const writes: Record<string, unknown>[] = [];
  const reading = {
    connection_id: "ha",
    entity_id: "light.desk",
    label: "书桌灯",
    revision: 9,
    state: "off",
    attributes: {},
    observed_at: new Date().toISOString(),
    code: "observed",
    connection_revision: 3,
  };
  await page.route("**/api/v1/devices/view**", (route) =>
    answer(route, {
      connections: [
        {
          id: "ha",
          revision: 3,
          base_url: "http://ha:8123",
          enabled: true,
          configured: true,
          state: "configured",
          reason: null,
          entities: [{ entity_id: "light.desk", label: "书桌灯" }],
          templates: [
            {
              template_id: "desk-on",
              entity_id: "light.desk",
              label: "打开书桌灯",
              service: "turn_on",
            },
          ],
          timeout_seconds: 5,
          status_max_age_seconds: 120,
        },
      ],
      readings: [reading],
      can_manage: false,
      can_control: true,
      control_capability: "device.control",
    }),
  );
  await page.route("**/api/v1/devices/control**", async (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf");
    const body = route.request().postDataJSON();
    writes.push(body);
    await answer(route, {
      ...command(body, null),
      action_id: "original-action",
      attempt_id: "original-attempt",
      receipt: receipt("pending"),
    });
  });
  await page.route("**/api/v1/capabilities/get**", (route) => {
    expect(route.request().postDataJSON().action_id).toBe("original-action");
    return answer(route, {
      action: {},
      attempt: {},
      receipt: receipt("unknown"),
      result_revision: 1,
      result_observed_at: new Date().toISOString(),
    });
  });
  await page.goto("/#/home/devices");
  await page.getByRole("button", { name: "打开书桌灯", exact: true }).click();
  await expect(
    page.getByText("原任务结果尚未确认，请查询原任务。", { exact: true }),
  ).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    connection_id: "ha",
    template_id: "desk-on",
    expected_revision: 9,
  });
  await expect(
    page.getByRole("link", { name: "查看原任务", exact: true }).first(),
  ).toHaveAttribute("href", /action=original-action/);
});

test("a weekly commitment freezes an authorized conversation selector and timezone", async ({
  page,
}) => {
  let saved: Record<string, unknown> | null = null;
  await page.route("**/api/v1/life/commitments.save**", async (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("conversation_id")).toBe("web-conversation");
    expect(url.searchParams.get("person_id")).toBe("person");
    saved = route.request().postDataJSON();
    await answer(
      route,
      command(saved!, { id: "promise", revision: 1, state: "scheduled" }, 1),
    );
  });
  await page.goto("/#/roles/commitments");
  await page.getByRole("button", { name: "新增约定", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("约定内容", { exact: true }).fill("周六七点提醒我");
  await dialog
    .getByLabel("计划时间（当前浏览器时区）", { exact: true })
    .fill("2026-11-07T07:00");
  await dialog
    .getByLabel("重复计划采用的时区", { exact: true })
    .fill("Asia/Shanghai");
  await dialog
    .getByRole("combobox", { name: "重复方式", exact: true })
    .selectOption("weekly");
  await dialog.getByLabel("星期六", { exact: true }).check();
  await dialog
    .getByRole("combobox", { name: "实际目标会话", exact: true })
    .selectOption("web-conversation");
  await dialog.getByRole("button", { name: "保存约定", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(saved).toMatchObject({
    actor_id: "actor",
    person_id: "person",
    conversation_id: "web-conversation",
    due_at: "2026-11-06T23:00:00.000Z",
    recurrence: "weekly",
    weekdays: [5],
    timezone: "Asia/Shanghai",
    expected_revision: null,
  });
});

test("room preview keeps the original CAS version when saving actual controls", async ({
  page,
}) => {
  let saved: Record<string, unknown> | null = null;
  await page.route("**/api/v1/life/room.save**", async (route) => {
    saved = route.request().postDataJSON();
    await answer(route, command(saved!, { controls: saved!.controls }, 8));
  });
  await page.goto("/#/roles/room");
  await expect(page.locator("canvas")).toHaveAttribute("data-ready", "true");
  await page.getByRole("button", { name: /环境控制/ }).click();
  await page
    .getByRole("slider", { name: "床头灯亮度", exact: true })
    .fill("70");
  await page.getByRole("button", { name: "保存当前环境", exact: true }).click();
  await expect.poll(() => saved).not.toBeNull();
  expect(saved).toMatchObject({
    expected_revision: room.revision,
    controls: { bedside_light: 0.7 },
  });
  await expect(
    page.getByRole("button", { name: "保存当前环境", exact: true }),
  ).toBeDisabled();
});

test("chat sends the canonical uploaded source in its authorized conversation", async ({
  page,
}) => {
  const media = {
    media_id: "source:upload:1",
    uri: "controlled",
    mime_type: "image/png",
    size_bytes: 8,
    sha256: null,
    duration_ms: null,
    width: 1,
    height: 1,
  };
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/v1/conversation/messages**", (route) =>
    answer(route, {
      items: [],
      next_cursor: null,
      coverage: { complete: true, total: null, missing: [] },
    }),
  );
  await page.route("**/api/v1/conversation/turn**", (route) =>
    answer(route, {
      turn_id: "turn",
      state: "collecting",
      revision: 1,
      receipt: null,
      missing: [],
      action_ids: [],
      expression_id: null,
    }),
  );
  await page.route("**/api/v1/capabilities/upload**", async (route) => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get("conversation_id")).toBe("web-conversation");
    const id = route
      .request()
      .postData()!
      .match(/name="client_request_id"\r\n\r\n([^\r\n]+)/)![1];
    await answer(
      route,
      command(
        { client_request_id: id },
        {
          artifact: {
            artifact_id: "uploaded",
            media: { ...media, media_id: "artifact:uploaded" },
            source_refs: [],
            coverage: null,
            metadata: {},
          },
          media,
          source_ref: { source_id: "upload", revision: 1 },
        },
      ),
    );
  });
  await page.route("**/api/v1/conversation/send**", async (route) => {
    sent = route.request().postDataJSON();
    await answer(
      route,
      command(
        sent!,
        { turn_id: "turn", input_id: "input", state: "collecting" },
        1,
      ),
    );
  });
  await page.goto("/#/roles/chat");
  await page
    .getByLabel("图片、音视频或文件", { exact: true })
    .setInputFiles({
      name: "upload.png",
      mimeType: "image/png",
      buffer: Buffer.from("isolated"),
    });
  await expect(
    page.getByRole("button", { name: "移除此附件", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect.poll(() => sent).not.toBeNull();
  expect(sent).toMatchObject({
    actor_id: "actor",
    person_id: "person",
    conversation_id: "web-conversation",
    parts: [{ kind: "image", media }],
  });
  await expect(
    page.getByText("正在收集连续消息", { exact: true }),
  ).toBeVisible();
});
