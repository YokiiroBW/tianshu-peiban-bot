export type Role = {
  id: string;
  label: string;
  state: string;
  enabled: boolean | null;
  assignable: boolean;
  profile_id: string | null;
  profile_version: number | null;
  capabilities: string[];
  source: string;
};
export type Account = {
  id: string;
  label: string;
  adapter: string;
  account_id: string;
  instance_id: string;
  states: string[];
  sources: string[];
};
export type Conversation = {
  id: string;
  label: string;
  conversation_id: string;
  kind: "group" | "private";
  account_id: string;
  actor_ids: string[];
  binding: "explicit" | "inherited" | "registered" | "unbound";
  mode: string;
  observe: boolean;
  reply_permitted: boolean;
  state: string;
  source: string;
  edit: {
    kind: "group";
    connection_id: string;
    revision: number;
    unlocked: boolean;
  } | null;
};
export type Relationships = {
  generated_at: string;
  accounts: Account[];
  roles: Role[];
  conversations: Conversation[];
  defaults: {
    account_id: string;
    kind: "group" | "private";
    actor_id: string | null;
    mode: string;
    observe: boolean;
  }[];
  discovered_complete: boolean;
  sources: { id: string; available: boolean }[];
};
export type SafeEvent = {
  schema_version: string;
  timestamp: string;
  service: string;
  instance_id: string;
  sequence: number;
  event_id: string;
  level: string;
  event: string;
  outcome: string;
  correlation_id: string | null;
  duration_ms: number | null;
  error_code: string | null;
};
export type TraceWindow = { start_ns: string; end_ns: string };
export type TraceSummary = {
  correlation_id: string;
  first_at: string;
  last_at: string;
  event_count: number;
  services: string[];
  has_failure: boolean;
  has_unknown: boolean;
  business_observed: boolean;
};
export type TraceIndex = {
  availability: "ready" | "not_configured";
  window: TraceWindow;
  traces: TraceSummary[];
  complete: boolean;
  event_count: number;
};
export type Trace = {
  availability: "ready" | "not_configured";
  correlation_id: string;
  window: TraceWindow;
  events: SafeEvent[];
  complete: boolean;
  stages: { id: string; label: string; event_ids: string[] }[];
};
export function stateLabel(state: string) {
  return (
    (
      {
        online: "心跳在线",
        ready: "配置就绪 · 在线未核验",
        active: "已启用",
        disabled: "已停用",
        pending: "待确认或离线",
        degraded: "运行受限",
        failed: "失败",
        unknown: "结果未知",
        not_created: "尚未创建连接",
        registered: "已登记",
        unverified: "角色状态未核验",
        observe: "只读观察",
        reply: "触发回复",
        active_mode: "自主参与",
        observe_only: "仅观察",
        whitelist: "私聊白名单",
        blacklist: "私聊黑名单",
      } as Record<string, string>
    )[state] ?? state
  );
}
export function outcomeLabel(outcome: string) {
  return (
    (
      {
        started: "开始记录",
        succeeded: "成功事件",
        failed: "失败事件",
        rejected: "已拒绝",
        unknown: "结果未知",
        degraded: "运行受限",
        cancelled: "已取消",
      } as Record<string, string>
    )[outcome] ?? outcome
  );
}
export function eventTone(event: SafeEvent) {
  return ["failed", "rejected"].includes(event.outcome)
    ? "red"
    : ["unknown", "degraded", "started"].includes(event.outcome)
      ? "yellow"
      : event.outcome === "cancelled"
        ? "gray"
        : "blue";
}
export function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
