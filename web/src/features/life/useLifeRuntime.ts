import { requestId } from "../../app/requestId";
import { useEffect, useRef, useState } from "react";
import {
  IntegrationError,
  readFailure,
  writeFailure,
} from "../../app/integrationApi";
import { manageLife, readLife, type LifeAccess } from "./runtimeApi";
import type { manage_response, Records, Resource } from "./runtimeTypes";

export function useLifeResource<K extends keyof Records & Resource>(
  access: LifeAccess,
  resource: K,
  options: {
    objectId?: string | null;
    chapterView?: "current" | "published";
    refreshMs?: number;
  } = {},
) {
  const { objectId = null, chapterView, refreshMs } = options;
  const [items, setItems] = useState<Records[K][]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [epoch, setEpoch] = useState(0);
  const current = useRef<AbortController | null>(null);
  const identity = useRef("");
  const pendingRead = useRef(false);
  const [visiblePage, setVisiblePage] = useState(!document.hidden);
  useEffect(() => {
    function visibility() {
      setVisiblePage(!document.hidden);
      if (document.hidden) current.current?.abort();
    }
    document.addEventListener("visibilitychange", visibility);
    return () => document.removeEventListener("visibilitychange", visibility);
  }, []);
  function failed(cause: unknown) {
    if (
      cause instanceof IntegrationError &&
      [
        "forbidden",
        "unauthorized",
        "session_expired",
        "scope_changed",
        "stale_evidence",
        "source_changed",
        "source_withdrawn",
        "not_found",
        "upstream_forbidden",
        "operator_not_authorized",
        "life_read_required",
      ].includes(cause.code)
    ) {
      setItems([]);
      setCursor(null);
    }
    setError(readFailure(cause));
  }
  useEffect(() => {
    const control = new AbortController();
    current.current?.abort();
    current.current = control;
    const key = JSON.stringify([
      access.actor,
      access.csrf,
      resource,
      objectId,
      chapterView,
    ]);
    if (identity.current !== key) {
      setItems([]);
      setCursor(null);
      identity.current = key;
    }
    setError("");
    if (!access.actor || !access.csrf || !visiblePage) {
      setLoading(false);
      return;
    }
    setLoading(true);
    pendingRead.current = true;
    void readLife(access, resource, control.signal, objectId, null, chapterView)
      .then((result) => {
        if (!control.signal.aborted) {
          setItems(result.items);
          setCursor(result.next_cursor);
        }
      })
      .catch((cause) => {
        if (!control.signal.aborted) {
          failed(cause);
        }
      })
      .finally(() => {
        if (current.current === control) {
          pendingRead.current = false;
          if (!control.signal.aborted) setLoading(false);
        }
      });
    return () => control.abort();
  }, [
    access.actor,
    access.csrf,
    resource,
    objectId,
    chapterView,
    epoch,
    visiblePage,
  ]);
  useEffect(() => {
    if (!refreshMs || !access.actor || !access.csrf || !visiblePage) return;
    const timer = window.setInterval(() => {
      if (!pendingRead.current && !document.hidden) setEpoch((old) => old + 1);
    }, refreshMs);
    return () => window.clearInterval(timer);
  }, [
    access.actor,
    access.csrf,
    resource,
    objectId,
    chapterView,
    refreshMs,
    visiblePage,
  ]);
  async function more() {
    if (!cursor || loading) return;
    const control = current.current ?? new AbortController();
    setLoading(true);
    pendingRead.current = true;
    setError("");
    try {
      const answer = await readLife(
        access,
        resource,
        control.signal,
        objectId,
        cursor,
        chapterView,
      );
      if (!control.signal.aborted) {
        setItems((old) => [...old, ...answer.items]);
        setCursor(answer.next_cursor);
      }
    } catch (cause) {
      if (!control.signal.aborted) failed(cause);
    } finally {
      if (current.current === control) {
        pendingRead.current = false;
        if (!control.signal.aborted) setLoading(false);
      }
    }
  }
  const visible =
    identity.current ===
    JSON.stringify([
      access.actor,
      access.csrf,
      resource,
      objectId,
      chapterView,
    ]);
  return {
    items: visible ? items : [],
    cursor,
    loading,
    error,
    refresh: () => setEpoch((old) => old + 1),
    more,
  };
}

export function useLifeAction(access: LifeAccess, updated: () => void) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [receipt, setReceipt] = useState<manage_response | null>(null);
  const active = useRef<AbortController | null>(null);
  const pending = useRef<{ semantic: string; id: string } | null>(null);
  useEffect(() => {
    active.current?.abort();
    pending.current = null;
    setReceipt(null);
    setError("");
    setBusy(false);
    return () => active.current?.abort();
  }, [access.actor, access.csrf]);
  async function run(
    operation: string,
    value: object,
    version: number | null = null,
  ) {
    if (active.current && !active.current.signal.aborted) return null;
    const semantic = JSON.stringify([operation, value, version]);
    const id =
      pending.current?.semantic === semantic ? pending.current.id : requestId();
    pending.current = { semantic, id };
    const control = new AbortController();
    active.current = control;
    setBusy(true);
    setError("");
    setReceipt(null);
    try {
      const answer = await manageLife(
        access,
        operation,
        value,
        version,
        id,
        control.signal,
      );
      if (control.signal.aborted) return null;
      if (answer.result.state !== "unknown") pending.current = null;
      setReceipt(answer);
      updated();
      return answer;
    } catch (cause) {
      if (!control.signal.aborted) setError(writeFailure(cause));
      return null;
    } finally {
      if (active.current === control) active.current = null;
      if (!control.signal.aborted) setBusy(false);
    }
  }
  return { busy, error, receipt, run };
}

// Keep object identity through an uncertain response; only a confirmed save starts a new draft.
export function useDraftId(prefix: string) {
  const id = useRef(`${prefix}:${requestId()}`);
  return {
    id: id.current,
    reset: () => {
      id.current = `${prefix}:${requestId()}`;
    },
  };
}
