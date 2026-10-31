import { useCallback, useEffect, useRef, useState } from "react";
import { apiRead, apiCommand, commandInput } from "./client";
import { useAuth } from "../app/Auth";
import type { CommandResult } from "./contracts";

/** A request belongs to a session and explicit scope, never to a previous role. */
export function useDomainResource<T>(path: string, scope: object = {}, enabled = true, pollMs = 0, pollWhile?: (value: T) => boolean) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const key = JSON.stringify(scope);
  const [data, setData] = useState<T | null>(null);
  const [dataKey, setDataKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const current = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const requestKey = `${csrf}:${path}:${key}`;
  const latest = useRef({ data, pollWhile });
  latest.current = { data, pollWhile };
  const refresh = useCallback(async () => {
    current.current?.abort();
    if (!csrf || !enabled) return;
    const controller = new AbortController();
    current.current = controller;
    const mark = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const answer = await apiRead<T>(path, JSON.parse(key), csrf, controller.signal);
      if (!controller.signal.aborted && mark === generation.current) { setData(answer); setDataKey(requestKey); }
    } catch (cause) {
      if (!controller.signal.aborted && mark === generation.current)
        setError(cause instanceof Error ? cause.message : "读取失败，请重试。");
    } finally {
      if (!controller.signal.aborted && mark === generation.current) setLoading(false);
    }
  }, [path, key, csrf, enabled, requestKey]);
  useEffect(() => {
    ++generation.current;
    setData(null);
    setError("");
    setLoading(false);
    void refresh();
    return () => { ++generation.current; current.current?.abort(); };
  }, [refresh]);
  useEffect(() => {
    if (!pollMs || !enabled || !csrf) return;
    const timer = window.setInterval(() => {
      const state = latest.current;
      if (!document.hidden && state.data && (!state.pollWhile || state.pollWhile(state.data))) void refresh();
    }, pollMs);
    return () => clearInterval(timer);
  }, [pollMs, enabled, csrf, refresh]);
  return { data: dataKey === requestKey ? data : null, error, loading, refresh };
}

export function useDomainCommand<T = unknown>(scope = "") {
  const { session } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CommandResult<T> | null>(null);
  const active = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const identity = session?.csrf;
  useEffect(() => {
    setResult(null); setError(""); setBusy(false); pending.current = null;
    return () => { active.current?.abort(); active.current = null; };
  }, [identity, scope]);
  async function run(path: string, value: object, revision: number | null = null) {
    if (!session?.authenticated || active.current) return null;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setError(""); setResult(null);
    const signature = JSON.stringify([path, value, revision]);
    if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    try {
      const answer = await apiCommand<T>(path, { ...value, ...commandInput(revision, pending.current.id) }, session.csrf, controller.signal);
      if (!controller.signal.aborted) {
        setResult(answer);
        if (answer.receipt?.execution !== "unknown" && answer.receipt?.effect !== "unknown") pending.current = null;
        window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: answer }));
      }
      return answer;
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof Error ? cause.message : "提交结果尚未确认，请查询原任务。");
      return null;
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return { run, busy, error, result };
}
