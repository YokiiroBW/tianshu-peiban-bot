import { integrationPost } from "../../app/integrationApi";

export function pluginCall<T>(
  operation: string,
  body: object,
  csrf: string,
  signal: AbortSignal,
) {
  return integrationPost<T>(`plugins/${operation}`, body, csrf, signal);
}
