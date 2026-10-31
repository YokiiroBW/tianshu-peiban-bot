import { apiData, apiJson, ApiError } from "../../api/client";
import { readSession } from "../../api/session";
export type Session = {
  authenticated: boolean;
  csrf: string;
  username?: string;
  conversations?: { id: string; label: string; actors: string[] }[];
  dialogue?: {
    available: boolean;
    code: string;
    model: string;
    actor_models?: Record<string, boolean>;
  };
};

const errors: Record<string, string> = {
  web_not_configured: "网页登录尚未配置。请由本机管理员配置账号与服务入口。",
  core_web_not_connected: "回复通道尚未接通，暂时不能发送消息。",
  unauthorized: "账号或密码不正确，或登录权限已失效。",
  session_expired: "登录已过期，请重新登录。",
  forbidden: "请求未通过会话安全检查，请重新连接。",
  too_many_requests: "尝试次数过多，请一分钟后重试。",
  dependency_unavailable: "后台服务暂时不可用，请稍后重新连接。",
  model_not_configured: "模型尚未配置，暂时不能发送。",
  version_conflict: "后台版本已变化，请刷新后再操作。",
  scope_changed: "会话授权范围已变化，请重新连接。",
  provider_unavailable: "所选模型当前不可用，请选择其他模型或检查供应商。",
  role_configuring: "角色正在应用设置，请完成后再修改。",
  not_found: "角色或所选内容已不存在，请刷新列表。",
  invalid_input: "设置格式有误，请检查名称和所选项目。",
  idempotency_conflict: "本次操作状态已变化，请刷新后重试。",
  budget_exceeded: "对话内容超过当前读取预算。",
};

export class ConversationError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T = Session>(
  path: string,
  signal: AbortSignal,
  body?: object,
  csrf?: string,
) {
  try {
    if (path === "session") return await readSession(signal) as T;
    return body === undefined
      ? await apiJson<T>(path, { signal })
      : await apiData<T>(path, body, csrf ?? "", signal);
  } catch (cause) {
    if (cause instanceof ApiError) throw new ConversationError(cause.code, errors[cause.code] ?? cause.message);
    throw cause;
  }
}
