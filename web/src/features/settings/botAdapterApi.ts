import { ApiError } from "../../api/client";
import { callOperation } from "../../api/operations";

export type AdapterKind = "astrbot" | "nonebot" | "onebot";
export function adapterLabel(kind: AdapterKind): string {
  return { astrbot: "AstrBot", nonebot: "NoneBot", onebot: "OneBot / NapCat" }[
    kind
  ];
}
export type AdapterAccount = { id: string; platform: string; label: string };
export type AdapterConnection = {
  id: string;
  name: string;
  adapter: AdapterKind;
  address: string;
  account_id: string;
  conversation: { kind: "group" | "private"; id: string };
  allowed_authors: string[];
  actor_id: string;
  enabled: boolean;
  revision: number;
  state: "draft" | "disabled" | "ready" | "degraded" | "unknown";
  last_error: string | null;
  last_checked_at: string | null;
};
export type AdapterView = {
  available: boolean;
  unlocked: boolean;
  actors: { id: string; label: string }[];
  connections: AdapterConnection[];
};
export type AdapterProbe = {
  draft_id: string;
  expires_at: string;
  protocol: string;
  instance_id: string;
  accounts: AdapterAccount[];
};

const messages: Record<string, string> = {
  web_not_configured: "此部署尚未提供机器人适配器管理接口。",
  management_disabled: "此部署尚未启用机器人适配器管理。",
  not_configured: "此部署尚未启用机器人适配器管理。",
  operator_not_authorized: "当前账号没有机器人接入管理权限。",
  management_required: "管理员解锁已失效，请重新输入密码。",
  session_expired: "登录已过期，请重新登录。",
  unauthorized: "验证失败。请检查插件连接密钥或管理员登录状态。",
  forbidden: "当前操作未获授权，请检查管理员权限。",
  invalid_input: "输入不符合服务端要求，请检查地址、账号和允许范围。",
  not_found: "草稿或连接已失效，请刷新后重新检测。",
  draft_expired: "检测草稿已过期，请重新检测连接。",
  version_conflict: "连接已在另一窗口改变，请刷新后核对。",
  result_unknown:
    "上次配置操作尚未确认。请刷新状态；若连接仍待核对，可显式恢复。",
  idempotency_conflict: "本次请求与已有记录冲突，请刷新后核对。",
  adapter_unauthorized: "插件拒绝了连接密钥，请核对宿主插件中配置的密钥。",
  adapter_not_installed:
    "该地址没有天枢机器人插件，请在所选宿主安装并启用插件。",
  adapter_incompatible: "插件协议或版本不兼容，请更新所选宿主的天枢插件。",
  adapter_unavailable: "插件暂时无法处理请求，请检查插件运行状态后重试。",
  adapter_unreachable: "无法连接插件地址，请检查地址、网络及 HTTPS 证书。",
  adapter_redirect: "插件地址发生跳转，请填写插件直接监听的地址。",
  external_target_changed:
    "插件地址的网络目标已变化，本次连接已拒绝；请联系管理员核对地址与 DNS。",
  bot_role_not_approved:
    "所选角色尚未获后台批准接入机器人，请联系管理员核对角色。",
  queue_full: "机器人连接或检测草稿已达上限，请稍后再试或联系管理员清理。",
  dependency_unavailable:
    "插件或后台暂时不可达，请检查地址、插件运行状态与网络。",
  busy: "插件暂时忙碌，请稍后手动重试。",
  protocol_mismatch: "插件协议版本不兼容，请更新插件。",
  unsupported_version: "插件版本不兼容，请更新插件。",
  invalid_upstream: "插件返回了无法识别的内容，请确认地址指向天枢插件。",
  connection_failed: "无法连接插件。请检查地址和插件运行状态。",
  timeout: "连接超时，请检查插件地址和网络。",
};

export class AdapterApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    readonly executionState: string | null,
  ) {
    super(`${messages[code] ?? "操作未完成，请检查后台状态。"}（${code}）`);
  }
}

export function adapterErrorMessage(code: string) {
  return messages[code] ?? code;
}

export async function adapterPost<T>(
  operation: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
): Promise<T> {
  try { return await callOperation<T>(`channels/adapters/${operation}`, body, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new AdapterApiError(cause.code, cause.status, cause.executionState);
    throw cause;
  }
}
