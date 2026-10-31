import { ApiError, apiRead, apiCommandData, commandInput } from "../../api/client";
import { commandIntent, finishIntent } from "../../api/intents";
import { GLOBAL_SCOPE } from "../../api/useDomainResource";

const reads = new Set(["view", "catalog", "actors", "schema", "installation", "invocations", "invocation", "logs", "view-content", "schedules"]);

/** Management is global. An invocation supplies its explicitly selected role/conversation. */
export async function pluginCall<T>(operation: string, body: object, csrf: string, signal: AbortSignal): Promise<T> {
  const path = `plugins/${operation}`;
  const value = { ...GLOBAL_SCOPE, ...body } as Record<string, unknown>;
  if (reads.has(operation) && !(operation === "schedules" && "operation" in value))
    return apiRead<T>(path, value, csrf, signal);
  const revision = typeof value.expected_revision === "number" ? value.expected_revision : null;
  const semantic = { ...value };
  delete semantic.client_request_id;
  const intent = commandIntent(csrf, "plugins", path, semantic, revision);
  try {
    const result = await apiCommandData<T>(path, { ...value, ...commandInput(revision, intent.id) }, csrf, signal);
    finishIntent(intent.key);
    return result;
  } catch (cause) {
    if (!(cause instanceof ApiError && ["unknown", "pending"].includes(cause.executionState ?? ""))) finishIntent(intent.key);
    throw cause;
  }
}
