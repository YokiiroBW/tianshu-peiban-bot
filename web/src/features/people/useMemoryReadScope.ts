import { useEffect, useEffectEvent } from "react";
import { integrationPost } from "../../app/integrationApi";
import type { Role } from "./types";

/** Reuse Memory's read-scope proof; a successful probe does not reset pagination. */
export function useMemoryReadScope({
  csrf,
  role,
  enabled,
  scopeVersion,
  onSuspend,
  onResume,
  onFailure,
}: {
  csrf: string;
  role: Role | null;
  enabled: boolean;
  scopeVersion: number | null;
  onSuspend: () => void;
  onResume: () => void;
  onFailure: (cause: unknown) => void;
}) {
  const suspend = useEffectEvent(onSuspend);
  const accept = useEffectEvent((version: number, resumed: boolean) => {
    if (resumed || (scopeVersion !== null && scopeVersion !== version)) {
      if (!resumed) onSuspend();
      onResume();
    }
  });
  const fail = useEffectEvent(onFailure);
  useEffect(() => {
    if (!csrf || !enabled || !role?.available) return;
    let pending: AbortController | null = null;
    const verify = async (resumed = false) => {
      if (document.hidden || pending) return;
      const controller = new AbortController();
      pending = controller;
      try {
        const result = await integrationPost<{ scope_version: number }>(
          "memory/overview",
          { role_id: role.id, role_version: role.version },
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted) accept(result.scope_version, resumed);
      } catch (cause) {
        if (!controller.signal.aborted) fail(cause);
      } finally {
        if (pending === controller) pending = null;
      }
    };
    const visible = () => {
      if (document.hidden) return;
      pending?.abort();
      pending = null;
      suspend();
      void verify(true);
    };
    const timer = window.setInterval(() => void verify(), 15000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      pending?.abort();
    };
  }, [csrf, role?.id, role?.version, role?.available, enabled]);
}
