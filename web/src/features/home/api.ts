import { apiData, ApiError } from "../../api/client";
import { readSession } from "../../api/session";
/** Same-origin web-console calls for the household devices section.
 *
 * The browser never sends a Home Assistant address, a credential, an entity id or a service
 * name: it names one registered action template, the reading revision it actually saw and a
 * client id. Everything else is resolved from deployment settings on the server.
 */

export type SessionState = {
  authenticated: boolean;
  csrf: string;
  username?: string;
};

export type Reading = {
  availability: string;
  code: string;
  state: string | null;
  value: number | string | null;
  unit: string | null;
  observed_at: string | null;
  attempt_at: string | null;
  revision: number;
};

export type ActionTemplate = {
  template_id: string;
  label: string;
  service: string;
};

export type Entity = Reading & {
  entity_id: string;
  label: string;
  kind: string;
  control: boolean;
  templates: ActionTemplate[];
};

export type Observation = {
  status: string;
  state: string | null;
  observed_at: string | null;
};

export type Control = {
  control_id: string;
  template_id: string;
  label: string;
  entity_id: string;
  service: string;
  requested_state: string;
  acceptance: string;
  code: string;
  target_reported: boolean;
  recorded_at: string | null;
  observation: Observation;
  revision: number;
};

export type Connector = {
  available: boolean;
  code: string;
  enabled: boolean;
  control_available: boolean;
  unlocked: boolean;
  unlock_ttl_seconds: number;
  stale_after_seconds: number;
  timeout_seconds: number;
  entities: number;
  templates: number;
  readable: boolean;
};

export type HomeView = {
  connector: Connector;
  entities: Entity[];
  controls: Control[];
};

export type ControlResult = Omit<Control, "observation" | "revision"> & {
  deduplicated: boolean;
  durable: boolean;
};

const messages: Record<string, string> = {
  web_not_configured: "网页配置尚未启用，家庭设备不可用。",
  home_disabled: "这个部署没有登记家庭设备连接。",
  control_disabled: "这个部署只读取设备状态，未开启开关控制。",
  operator_not_authorized: "当前账号没有家庭设备控制权限。",
  control_required: "请先验证管理员密码，再执行设备动作。",
  unauthorized: "账号、密码或设备控制授权不正确。",
  session_expired: "登录已过期，请重新登录。",
  forbidden: "请求未通过会话安全检查，请重新连接。",
  too_many_requests: "尝试次数过多，请一分钟后重试。",
  not_found: "找不到这个设备动作模板。",
  state_conflict: "设备状态已被改变，已重新读取；请确认当前状态后再操作。",
  idempotency_conflict:
    "这次请求与之前的请求不一致，请重新读取设备状态后再操作。",
  device_credential_missing: "连接器没有可用的设备凭据，无法读取或控制设备。",
  device_unavailable: "无法连接 Home Assistant，设备状态暂不可用。",
  device_timeout: "Home Assistant 未在时限内响应，本次结果未知，不会自动重发。",
  device_redirect: "Home Assistant 返回了重定向，连接器已拒绝跟随。",
  device_unauthorized: "Home Assistant 拒绝了本次凭据。",
  device_rejected: "Home Assistant 拒绝了这次服务调用，指令未送达。",
  device_failed: "Home Assistant 未能完成这次服务调用，结果未知。",
  device_invalid_response: "Home Assistant 的响应无法读取，本次结果未知。",
  receipt_unreadable: "指令回执无法读取，本次结果未知，不会自动重发。",
  control_unverified:
    "这次指令的结果无法确认（可能已发出、也可能仍在途中），不会自动重发；请重新读取设备状态后再决定。",
  control_in_progress:
    "同一个请求仍在执行中，结果尚未确定；请稍后重新读取设备状态。",
  device_missing: "登记的实体在 Home Assistant 中不存在。",
  dependency_unavailable: "平台存储暂时不可用，请稍后重试。",
  invalid_input: "请求内容不符合当前接口要求。",
  timeout: "请求超时，请重新读取设备状态。",
  budget_exceeded: "请求内容过大。",
};

export class WebError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(`${messages[code] ?? "请求失败，请重新连接。"}（${code}）`);
  }
}

export async function call<T>(path: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  try { return await apiData<T>(path, body, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new WebError(cause.code, cause.status);
    throw cause;
  }
}

export async function session(signal: AbortSignal): Promise<SessionState> {
  return readSession(signal);
}

export const availabilityLabels: Record<
  string,
  { tone: "blue" | "yellow" | "red" | "gray"; label: string; detail: string }
> = {
  current: { tone: "blue", label: "当前", detail: "最近一次读取在有效期内。" },
  stale: {
    tone: "yellow",
    label: "读数已过期",
    detail: "采样时间已超过有效期，显示的是上次观测值。",
  },
  offline: {
    tone: "gray",
    label: "无法读取",
    detail: "连接器未能从 Home Assistant 读到这个实体。",
  },
  unavailable: {
    tone: "gray",
    label: "设备不可用",
    detail: "Home Assistant 报告该实体当前不可用。",
  },
  unknown: {
    tone: "gray",
    label: "状态未知",
    detail: "读到的状态无法解释，未按关闭或零显示。",
  },
  never_read: {
    tone: "gray",
    label: "尚未读取",
    detail: "还没有这个实体的观测记录。",
  },
};

export const acceptanceLabels: Record<string, string> = {
  pending: "已提交，尚无回执",
  executing: "正在执行，尚无回执",
  accepted: "已受理，等待设备反馈",
  observed: "已观察到目标状态（未收到指令回执）",
  unknown: "结果未知，不会自动重发",
  rejected: "未送达（Home Assistant 拒绝）",
};

export const observationLabels: Record<string, string> = {
  pending: "尚无后续观测",
  confirmed: "后续观测已确认目标状态",
  contradicted: "后续观测与目标状态不一致",
  unavailable: "设备当前不可用",
  unknown: "后续观测无法判断",
  unreadable: "后续读取失败",
  not_sent: "没有发出指令",
};

export const stateLabels: Record<string, string> = {
  on: "已开启",
  off: "已关闭",
};

export function readingText(entity: Entity): string {
  const value = entity.state
    ? (stateLabels[entity.state] ?? entity.state)
    : entity.value !== null && entity.value !== undefined
      ? `${entity.value}${entity.unit ?? ""}`
      : "状态不可用";
  if (entity.availability === "never_read") return "尚未读取";
  // A reading kept from before the connector lost contact is never shown as the current one.
  if (entity.availability === "offline")
    return value === "状态不可用" ? value : `上次读数：${value}`;
  return value;
}

export function shortTime(value: string | null): string {
  if (!value) return "没有采样时间";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return "采样时间无法解析";
  return moment.toLocaleString("zh-CN", { hour12: false });
}
