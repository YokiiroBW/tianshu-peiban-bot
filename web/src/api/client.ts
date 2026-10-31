import type { ApiErrorBody, CommandInput, CommandResult } from "./contracts";

export const API_ROOT = "/api/v1/";

export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string = code,
    readonly details: Record<string, unknown> | null = null,
    readonly requestId: string | null = null,
    readonly executionState: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Options = {
  body?: object;
  csrf?: string;
  signal?: AbortSignal;
  keepalive?: boolean;
  binary?: BodyInit;
  headers?: Record<string, string>;
};

function endpoint(path: string): string {
  if (!path || path.startsWith("/") || path.includes(":") || path.includes(".."))
    throw new Error("API 路径必须属于当前天枢领域。");
  return API_ROOT + path;
}

/** One transport for all domains; never retries a mutation. */
export async function apiFetch(path: string, options: Options = {}) {
  let response: Response;
  try {
    response = await fetch(endpoint(path), {
      method: options.body === undefined && options.binary === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
      keepalive: options.keepalive,
      headers: options.body === undefined && options.binary === undefined ? options.headers : {
        "Content-Type": options.binary === undefined ? "application/json" : "application/octet-stream",
        "X-CSRF-Token": options.csrf ?? "",
        ...options.headers,
      },
      body: options.binary ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
    });
  } catch (cause) {
    if (options.signal?.aborted) throw cause;
    throw new ApiError(
      options.body === undefined ? "connection_failed" : "outcome_unknown",
      0,
      options.body === undefined
        ? "连接中断，本次没有取得数据。"
        : "连接中断，提交结果尚未确认。请查询原任务或当前版本。",
      null, null, options.body === undefined ? null : "unknown",
    );
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as ApiErrorBody | null;
    const error = payload?.error;
    const code = error?.code ?? "invalid_response";
    if (["session_expired", "unauthorized"].includes(code) && path !== "auth/login")
      window.dispatchEvent(new Event("tianshu:session-lost"));
    const execution = error?.details?.execution_state;
    throw new ApiError(
      code, response.status, error?.message ?? "服务返回了无法读取的结果。",
      error?.details ?? null, error?.request_id ?? null,
      typeof execution === "string" ? execution : null,
    );
  }
  return response;
}

export async function apiJson<T>(path: string, options: Options = {}): Promise<T> {
  const response = await apiFetch(path, options);
  if (!response.headers.get("content-type")?.includes("application/json"))
    throw new ApiError("invalid_response", response.status, "服务没有返回有效的数据。");
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError("invalid_response", response.status, "服务返回的数据不完整。");
  }
}

export function apiRead<T>(path: string, body: object, csrf: string, signal: AbortSignal) {
  return apiJson<T>(path, { body, csrf, signal });
}

export function commandInput(revision: number | null = null, requestId = crypto.randomUUID()): CommandInput {
  return { client_request_id: requestId, expected_revision: revision };
}

export function apiCommand<T>(path: string, body: object & CommandInput, csrf: string, signal: AbortSignal) {
  return apiJson<CommandResult<T>>(path, { body, csrf, signal });
}

/** Existing domain views use the payload while the execution record remains observable. */
export async function apiData<T>(path: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  const input = "client_request_id" in body && !("expected_revision" in body)
    ? { ...body, expected_revision: null } : body;
  const result = await apiRead<T | CommandResult<T>>(path, input, csrf, signal);
  if (result && typeof result === "object" && "client_request_id" in result && "data" in result && "receipt" in result) {
    const command = result as CommandResult<T>;
    window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: command }));
    if (command.receipt?.execution === "unknown" || command.receipt?.effect === "unknown")
      throw new ApiError("outcome_unknown", 202, "操作结果尚未确认，请查询原任务。", { action_id: command.action_id, attempt_id: command.attempt_id, receipt: command.receipt }, null, "unknown");
    return command.data;
  }
  return result as T;
}

export async function apiBlob(path: string, body: object, csrf: string, signal: AbortSignal) {
  return (await apiFetch(path, { body, csrf, signal })).blob();
}
