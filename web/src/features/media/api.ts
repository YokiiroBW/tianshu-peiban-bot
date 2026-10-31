import { ApiError } from "../../api/client";
import { callOperation } from "../../api/operations";
import { errorWords } from "./wording";

export class MediaError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(
      `${errorWords[code] ?? "本次操作未完成，请检查当前状态后重试。"}（${code}）`,
    );
  }
}
export async function mediaCall<T>(
  operation: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
): Promise<T> {
  const serialized = JSON.stringify(body);
  const maximum = ["books/library/import", "books/sources/import"].includes(
    operation,
  )
    ? 44 * 1024 * 1024
    : 6 * 1024 * 1024;
  if (new TextEncoder().encode(serialized).byteLength > maximum)
    throw new MediaError("budget_exceeded", 413);
  const [domain, ...rest] = operation.split("/");
  const path = ["books", "music", "comics"].includes(domain) ? `${domain}/${rest.join("/")}` : `media/${operation}`;
  try { return await callOperation<T>(path, body, csrf, signal); }
  catch (cause) {
    if (cause instanceof ApiError) throw new MediaError(cause.code, cause.status);
    throw cause;
  }
}
