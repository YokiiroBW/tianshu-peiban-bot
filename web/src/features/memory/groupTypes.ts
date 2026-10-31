import type { GroupRule } from "../settings/GroupPolicyEditor";

export type GroupEntry = {
  id: string;
  owner_id: string;
  owner_name: string;
  account_id: string;
  group_id: string;
  name: string | null;
  member_count: number | null;
  max_member_count: number | null;
  first_seen_at: number | null;
  last_seen_at: number | null;
  revision: number;
  rule: GroupRule;
  pending: boolean;
  read_enabled: boolean;
  has_archive: boolean;
  state: string;
  ordinary_claim_available: boolean;
  memory_group_id: string | null;
};

export type Group = {
  id: string;
  label: string;
  conversation_id: string | null;
  mode?: string;
  pending?: boolean;
  unavailable?: string | null;
};
export type EvidenceRecord = {
  locator: string;
  stored_content_sha256: string;
  sender_id: string;
  timestamp: number;
  text: string;
};
export type Stats = {
  authors: { sender_id: string; messages: number }[];
  edges: {
    from_sender_id: string;
    to_sender_id: string;
    reply_count: number;
    mention_count: number;
    interaction_count: number;
    evidence_locators: string[];
  }[];
  buckets: {
    start_timestamp: number;
    messages: number;
    active_senders: number;
    reply_messages: number;
    mention_messages: number;
    broadcast_messages: number;
  }[];
  coverage: {
    messages_scanned: number;
    has_more: boolean;
    complete: boolean;
    unresolved_reply: number;
    unresolved_mention: number;
    broadcast_messages: number;
    relation_unavailable_messages: number;
  };
};
export type Topic = {
  topic_id: string;
  summary: string;
  person_ids: string[];
  member_refs: {
    member_id: string;
    role: "human" | "bot";
    person_id: string | null;
    actor_id: string | null;
    observed_at: string;
    reality: string;
  }[];
  memory_refs: {
    subject_person_id: string;
    record_id: string;
    record_version: number;
  }[];
};
export type TopicPage = {
  items: {
    segment_id: string;
    window_id: string;
    recorded_at: string;
    topics: Topic[];
    coverage: {
      complete_new: boolean;
      omitted_context: boolean;
      result_person_count: number;
    };
  }[];
  next_cursor: string | null;
  omitted_invalidated: boolean;
};
export type RecordPage = {
  items: import("./MemoryRecords").RecordGroup[];
  next_cursor: string | null;
};
export type Part<T> =
  { state: "available"; data: T } | { state: "unavailable"; code: string };
export type Insights = {
  group: Group;
  window: { start_timestamp: number; end_timestamp: number };
  stats: Part<Stats>;
  topics: Part<TopicPage>;
  records: Part<RecordPage>;
};
