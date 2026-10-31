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
  kind?: "read" | "command";
  intent?: CommandInput;
};

function uncertain(path: string, options: Options, status = 0) {
  window.dispatchEvent(new CustomEvent("tianshu:command-unknown", { detail: { operation: path, client_request_id: options.intent?.client_request_id, expected_revision: options.intent?.expected_revision } }));
  return new ApiError("outcome_unknown", status,
    "本次提交结果尚未确认。取消等待不会撤销服务端操作，请查询原任务或当前版本。",
    { operation: path, client_request_id: options.intent?.client_request_id ?? null,
      expected_revision: options.intent?.expected_revision ?? null }, null, "unknown");
}

function endpoint(path: string): string {
  if (!path || path.startsWith("/") || path.includes(":") || path.includes(".."))
    throw new Error("API 路径必须属于当前天枢领域。");
  return API_ROOT + path;
}

const selectors = ["actor_id", "person_id", "conversation_id"] as const;
const getReads = new Set(["session", "identity/conversations", "identity/people", "identity/directory", "models/catalog", "models/attempts", "channels/view"]);

function selectedEndpoint(path: string, body?: object) {
  const url = new URL(endpoint(path), window.location.origin);
  const values = (body ?? {}) as Record<string, unknown>;
  for (const key of selectors) {
    if (!(key in values)) continue;
    const value = values[key];
    if (value !== null && value !== undefined && typeof value !== "string")
      throw new ApiError("invalid_target", 0, "目标选择无效。", null, null, "not_started");
    const supplied = url.searchParams.get(key);
    if (supplied !== null && supplied !== (value ?? ""))
      throw new ApiError("target_mismatch", 0, "同一请求的会话目标不一致。", null, null, "not_started");
    if (typeof value === "string" && value) url.searchParams.set(key, value);
  }
  return url.pathname + url.search;
}

/** One transport for all domains; never retries a mutation. */
export async function apiFetch(path: string, options: Options = {}) {
  const isCommand = options.kind === "command";
  if (options.signal?.aborted) {
    if (!isCommand) throw options.signal.reason ?? new DOMException("Aborted", "AbortError");
    throw new ApiError("request_not_started", 0, "请求尚未发出，已停止提交。",
      { client_request_id: options.intent?.client_request_id ?? null }, null, "not_started");
  }
  const url = selectedEndpoint(path, options.body);
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.body === undefined && options.binary === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
      keepalive: options.keepalive,
      headers: options.body === undefined && options.binary === undefined ? options.headers : {
        ...(options.binary instanceof FormData ? {} : { "Content-Type": options.binary === undefined ? "application/json" : "application/octet-stream" }),
        "X-CSRF-Token": options.csrf ?? "",
        ...options.headers,
      },
      body: options.binary ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
    });
  } catch (cause) {
    if (isCommand) throw uncertain(path, options);
    if (options.signal?.aborted) throw cause;
    throw new ApiError("connection_failed", 0, "连接中断，本次没有取得数据。");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as ApiErrorBody | null;
    const error = payload?.error;
    if (!error && isCommand) throw uncertain(path, options, response.status);
    const code = error?.code ?? "invalid_response";
    if (isCommand && response.status >= 500 && error?.details?.execution_state !== "not_started")
      throw new ApiError("outcome_unknown", response.status, "本次提交结果尚未确认，请查询原任务或当前版本。",
        { ...error?.details, operation: path, client_request_id: options.intent?.client_request_id ?? null,
          expected_revision: options.intent?.expected_revision ?? null, server_error: code }, error?.request_id ?? null, "unknown");
    if (["session_expired", "unauthorized"].includes(code) && path !== "auth/login")
      window.dispatchEvent(new Event("tianshu:session-lost"));
    const execution = error?.details?.execution_state;
    throw new ApiError(
      code, response.status, error?.message ?? "服务返回了无法读取的结果。",
      { ...error?.details, ...(isCommand ? { operation: path, client_request_id: options.intent?.client_request_id ?? null } : {}) }, error?.request_id ?? null,
      typeof execution === "string" ? execution : null,
    );
  }
  return response;
}

export async function apiJson<T>(path: string, options: Options = {}): Promise<T> {
  const response = await apiFetch(path, options);
  if (!response.headers.get("content-type")?.includes("application/json"))
    throw options.kind === "command" ? uncertain(path, options, response.status) : new ApiError("invalid_response", response.status, "服务没有返回有效的数据。");
  try {
    return await response.json() as T;
  } catch {
    throw options.kind === "command" ? uncertain(path, options, response.status) : new ApiError("invalid_response", response.status, "服务返回的数据不完整。");
  }
}

