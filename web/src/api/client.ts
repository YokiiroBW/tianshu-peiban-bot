import type { ApiErrorBody, CommandInput, CommandResult } from "./contracts";
import { requestId } from "../app/requestId";

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

let dataGeneration: string | null = null;
let generationInvalidated = false;

function generationChanged(nextGeneration?: string) {
  if (generationInvalidated) return;
  generationInvalidated = true;
  try {
    const staleKeys = Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
      .filter((key): key is string => Boolean(key) && ["tianshu-workspace-role:", "tianshu-memory-role:", "tianshu-infra-control:"].some((prefix) => key!.startsWith(prefix)));
    staleKeys.forEach((key) => sessionStorage.removeItem(key));
    if (nextGeneration) sessionStorage.setItem("tianshu-data-generation", nextGeneration);
    else sessionStorage.removeItem("tianshu-data-generation");
    sessionStorage.setItem("tianshu-generation-notice", "1");
  } catch {
    // A reload still clears in-memory business state when storage is unavailable.
  }
  window.dispatchEvent(new Event("tianshu:data-generation-changed"));
  const route = window.location.hash.split("?")[0] || "#/workbench";
  window.history.replaceState(null, "", window.location.pathname + window.location.search + route);
  window.location.reload();
}

/** Only the session establishes the active installation generation. */
export function establishDataGeneration(value: string) {
  if (!value || generationInvalidated)
    throw new ApiError("data_generation_unavailable", 0, "当前数据代际尚未取得，请重新连接。", null, null, "not_started");
  let previous = dataGeneration;
  try {
    previous ??= sessionStorage.getItem("tianshu-data-generation");
  } catch {
    // The live in-memory generation still protects this page.
  }
  if (previous && previous !== value) {
    generationChanged(value);
    throw new ApiError("data_generation_changed", 409, "数据已切换，正在重新读取。", null, null, "not_started");
  }
  dataGeneration = value;
  try {
    sessionStorage.setItem("tianshu-data-generation", value);
  } catch {
    // In-memory generation remains authoritative for this page.
  }
}

/** Native media, downloads and frames cannot attach request headers. */
export function nativeResourceUrl(value: string): string {
  try {
    const url = new URL(value, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith(API_ROOT)) return value;
    if (!dataGeneration || generationInvalidated) return "about:blank";
    url.searchParams.set("data_generation", dataGeneration);
    return url.pathname + url.search + url.hash;
  } catch {
    return value;
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

export type CommandTarget = { actor_id: string | null; person_id: string | null; conversation_id: string | null };
export function commandTarget(value: object = {}, path = ""): CommandTarget {
  const query = new URLSearchParams(path.split("?")[1] ?? "");
  const values = value as Record<string, unknown>;
  return Object.fromEntries(["actor_id", "person_id", "conversation_id"].map((key) =>
    [key, typeof values[key] === "string" ? values[key] : query.get(key)])) as CommandTarget;
}
export function publishCommandResult(value: CommandResult<unknown>, target: object, operation?: string) {
  window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: { ...value, target: commandTarget(target), operation, capability_id: (target as Record<string, unknown>).capability_id } }));
}
function uncertain(path: string, options: Options, status = 0) {
  window.dispatchEvent(new CustomEvent("tianshu:command-unknown", { detail: { operation: path, client_request_id: options.intent?.client_request_id, expected_revision: options.intent?.expected_revision, target: commandTarget(options.body, path), capability_id: (options.body as Record<string, unknown> | undefined)?.capability_id, action_id: (options.body as Record<string, unknown> | undefined)?.action_id } }));
  return new ApiError("outcome_unknown", status,
    "本次提交结果尚未确认。取消等待不会撤销服务端操作，请查询原任务或当前版本。",
    { operation: path, client_request_id: options.intent?.client_request_id ?? null,
      expected_revision: options.intent?.expected_revision ?? null, target: commandTarget(options.body, path) }, null, "unknown");
}

function endpoint(path: string): string {
  if (!path || path.startsWith("/") || path.includes(":") || path.includes(".."))
    throw new Error("API 路径必须属于当前天枢领域。");
  return API_ROOT + path;
}

