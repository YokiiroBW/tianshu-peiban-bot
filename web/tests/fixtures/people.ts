// Synthetic people payloads for browser regression tests only.
export function createPeopleAccount() {
  return {
    id: "bot:one",
    name: "家里的机器人",
    account_id: "90001",
    revision: 1,
    read_enabled: true,
    enabled: true,
    state: "ready",
    private_policy: {
      observe: true,
      mode: "observe_only",
      list: [] as string[],
      actor_id: null as string | null,
    },
    group_policy: {
      observe: true,
      mode: "blacklist",
      list: ["88888"],
      actor_id: "role:a",
    },
  };
}

export function profiles(after = false) {
  return {
    items: after
      ? [
          {
            qq_id: "10003",
            person_id: "person:three",
            display_name: "林间风",
            aliases: [],
          },
        ]
      : [
          {
            qq_id: "10001",
            person_id: "person:one",
            display_name: "小雨",
            aliases: [
              {
                kind: "group_card",
                bot_id: "90001",
                group_id: "80001",
                value: "群里的小雨",
                observed_at: "2026-10-04T10:00:00Z",
              },
            ],
          },
        ],
    next_cursor: after ? null : "profiles-page-2",
  };
}

export function portrait(roleId: string) {
  return {
    items:
      roleId === "role:b"
        ? [
            {
              semantic_group_id: "portrait:one",
              category: "preference",
              field_key: "reading",
              item_key: "books",
              units: [
                {
                  record_id: "r1",
                  record_version: 1,
                  statement: "小雨喜欢在傍晚读书。",
                  uncertainty: null,
                  conditions: null,
                  negations: null,
                  valid_time: null,
                  reality: null,
                },
              ],
            },
          ]
        : [],
    next_cursor: null,
    scope_version: 1,
    verified_at: "2026-10-04T10:00:00Z",
  };
}
