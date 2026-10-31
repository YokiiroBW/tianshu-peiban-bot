// Synthetic browser fixtures shared with the local UI preview.
type Filters = {
  keyword: string | null;
  sender_id: string | null;
  start_timestamp: number | null;
  end_timestamp: number | null;
};
export type Body = {
  role_id: string;
  role_version: number;
  group_id?: string;
  window?: string;
  filters?: Filters;
  cursor?: string | null;
  limit?: number;
  locator?: string;
  stored_content_sha256?: string;
};
export const ROLES = [
  { id: "actor:a", label: "角色甲", version: 0, available: true, reason: null },
  { id: "actor:b", label: "角色乙", version: 2, available: true, reason: null },
];
export const GROUPS = [
  { id: "group-a", label: "甲群", conversation_id: "conversation:a" },
  { id: "group-b", label: "乙群", conversation_id: "conversation:b" },
];
export const ROLE_B_GROUP = {
  id: "group-role-b",
  label: "甲群（角色乙）",
  conversation_id: "conversation:a",
};
export const HASH = "a".repeat(64);
export const LOCATOR = `audit-evidence:0:${"b".repeat(64)}:message-a:${HASH}`;

export function record(index = 1) {
  return {
    locator:
      index === 1
        ? LOCATOR
        : `audit-evidence:0:${"b".repeat(64)}:message-${index}:${HASH}`,
    stored_content_sha256: HASH,
    sender_id: index === 1 ? "10001" : "10002",
    timestamp: 1792472400 + index,
    text: index === 1 ? "10001 当时的完整原话。" : "10002 随后的完整原话。",
  };
}

function marker(body: Body) {
  const group = [...GROUPS, ROLE_B_GROUP].find(
    (item) => item.id === body.group_id,
  )!;
  return `${body.role_id === "actor:a" ? "角色甲" : "角色乙"} / ${group.label}`;
}

function summary(body: Body) {
  return `${marker(body)}：读书讨论`;
}
function statement(body: Body) {
  return `${marker(body)} 当前群画像`;
}

export function insights(body: Body) {
  const group = [...GROUPS, ROLE_B_GROUP].find(
    (item) => item.id === body.group_id,
  )!;
  return {
    group,
    window: { start_timestamp: 1792418400, end_timestamp: 1792504799 },
    stats: {
      state: "available",
      data: {
        authors: [
          { sender_id: "10001", messages: 4 },
          { sender_id: "10002", messages: 2 },
        ],
        edges: [
          {
            from_sender_id: "10001",
            to_sender_id: "10002",
            reply_count: 2,
            mention_count: 1,
            interaction_count: 2,
            evidence_locators: [LOCATOR],
          },
        ],
        buckets: [
          {
            start_timestamp: 1792418400,
            messages: 2,
            active_senders: 1,
            reply_messages: 0,
            mention_messages: 0,
            broadcast_messages: 0,
          },
          {
            start_timestamp: 1792422000,
            messages: 4,
            active_senders: 2,
            reply_messages: 2,
            mention_messages: 1,
            broadcast_messages: 1,
          },
        ],
        coverage: {
          messages_scanned: 6,
          complete: true,
          has_more: false,
          unresolved_reply: 1,
          unresolved_mention: 2,
          broadcast_messages: 1,
          relation_unavailable_messages: 1,
        },
      },
    },
    topics: {
      state: "available",
      data: {
        items: [
          {
            segment_id: `segment:${body.role_id}:${group.id}`,
            window_id: `window:${group.id}`,
            recorded_at: "2026-10-20T10:00:00Z",
            topics: [
              {
                topic_id: "topic:reading",
                summary: summary(body),
                person_ids: ["person:reader-a", "person:reader-b"],
                member_refs: [
                  {
                    member_id: "material:human-a",
                    role: "human",
                    person_id: "person:reader-a",
                    actor_id: null,
                    observed_at: "2026-10-20T09:00:00Z",
                    reality: "real",
                  },
                  {
                    member_id: "reply:bot-a",
                    role: "bot",
                    person_id: null,
                    actor_id: body.role_id,
                    observed_at: "2026-10-20T09:01:00Z",
                    reality: "real",
                  },
                ],
                memory_refs: [
                  {
                    subject_person_id: "person:reader-a",
                    record_id: "record:reading",
                    record_version: 3,
                  },
                ],
              },
            ],
            coverage: {
              complete_new: true,
              omitted_context: false,
              result_person_count: 2,
            },
          },
        ],
        next_cursor: null,
        omitted_invalidated: false,
      },
    },
    records: {
      state: "available",
      data: {
        items: [
          {
            semantic_group_id: `semantic:${group.id}`,
            units: [
              {
                record_id: "record:reading",
                record_version: 3,
                statement: statement(body),
              },
            ],
          },
        ],
        next_cursor: null,
      },
    },
  };
}
