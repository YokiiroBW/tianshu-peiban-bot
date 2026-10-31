import { ApiError, commandInput, apiRead, apiCommandData } from "../../api/client";
import { capabilityReads } from "../../api/capabilityOperations";
import { GLOBAL_SCOPE } from "../../api/useDomainResource";
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
  const value = { ...GLOBAL_SCOPE, ...body } as Record<string, unknown>;
  const serialized = JSON.stringify(value);
  const maximum = ["books/library/import", "books/sources/import"].includes(
    operation,
  )
    ? 44 * 1024 * 1024
    : 6 * 1024 * 1024;
  if (new TextEncoder().encode(serialized).byteLength > maximum)
    throw new MediaError("budget_exceeded", 413);
  const [domain, ...rest] = operation.split("/");
  const path = ["books", "music", "comics"].includes(domain) ? `${domain}/${rest.join("/")}` : `media/${operation}`;
  try {
    if ((capabilityReads as readonly string[]).includes(path)) return await apiRead<T>(path, value, csrf, signal);
    return await apiCommandData<T>(path, { ...commandInput(), ...value }, csrf, signal);
  }
  catch (cause) {
    if (cause instanceof ApiError) {
      if (["unknown", "pending"].includes(cause.executionState ?? "")) throw cause;
      throw new MediaError(cause.code, cause.status);
    }
    throw cause;
  }
}
