import { ApiError } from "../../../api/client";
import { callOperation } from "../../../api/operations";

export type Role = {
  id: string;
  label: string;
  version: number;
  available: boolean;
  reason: string | null;
};
export type Person = { id: string; label: string; after: string | null };
export type Selection = {
  role_id: string;
  role_version: number;
  person_id: string;
  people_after: string | null;
};
export type Projection = {
  pair: { actor_id: string; person_id: string };
  version: number;
  relationship_type: string;
  display_label: string;
  score: number;
  stage: string;
  frozen: boolean;
};
export type Event = {
  id: string;
  kind: string;
  delta: number;
  outcome: string;
  at: string;
  valid: boolean;
  operation: string | null;
  reason: string | null;
};
export type View = {
  projection: Projection;
  items: Event[];
  has_more: boolean;
};
export type Catalog = {
  roles: Role[];
  items: Person[];
  next_after: string | null;
};
export type Command =
  | {
      operation: "set_binding";
      relationship_type: string;
      display_label: string;
    }
  | { operation: "set_freeze"; frozen: boolean }
  | { operation: "adjust_affinity"; delta: number; reason: string };

export class RelationshipError extends Error {
  constructor(public code: string) {
    const messages: Record<string, string> = {
      version_conflict: "关系已被其他操作更新。请刷新，核对后重新修改。",
      idempotency_conflict: "这次操作与已有提交冲突，请刷新核对。",
      scope_changed: "角色或授权已变化，请重新选择。",
      forbidden: "当前账号没有这项管理权限。",
      unauthorized: "登录或服务授权已失效，请重新登录。",
      session_expired: "登录已失效，请重新登录。",
      relationships_not_configured: "关系管理尚未接通。",
      dependency_unavailable: "服务暂时不可用，请稍后刷新。",
      result_unknown: "提交结果尚未确认。请刷新核对，暂勿重复调整。",
    };
    super(messages[code] ?? "请求未完成，请刷新核对。");
  }
}

export async function call<T>(
  name: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
): Promise<T> {
  try { return await callOperation<T>(`people/relationships/${name}`, body, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new RelationshipError(cause.code);
    throw cause;
  }
}
