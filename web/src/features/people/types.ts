import type { RecordGroup } from "../memory/MemoryRecords";
export type { RecordGroup } from "../memory/MemoryRecords";

export type Role = {
  id: string;
  label: string;
  version: number;
  available: boolean;
  reason: string | null;
};
export type MemoryState = {
  available: boolean;
  code: string;
  actor_id: string | null;
  roles: Role[];
};
export type Profile = {
  qq_id: string;
  person_id: string;
  display_name: string;
  aliases: {
    kind: string;
    bot_id: string;
    group_id: string;
    value: string;
    observed_at: string;
  }[];
};
export type Profiles = { items: Profile[]; next_cursor: string | null };
export type Policy = {
  observe: boolean;
  mode: "observe_only" | "whitelist" | "blacklist";
  list: string[];
  actor_id: string | null;
};
export type Connection = {
  id: string;
  name: string;
  account_id: string;
  revision: number;
  read_enabled: boolean;
  enabled: boolean;
  state: string;
  private_policy: Policy;
  group_policy: Policy;
};
export type Observations = {
  available: boolean;
  unlocked: boolean;
  connections: Connection[];
};
export type Found = {
  conversation: string;
  author: string;
  count: number;
  last_at: number;
  decision: {
    observe: boolean;
    reply_permitted: boolean;
    reply_triggered: boolean;
  };
};
export type FoundPage = {
  items: Found[];
  next_cursor: { conversation: string; author: string } | null;
};
export type Source = Found & { connection: Connection };
export type Person = { qqId: string; profile?: Profile; sources: Source[] };
export type Records = {
  items: RecordGroup[];
  next_cursor: string | null;
  verified_at: string;
  scope_version: number;
};
export type Archive = {
  memory_state: string;
  backlog: Record<string, number>;
  archive_next_cursor: string | null;
  items: {
    source_ref: string;
    author: string;
    archive_state: string;
    error_code: string | null;
  }[];
  archive_items: {
    source_ref: string;
    author: string;
    content_state: string;
    text: string;
    sent_at: string;
  }[];
};
export const personName = (person: Person) =>
  person.profile?.display_name || `QQ ${person.qqId}`;
export function dateText(at: number | string) {
  const date = new Date(typeof at === "number" ? at * 1000 : at);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString("zh-CN", { hour12: false })
    : "时间未记录";
}
