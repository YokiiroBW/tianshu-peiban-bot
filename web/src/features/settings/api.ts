import { ApiError } from "../../api/client";
import { callOperation } from "../../api/operations";
import { readSession } from "../../api/session";
/** Same-origin web-console calls for the models section; never a service credential. */

export type SessionState = {
  authenticated: boolean;
  csrf: string;
  username?: string;
};

export type Template = {
  template_id: string;
  label: string;
  target: string;
  provider_id: string;
  model_id: string;
  verified_capabilities: string[];
  lifetime_seconds: number;
  timeout_ms: number;
};

export type Version = {
  version: number;
  revoked: boolean;
  availability: string;
  published_at: string;
  usable_until: string;
  providers: { provider_id: string; protocol: string; model_id: string }[];
  bindings: { workload: string; provider_id: string; model_id: string }[];
};

export type Target = {
  target: string;
  label: string;
  detail: string;
  protocol: string;
  workload: string;
  current_version: number | null;
  availability: string;
  usable_until: string | null;
  versions: Version[];
};

export type Management = {
  available: boolean;
  code: string;
  unlocked: boolean;
  unlock_ttl_seconds: number;
  templates: Template[];
};

export type ModelsView = { management: Management; targets: Target[] };

export type Preview = {
  template: Template;
  target: string;
  expected_revision: number | null;
  version: number;
  published_at: string;
  usable_until: string;
  providers: { provider_id: string; protocol: string; model_id: string }[];
  bindings: { workload: string; model_id: string }[];
};

export type Publication = {
  target: string;
  version: number;
  expected_revision: number | null;
  state: "published" | "replayed";
  deduplicated: boolean;
  usable_until: string;
};

/** One responsible module behind the task centre, including the ones with nothing connected. */
export type TaskSource = {
  id: string;
  kind: string | null;
  connected: boolean;
  state: string;
  code: string;
  records: number | null;
  cancel_code: string | null;
};

export type TaskValue =
  string | number | boolean | null | TaskValue[] | { [key: string]: TaskValue };
export type TaskEvidence = Record<string, TaskValue>;

/** A platform operation record: the status always carries the evidence it was read from. */
export type TaskItem = {
  task_id: string;
  source: string;
  kind: string;
  title: string;
  target: string;
  status: string;
  stage: string;
  code: string;
  created_at: string;
  updated_at: string;
  settled: boolean;
  pending: boolean;
  attention: boolean;
  source_state: string;
  cancel: { supported: boolean; code: string };
  evidence: TaskEvidence;
  module: { page: string | null };
};

export type TaskDetail = TaskItem & {
  request: TaskEvidence;
  timeline: TaskEvidence;
};

export type TasksPage = {
  size: number;
  returned: number;
  has_more: boolean;
  sort: string;
  next_cursor: string | null;
};

export type TasksView = {
  generated_at: string;
  filters: { status: string | null; source: string | null; page_size: number };
  statuses: string[];
  sources: TaskSource[];
  items: TaskItem[];
  page: TasksPage;
};

export type TaskDetailView = {
  generated_at: string;
  task: TaskDetail;
  sources: TaskSource[];
};

const messages: Record<string, string> = {
  web_not_configured: "网页配置尚未启用，模型配置不可管理。",
  unauthorized: "账号或密码不正确，或管理授权已失效。",
  session_expired: "登录已过期，请重新登录。",
  forbidden: "请求未通过会话安全检查，请重新连接。",
  too_many_requests: "尝试次数过多，请一分钟后重试。",
  management_disabled: "该部署未开启网页模型管理。",
  operator_not_authorized: "当前账号没有模型配置权限。",
  management_required: "请先验证管理员密码，再执行模型配置操作。",
  not_found: "找不到该配置模板或版本。",
  version_conflict: "后台版本已变化，已重新读取；请确认后再发布。",
  idempotency_conflict: "这次发布请求与之前的请求不一致，请重新预览后再发布。",
  publication_unverified:
    "这次发布的结果与权威配置不一致，请人工核对后再操作。",
  dependency_unavailable: "后台服务暂时不可用，请稍后重试。",
  invalid_input: "请求内容不符合当前接口要求。",
  authentication_failed: "服务拒绝了密钥。请检查 API Key，保存后再手动测试。",
  endpoint_failed: "服务地址未提供所需接口。请检查 API 基础地址与兼容协议。",
  enumeration_unsupported: "该服务不支持获取模型列表。请手动填写模型 ID。",
  model_not_found: "服务找不到该模型。请检查模型 ID，或重新读取模型列表。",
  timed_out:
    "连接或测试超时。结果可能未知；请检查服务状态，再决定是否手动重试。",
  connection_failed: "无法连接模型服务。请检查服务地址和网络连通性。",
  upstream_invalid:
    "模型服务返回了无法识别的内容。请确认其兼容 Chat Completions。",
  upstream_rejected:
    "模型服务已返回拒绝响应。请检查账号权限和模型支持范围；OpenCode 会话接入由天枢自动处理。",
  provider_unavailable:
    "供应商已停用、缺少密钥或配置已变化。请重新读取并检查。",
  provider_not_tested: "当前配置尚未通过短回复测试，不能设为默认。",
  default_revision_conflict: "默认模型已被其他页面更改。请重新读取后再选择。",
  revision_conflict: "供应商已在其他页面更改。请重新读取后再操作。",
  model_already_added: "此供应商下已添加同类型的同名模型。",
  provider_not_found: "供应商已不存在。请重新读取列表。",
  budget_exceeded: "请求内容过长。请缩短名称或模型 ID。",
  result_unknown:
    "短回复测试的回执无法确认。模型可能已被调用；请人工核对后台状态，避免重复计费。",
  timeout: "平台请求超时。请重新读取状态，再决定是否手动操作。",
  provider_store_unavailable:
    "供应商配置暂时无法读取。请稍后重新连接或联系管理员。",
};

export class WebError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly executionState: "not_started" | "unknown" = "not_started",
  ) {
    super(`${messages[code] ?? "请求失败，请重新连接。"}（${code}）`);
  }
}

export async function call<T>(path: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  try { return await callOperation<T>(path, body, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new WebError(cause.code, cause.status, cause.executionState === "unknown" ? "unknown" : "not_started");
    throw cause;
  }
}

export async function session(signal: AbortSignal): Promise<SessionState> {
  return readSession(signal);
}