const selectors = ["actor_id", "person_id", "conversation_id"] as const;
const getReads = new Set(["session", "identity/conversations", "identity/people", "identity/directory", "identity/publication-recipients", "identity/role-administrator", "models/catalog", "models/attempts", "channels/view", "channels/group-names"]);

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
  const bootstrap = ["session", "setup", "auth/login"].includes(path.split("?")[0]);
  if (generationInvalidated || (!dataGeneration && !bootstrap))
    throw new ApiError("data_generation_unavailable", 0, "当前数据代际尚未取得，请重新连接。", null, null, "not_started");
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
      headers: {
        ...(options.body === undefined && options.binary === undefined ? {} : {
          ...(options.binary instanceof FormData ? {} : { "Content-Type": options.binary === undefined ? "application/json" : "application/octet-stream" }),
          "X-CSRF-Token": options.csrf ?? "",
        }),
        ...options.headers,
        ...(dataGeneration ? { "X-Tianshu-Data-Generation": dataGeneration } : {}),
      },
      body: options.binary ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
    });
  } catch (cause) {
    if (isCommand) throw uncertain(path, options);
    if (options.signal?.aborted) throw cause;
    throw new ApiError("connection_failed", 0, "连接中断，本次没有取得数据。");
  }
  const observedGeneration = response.headers.get("X-Tianshu-Data-Generation");
  if (generationInvalidated) {
    if (isCommand) throw uncertain(path, options, response.status);
    throw new ApiError("data_generation_changed", 409, "数据已切换，正在重新读取。", null, null, "not_started");
  }
  if (dataGeneration && observedGeneration && observedGeneration !== dataGeneration) {
    const payload = response.status === 409 ? await response.clone().json().catch(() => null) as ApiErrorBody | null : null;
    const rejected = payload?.error?.code === "data_generation_changed";
    generationChanged(observedGeneration);
    if (isCommand && !rejected) throw uncertain(path, options, response.status);
    throw new ApiError("data_generation_changed", 409, "数据已切换，正在重新读取。", null, null, "not_started");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as ApiErrorBody | null;
    const error = payload?.error;
    if (!error && isCommand) throw uncertain(path, options, response.status);
    const code = error?.code ?? "invalid_response";
    if (code === "data_generation_changed") {
      generationChanged(observedGeneration ?? undefined);
      throw new ApiError(code, response.status, "数据已切换，正在重新读取。", error?.details ?? null, error?.request_id ?? null, "not_started");
    }
    if (isCommand && response.status >= 500 && error?.details?.execution_state !== "not_started") {
      uncertain(path, options, response.status);
      throw new ApiError("outcome_unknown", response.status, "本次提交结果尚未确认，请查询原任务或当前版本。",
        { ...error?.details, operation: path, client_request_id: options.intent?.client_request_id ?? null,
          expected_revision: options.intent?.expected_revision ?? null, server_error: code, target: commandTarget(options.body, path) }, error?.request_id ?? null, "unknown");
    }
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

export function commandInput(revision: number | null = null, clientRequestId: string = requestId()): CommandInput {
  return { client_request_id: clientRequestId, expected_revision: revision };
}

export async function apiCommand<T>(path: string, body: object & CommandInput, csrf: string, signal: AbortSignal) {
  if (!body.client_request_id || !("expected_revision" in body))
    throw new ApiError("command_identity_required", 0, "缺少本次操作标识或已读取的版本。", null, null, "not_started");
  const options: Options = { body, csrf, signal, kind: "command", intent: body };
  const result = await apiJson<CommandResult<T>>(path, options);
  if (!result || result.client_request_id !== body.client_request_id || !("receipt" in result) || !("data" in result))
    throw uncertain(path, options, 200);
  publishCommandResult(result, commandTarget(body, path), path);
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
  publishCommandResult(initial, body, path);
  const command = await awaitCommand(initial, body, csrf, signal, (value) =>
    publishCommandResult(value, body, path));
  return command.data;
}

export async function apiBlob(path: string, body: object, csrf: string, signal: AbortSignal) {
  return (await apiFetch(path, { body, csrf, signal })).blob();
}
