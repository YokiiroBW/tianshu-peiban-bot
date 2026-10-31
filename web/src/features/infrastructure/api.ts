import { useState } from "react";
import { useAuth } from "../../app/Auth";
import { apiRead } from "../../api/client";
import { capabilityReads } from "../../api/capabilityOperations";
import { useDomainCommand, useDomainResource, GLOBAL_SCOPE } from "../../api/useDomainResource";

/** Infrastructure management has an explicit global scene and the shared Action monitor. */
export function useInfrastructureQuery<T>(operation: string, body: object, enabled = true) {
  const resource = useDomainResource<T>(`infrastructure/${operation}`, { ...GLOBAL_SCOPE, ...body }, enabled, 3000);
  return { ...resource, reload: resource.reload };
}

export function useInfrastructureAction() {
  const command = useDomainCommand<unknown>("infrastructure", "global");
  const { session } = useAuth();
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState("");
  async function send<T>(operation: string, body: object): Promise<T | null> {
    const path = `infrastructure/${operation}`;
    if ((capabilityReads as readonly string[]).includes(path)) {
      if (!session?.csrf) return null;
      setReading(true); setReadError("");
      try { return await apiRead<T>(path, { ...GLOBAL_SCOPE, ...body }, session.csrf, AbortSignal.timeout(15000)); }
      catch (cause) { setReadError(cause instanceof Error ? cause.message : "读取失败"); return null; }
      finally { setReading(false); }
    }
    const revision = (body as { expected_revision?: number }).expected_revision ?? null;
    const answer = await command.run(`infrastructure/${operation}`, body, revision);
    return answer ? answer.data as T : null;
  }
  return { send, pending: command.busy || reading, error: command.error || readError };
}
