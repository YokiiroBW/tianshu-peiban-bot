import { useCallback, useEffect, useRef, useState } from "react";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { useAuth } from "../../app/Auth";

export function useInfrastructureQuery<T>(
  operation: string,
  body: object,
  enabled = true,
) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const bodyKey = JSON.stringify(body);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const active = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    if (!csrf || !enabled) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const current = ++sequence.current;
    try {
      const answer = await integrationPost<T>(
        `infrastructure/${operation}`,
        JSON.parse(bodyKey),
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted && current === sequence.current) {
        setData(answer);
        setError("");
        return answer;
      }
    } catch (cause) {
      if (!controller.signal.aborted && current === sequence.current)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted && current === sequence.current)
        setLoading(false);
    }
  }, [bodyKey, csrf, enabled, operation]);
  useEffect(() => {
    setData(null);
    setError("");
    setLoading(Boolean(csrf && enabled));
    void load();
    const visibleLoad = () => {
      if (!document.hidden) void load();
    };
    const timer = window.setInterval(visibleLoad, 3000);
    document.addEventListener("visibilitychange", visibleLoad);
    return () => {
      active.current?.abort();
      ++sequence.current;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibleLoad);
    };
  }, [csrf, enabled, load]);
  return { data, error, loading, reload: load };
}

export function useInfrastructureAction() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const active = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => () => active.current?.abort(), [csrf]);
  const send = async <T>(
    operation: string,
    body: object,
  ): Promise<T | null> => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setError("");
    setPending(true);
    try {
      const result = await integrationPost<T>(
        `infrastructure/${operation}`,
        body,
        csrf,
        controller.signal,
      );
      return controller.signal.aborted ? null : result;
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
      return null;
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  };
  return { send, pending, error };
}
