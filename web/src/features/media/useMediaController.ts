import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { MediaError, mediaCall } from "./api";
import type { MediaView } from "./types";

/** The page reads the media owner's facts. Browser state is only an editor or a snapshot. */
export function useMediaController() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const currentSession = useRef(csrf);
  currentSession.current = csrf;
  const [view, setView] = useState<MediaView | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [responseMs, setResponseMs] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const active = useRef(new Map<string, AbortController>());
  const alive = useRef(false);
  const generation = useRef(0);
  const failedCommands = useRef(
    new Map<string, { body: string; client: string }>(),
  );
  const commandInFlight = useRef(false);
  const errorSlot = useRef("");

  const cancel = useCallback((slot: string) => {
    active.current.get(slot)?.abort();
    active.current.delete(slot);
  }, []);
  const clearError = useCallback((slot?: string) => {
    if (slot && errorSlot.current !== slot) return;
    setError("");
    errorSlot.current = "";
  }, []);
  const clearFeedback = useCallback(() => {
    clearError();
    setNotice("");
  }, [clearError]);
  const request = useCallback(
    async <T>(
      slot: string,
      operation: string,
      body: object,
    ): Promise<T | undefined> => {
      if (!csrf || !alive.current) return;
      cancel(slot);
      clearError(slot);
      const controller = new AbortController();
      active.current.set(slot, controller);
      const mine = generation.current;
      try {
        const result = await mediaCall<T>(
          operation,
          body,
          csrf,
          controller.signal,
        );
        if (
          !controller.signal.aborted &&
          alive.current &&
          mine === generation.current &&
          currentSession.current === csrf &&
          active.current.get(slot) === controller
        ) {
          clearError(slot);
          return result;
        }
      } catch (cause) {
        if (
          !controller.signal.aborted &&
          alive.current &&
          mine === generation.current &&
          currentSession.current === csrf
        ) {
          if (
            cause instanceof MediaError &&
            ["unauthorized", "session_expired"].includes(cause.code)
          ) {
            generation.current += 1;
            active.current.forEach((item) => item.abort());
            active.current.clear();
            setView(null);
            setNotice("");
            failedCommands.current.clear();
          }
          errorSlot.current = slot;
          setNotice("");
          setError(
            cause instanceof MediaError
              ? cause.message
              : "连接中断，结果尚未确认。恢复后请先读取当前状态；重复同一操作会沿用原请求标识。",
          );
          if (slot === "view") setStale(true);
        }
      } finally {
        if (active.current.get(slot) === controller)
          active.current.delete(slot);
      }
      return undefined;
    },
    [csrf, cancel, clearError],
  );

  const refresh = useCallback(async () => {
    const startedAt = performance.now();
    const result = await request<MediaView>("view", "view", {});
    if (result) {
      if (
        !Array.isArray(result.accounts) ||
        !Array.isArray(result.targets) ||
        !Array.isArray(result.subscriptions) ||
        !Array.isArray(result.jobs) ||
        typeof result.configured !== "boolean"
      ) {
        setError("后台返回的媒体状态不完整，本次读取未采用。");
        setStale(true);
        setLoading(false);
        return false;
      }
      setView(result);
      setResponseMs(Math.round(performance.now() - startedAt));
      setStale(false);
      if (errorSlot.current === "view") {
        setError("");
        errorSlot.current = "";
      }
    }
    if (alive.current) setLoading(false);
    return !!result;
  }, [request]);

  const command = useCallback(
    async <T>(operation: string, body: object): Promise<T | undefined> => {
      if (commandInFlight.current) return;
      commandInFlight.current = true;
      setBusy(operation);
      setError("");
      setNotice("");
      // Keep the same command key after a transport failure; only the owner decides replay.
      const signature = JSON.stringify(body);
      const pending = failedCommands.current.get(operation);
      const client = pending?.body === signature ? pending.client : requestId();
      // This transient record is scoped to this mounted login and never enters storage.
      failedCommands.current.set(operation, { body: signature, client });
      try {
        const result = await request<T>("command", operation, {
          ...body,
          client_request_id: client,
        });
        if (result) {
          failedCommands.current.delete(operation);
          void refresh();
        }
        return result;
      } finally {
        commandInFlight.current = false;
        if (alive.current) setBusy("");
      }
    },
    [request, refresh],
  );

  useEffect(() => {
    alive.current = true;
    generation.current += 1;
    setView(null);
    setLoading(!!csrf);
    setError("");
    setNotice("");
    setStale(false);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const poll = async () => {
      if (!alive.current || document.hidden || !csrf) return;
      const success = await refresh();
      failures = success ? 0 : Math.min(failures + 1, 3);
      if (alive.current && !document.hidden)
        timer = setTimeout(poll, Math.min(15_000 * 2 ** failures, 120_000));
    };
    const visible = () => {
      clearTimeout(timer);
      cancel("view");
      if (!document.hidden) void poll();
    };
    if (csrf) void poll();
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive.current = false;
      generation.current += 1;
      clearTimeout(timer);
      active.current.forEach((item) => item.abort());
      active.current.clear();
      failedCommands.current.clear();
      document.removeEventListener("visibilitychange", visible);
    };
  }, [csrf, refresh, cancel]);

  return {
    view,
    loading,
    stale,
    responseMs,
    error,
    notice,
    busy,
    request,
    command,
    refresh,
    cancel,
    setNotice,
    clearError,
    clearFeedback,
  };
}
export type MediaController = ReturnType<typeof useMediaController>;
