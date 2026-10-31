import { apiData, ApiError } from "../api/client";
export { ApiError as IntegrationError } from "../api/client";
export function integrationPost<T>(path: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  return apiData<T>(path, body, csrf, signal);
}

export function readFailure(cause: unknown) {
  if (cause instanceof Error) return cause.message;
  return "连接中断；本次没有取得数据。";
}

export function writeFailure(cause: unknown) {
  return !(cause instanceof ApiError) ||
    cause.executionState === "unknown"
    ? `写入结果尚未确认，请先查询原任务或实际版本；同内容重试会核对原操作。${readFailure(cause)}`
    : readFailure(cause);
}

export function deploymentFailure(code: string) {
  return [
    "not_configured",
    "knowledge_not_configured",
    "life_not_configured",
    "operator_not_authorized",
    "unauthorized",
    "forbidden",
  ].includes(code);
}
