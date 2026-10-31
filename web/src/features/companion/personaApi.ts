import { apiData, ApiError } from "../../api/client";
import { readSession } from "../../api/session";
/**
 * Same-origin console calls for the read-only persona page.
 *
 * The browser posts one of four fixed bodies and never chooses an operation, a page size, a
 * character service, an endpoint or a scope. The server's answer carries no credential, no
 * endpoint and no local path; what is typed here is exactly what the console projects. Every
 * refusal keeps the server's own code, so the page can say what actually happened.
 */

export type SessionState = {
  authenticated: boolean;
  csrf: string;
  username?: string;
};

/**
 * The page's own words for the codes this console actually sends.
 *
 * `personas_not_configured`, `personas_disabled` and `persona_read_required` are the deployment
 * and this account's permission; the rest are one failed or stale read. None of them is ever
 * rendered as an empty list.
 *
 * `forbidden` is the one code this console uses for two different refusals - its own session, host
 * or CSRF check, and a character service that refuses this deployment's own reading identity - so
 * its sentence names both possibilities instead of guessing at one of them.
 */
export const messages: Record<string, string> = {
  personas_not_configured: "这个部署没有登记人格页。",
  personas_disabled: "这个部署没有启用人格页。",
  persona_read_required: "当前账号没有读取人格的权限。",
  persona_write_required: "当前账号没有这项人格管理权限。",
  unauthorized: "账号或密码不正确，或登录已失效。",
  session_expired: "登录已过期，请重新登录。",
  forbidden:
    "这次请求没有被接受：会话安全检查未通过，或角色服务拒绝了这个角色。",
  too_many_requests: "同时读取的请求过多，请稍后重试。",
  cursor_conflict: "这一页的续读位置已失效。",
  version_conflict: "人格在这期间已经变化。",
  not_found: "该版本不存在，或不属于当前角色。",
  invalid_input: "请求内容不符合当前接口要求。",
  invalid_upstream: "角色服务返回了无法使用的回答。",
  timeout: "角色服务没有在时限内回答。",
  dependency_unavailable: "角色服务暂时读不到。",
  budget_exceeded: "读取内容超过当前预算。",
  web_not_configured: "网页登录尚未配置。",
};

export const conflictCodes = ["cursor_conflict", "version_conflict"];
export const sessionCodes = ["session_expired", "unauthorized"];

export class PersonaError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(word(code));
  }
}

/** The page's sentence for one code, always with the code itself so nothing is guessed at. */
export function word(code: string) {
  return `${messages[code] ?? "请求失败，请重新连接。"}（${code}）`;
}

async function post(
  path: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
) {
  try { return await apiData(path, body, csrf, signal); }
  catch (cause) { if (cause instanceof ApiError) throw new PersonaError(cause.code, cause.status); throw cause; }
}

/** One of the four fixed reads. The body is built by the caller from the page's own state. */
export async function read<T>(
  path: "catalog" | "history" | "revision" | "compare",
  body: object,
  csrf: string,
  signal: AbortSignal,
) {
  return (await post(`personas/${path}`, body, csrf, signal)) as T;
}

export async function author<T>(
  path: "profiles" | "view" | "create" | "save" | "apply",
  body: object,
  csrf: string,
  signal: AbortSignal,
) {
  return (await post(`personas/${path}`, body, csrf, signal)) as T;
}

export async function session(signal: AbortSignal): Promise<SessionState> { return readSession(signal); }

export async function login(
  username: string,
  password: string,
  csrf: string,
  signal: AbortSignal,
): Promise<void> {
  await post("auth/login", { username, password }, csrf, signal);
}

export async function logout(csrf: string, signal: AbortSignal): Promise<void> {
  await post("auth/logout", {}, csrf, signal);
}

/** A disconnected browser is not an empty page either: the message says so in words. */
export function reason(cause: unknown) {
  if (cause instanceof PersonaError) return cause.message;
  if (cause instanceof Error && cause.name === "AbortError") return "";
  return "请求中断，请重新连接。";
}
