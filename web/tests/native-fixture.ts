import type { Page, Route } from "@playwright/test";

export const coverage = { complete: true, total: null, missing: [] };
export const emptyPage = { items: [], next_cursor: null, coverage };
export const conversation = {
  conversation_id: "web-conversation",
  actor_id: "actor",
  person_id: "person",
  kind: "private",
  label: "网页私聊",
  revision: 1,
  scope_id: "scope",
  adapter: "web",
  target_ref: "web:web-conversation",
};
export const room = {
  actor_id: "actor",
  revision: 7,
  timezone: "Asia/Shanghai",
  world_id: "world",
  room_id: "room",
  controls: {
    window: 0,
    sheer: 0.5,
    curtain: 0,
    desk_light: 0,
    bedside_light: 0,
    desk_warmth: 0.8,
    bedside_warmth: 1,
  },
  weather_location: null,
  fictional: true,
  observed_at: "2026-10-31T06:00:00Z",
};
export const state = {
  actor_id: "actor",
  revision: 7,
  timezone: "Asia/Shanghai",
  world_id: "world",
  room_id: "room",
  weather_location: null,
  activity: {
    id: "reading",
    revision: 1,
    title: "正在阅读",
    kind: "reading",
    state: "running",
    checkpoint_step: 0,
    checkpoint_position: 0,
    checkpoint_unit: "characters",
    waiting_reason: null,
    goal_id: null,
    next_due_at: null,
    resume_condition: null,
    action_id: null,
    checkpoint_data: {},
    source_refs: [],
  },
  outfit: null,
  affect: {
    feelings: [],
    regulation: { mode: "steady", expression_direction: "平静地表达" },
    source_refs: [],
    missing: [],
  },
  room_controls: room.controls,
  observed_at: "2026-10-31T06:00:00Z",
  missing: [],
};
export function command(
  body: Record<string, unknown>,
  data: unknown,
  revision: number | null = null,
) {
  return {
    client_request_id: body.client_request_id,
    revision,
    action_id: null,
    attempt_id: null,
    receipt: null,
    data,
  };
}
export function receipt(execution: "pending" | "succeeded" | "unknown") {
  return {
    receipt_id: `receipt-${execution}`,
    action_id: "original-action",
    attempt_id: "original-attempt",
    execution,
    effect:
      execution === "unknown"
        ? "unknown"
        : execution === "succeeded"
          ? "confirmed"
          : "none",
    delivery: "not_requested",
    artifacts: [],
    coverage: null,
    result: {},
    error_code: null,
  };
}
export async function answer(route: Route, value: unknown) {
  await route.fulfill({ json: value });
}

/** Domain selectors stay in the real URL; match only the exact API pathname. */
export function apiPath(path: string) {
  return (url: URL) => url.pathname === `/api/v1/${path}`;
}

/** Synthetic fixtures use the actual contracts and reject undeclared operations. */
export async function nativeSession(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.slice(
      "/api/v1/".length,
    );
    const values: Record<string, unknown> = {
      session: {
        authenticated: true,
        initialized: true,
        setup_token_required: false,
        principal: {
          principal_id: "author",
          display_name: "测试作者",
          permissions: ["*"],
        },
        csrf_token: "synthetic-csrf",
      },
      "companion/roles/list": {
        ...emptyPage,
        items: [
          { actor_id: "actor", name: "小屋角色", revision: 1, enabled: true },
        ],
      },
      "identity/conversations": { ...emptyPage, items: [conversation] },
      "life/access": {
        can_read: true,
        can_manage: true,
        can_read_published: true,
      },
      "life/state": state,
      "life/routine": {
        actor_id: "actor",
        revision: 1,
        enabled: true,
        timezone: "Asia/Shanghai",
        wake_at: "08:00",
        sleep_at: "23:00",
        idle_seconds: 5400,
        next_due_at: "2026-11-01T06:00:00Z",
        last_tick_at: null,
        schedule_status: "waiting",
        schedule_reason: null,
        planning: null,
        observed_at: "2026-11-01T05:00:00Z",
      },
      "companion/growth/view": {
        items: [],
        reflections: [],
        next_cursor: null,
        missing: [],
        review: null,
        can_review: true,
      },
      "life/activities": { ...emptyPage, items: [state.activity] },
      "memory/episodes": { ...emptyPage, watermark: "0" },
      "infrastructure/host": { configured: false, node: null },
      "life/room": room,
      "life/commitments": emptyPage,
      "capabilities/discover": [],
      "capabilities/catalog/view": {
        actor_id: "actor",
        revision: 1,
        items: [],
        sources: [],
      },
      "memory/claims": emptyPage,
      "memory/matters": emptyPage,
      "memory/reflections": emptyPage,
      "memory/query": emptyPage,
      "devices/view": {
        connections: [],
        readings: [],
        can_manage: true,
        can_control: true,
        control_capability: "device.control",
      },
    };
    if (path in values) return answer(route, values[path]);
    return route.fulfill({
      status: 404,
      json: {
        error: {
          code: "fixture_operation_not_declared",
          message: path,
          details: null,
          request_id: "fixture",
        },
      },
    });
  });
}