export function apiRead<T>(path: string, body: object, csrf: string, signal: AbortSignal) {
  if (getReads.has(path)) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(body)) if (value !== null && value !== undefined) query.set(key, String(value));
    return apiJson<T>(path + (query.size ? `?${query}` : ""), { csrf, signal, kind: "read" });
  }
  return apiJson<T>(path, { body, csrf, signal, kind: "read" });
}

export function commandInput(revision: number | null = null, requestId = crypto.randomUUID()): CommandInput {
  return { client_request_id: requestId, expected_revision: revision };
}

export async function apiCommand<T>(path: string, body: object & CommandInput, csrf: string, signal: AbortSignal) {
  if (!body.client_request_id || !("expected_revision" in body))
    throw new ApiError("command_identity_required", 0, "缺少本次操作标识或已读取的版本。", null, null, "not_started");
  const options: Options = { body, csrf, signal, kind: "command", intent: body };
  const result = await apiJson<CommandResult<T>>(path, options);
  if (!result || result.client_request_id !== body.client_request_id || !("receipt" in result) || !("data" in result))
    throw uncertain(path, options, 200);
  return result;
}

export function unresolved(command: CommandResult<unknown>) {
  const receipt = command.receipt;
  return !!receipt && (receipt.execution === "pending" || receipt.execution === "running");
}

/** Polls only the original action. It never submits the write again. */
export async function awaitCommand<T>(initial: CommandResult<T>, selector: object, csrf: string,
  signal: AbortSignal, onProgress?: (value: CommandResult<T>) => void): Promise<CommandResult<T>> {
  let command = initial;
  const targets = Object.fromEntries(Object.entries(selector).filter(([key]) => ["actor_id", "person_id", "conversation_id"].includes(key)));
  const started = Date.now();
  while (unresolved(command) && command.action_id) {
    onProgress?.(command);
    if (Date.now() - started > 15 * 60_000) throw new ApiError("operation_pending", 202,
      "任务仍由原执行器处理，可以在任务页继续查询。", { action_id: command.action_id,
        attempt_id: command.attempt_id, client_request_id: command.client_request_id }, null, "pending");
    try {
      await new Promise<void>((resolve, reject) => {
        if (signal.aborted) { reject(signal.reason); return; }
        const aborted = () => { clearTimeout(timer); reject(signal.reason); };
        const timer = window.setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, 1200);
        signal.addEventListener("abort", aborted, { once: true });
      });
      const current = await apiRead<{ receipt: CommandResult<T>["receipt"]; result_revision: number }>(
        "capabilities/get", { ...targets, action_id: command.action_id }, csrf, signal);
      if (!current.receipt) throw new Error("receipt_missing");
      command = { ...command, receipt: current.receipt, data: current.receipt.result as T,
        revision: typeof current.receipt.result.revision === "number" ? current.receipt.result.revision : command.revision };
    } catch (cause) {
      if (cause instanceof ApiError && cause.executionState === "pending") throw cause;
      throw new ApiError("outcome_unknown", 202, "已提交原任务，当前未取得最新结果。取消等待不会撤销任务。",
        { action_id: command.action_id, attempt_id: command.attempt_id,
          client_request_id: command.client_request_id }, null, "unknown");
    }
  }
  onProgress?.(command);
  const receipt = command.receipt;
  if (receipt?.execution === "unknown" || receipt?.effect === "unknown" || receipt?.delivery === "unknown")
    throw new ApiError("outcome_unknown", 202, "原任务结果尚未确认，请查询原任务。",
      { action_id: command.action_id, attempt_id: command.attempt_id, receipt,
        client_request_id: command.client_request_id }, null, "unknown");
  if (receipt && ["failed", "cancelled"].includes(receipt.execution))
    throw new ApiError(receipt.error_code ?? receipt.execution, 409,
      receipt.execution === "cancelled" ? "原任务已取消。" : "操作未完成，表单内容已保留。",
      { action_id: command.action_id, attempt_id: command.attempt_id, receipt,
        client_request_id: command.client_request_id }, null, receipt.execution);
  return command;
}

/** The operation is declared a command by its domain client, never guessed from response fields. */
export async function apiCommandData<T>(path: string, body: object & CommandInput, csrf: string, signal: AbortSignal): Promise<T> {
  const initial = await apiCommand<T>(path, body, csrf, signal);
  window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: initial }));
  const command = await awaitCommand(initial, body, csrf, signal, (value) =>
    window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: value })));
  return command.data;
}

export async function apiBlob(path: string, body: object, csrf: string, signal: AbortSignal) {
  return (await apiFetch(path, { body, csrf, signal })).blob();
}
