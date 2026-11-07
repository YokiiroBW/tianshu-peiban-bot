import { humanizeCode } from "../../design/humanize";

export function connectionName(connection?: {
  namespace: string;
  self_external_id: string;
}): string {
  if (!connection) return "渠道连接不可用";
  return `${humanizeCode(connection.namespace, "机器人账号")} · ${connection.self_external_id}`;
}

export type Person = {
  person_id: string;
  display_name: string;
  revision: number;
  aliases?: string[];
};
export type Connection = {
  connection_id: string;
  actor_id: string;
  namespace: string;
  self_external_id: string;
  enabled: boolean;
  revision: number;
  event_permissions?: string[];
};
export type Account = {
  connection_id: string;
  external_id: string;
  person_id: string;
  principal_id: string | null;
  revision: number;
};
export type Conversation = {
  conversation_id: string;
  actor_id: string;
  person_id: string | null;
  scope_id: string;
  kind: string;
  label: string;
  adapter: string;
  target_ref: string;
  revision: number;
};
export type ConversationLink = {
  connection_id: string;
  external_id: string;
  conversation_id: string;
  mode: "reply" | "observe" | "ignore";
  mention_required: boolean;
  reply_owner: string;
  reply_keywords?: string[];
  revision: number;
};
export type IdentityDirectory = {
  connections: Connection[];
  accounts: Account[];
  conversation_links: ConversationLink[];
  scopes: {
    scope_id: string;
    actor_id: string;
    person_id: string | null;
    kind: string;
    label: string;
    permissions: string[];
  }[];
  principals: {
    principal_id: string;
    person_id: string;
    display_name: string;
    username: string;
    revision: number;
  }[];
};
export const personName = (person: Person) =>
  person.display_name || "未命名人物";
export function dateText(value: string | number | null) {
  if (value == null) return "未记录";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "未记录" : date.toLocaleString();
}
