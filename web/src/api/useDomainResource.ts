import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRead, apiCommand, commandInput, awaitCommand } from "./client";
import { commandIntent, finishIntent } from "./intents";
import { useAuth } from "../app/Auth";
import { useRoleWorkspace } from "../app/RoleWorkspace";
import type { CommandResult } from "./contracts";

export function useDomainTarget() {
  const workspace = useRoleWorkspace();
  return { actor_id: workspace.actor || null,
    ...(workspace.conversation ? { conversation_id: workspace.conversation.conversation_id,
      ...(workspace.conversation.person_id ? { person_id: workspace.conversation.person_id } : {}) } : {}) };
}

function selectTarget(target: ReturnType<typeof useDomainTarget>, value: object) {
  const given = value as Record<string, unknown>;
  const changedActor = "actor_id" in given && given.actor_id !== target.actor_id;
  const changedPerson = "person_id" in given && given.person_id !== target.person_id;
  return { ...(changedActor || changedPerson ? { actor_id: target.actor_id } : target), ...value };
}

/** A request belongs to a session and explicit scope, never to a previous role. */
export function useDomainResource<T>(path: string, scope: object = {}, enabled = true, pollMs = 0, pollWhile?: (value: T) => boolean) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const target = useDomainTarget();
  const key = JSON.stringify(selectTarget(target, scope));
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
  const target = useDomainTarget();
  const targetKey = JSON.stringify(target);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<CommandResult<T> | null>(null);
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const identity = session?.csrf;
  useEffect(() => {
    mounted.current = true;
    setResult(null); setError(""); setBusy(false);
    return () => { mounted.current = false; active.current?.abort(); active.current = null; };
  }, [identity, scope, targetKey]);
  async function run(path: string, value: object, revision: number | null = null) {
    if (!session?.authenticated || active.current) return null;
    const submitted = selectTarget(target, value);
    const controller = new AbortController();
    active.current = controller;
    setBusy(true); setError(""); setResult(null);
    const intent = commandIntent(session.csrf, scope, path, submitted, revision);
    try {
      const initial = await apiCommand<T>(path, { ...submitted, ...commandInput(revision, intent.id) }, session.csrf, controller.signal);
      const answer = await awaitCommand(initial, submitted, session.csrf, controller.signal, (current) => {
        if (!controller.signal.aborted && mounted.current && active.current === controller) setResult(current);
        window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: current }));
      });
      if (answer.receipt?.execution !== "unknown" && answer.receipt?.effect !== "unknown" && answer.receipt?.delivery !== "unknown") finishIntent(intent.key);
      window.dispatchEvent(new CustomEvent("tianshu:command-result", { detail: answer }));
      return answer;
    } catch (cause) {
      const unknown = cause instanceof ApiError && ["unknown", "pending"].includes(cause.executionState ?? "");
      if (unknown) window.dispatchEvent(new CustomEvent("tianshu:command-unknown", { detail: { operation: path, client_request_id: intent.id, expected_revision: revision, scope, ...(cause instanceof ApiError ? cause.details : {}) } }));
      else finishIntent(intent.key);
      if (mounted.current && active.current === controller)
        setError(cause instanceof Error ? cause.message : "提交结果尚未确认，请查询原任务。");
      return null;
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted && mounted.current) setBusy(false);
    }
  }
  return { run, busy, error, result };
}
