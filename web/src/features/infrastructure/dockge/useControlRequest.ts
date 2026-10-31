import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../app/Auth";
import { integrationPost, IntegrationError } from "../../../app/integrationApi";
import { requestId } from "../../../app/requestId";
import { useInfrastructureQuery, useInfrastructureAction } from "../api";
import { controlFailure } from "./presentation";
import type { Operation } from "./types";

type Receipt = { client_request_id: string };
function readReceipt(key: string): Receipt | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null");
    return typeof value?.client_request_id === "string"
      ? { client_request_id: value.client_request_id }
      : null;
  } catch {
    return null;
  }
}
function saveReceipt(key: string, value: Receipt | null) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
  } catch {
    /* The in-memory receipt still prevents repeat submission. */
  }
}

// Only a receipt key is retained, never configuration text or authoritative results.
// A missing response leaves the original key recoverable without submitting again.
export function useControlRequest(scope: string, durable = true) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const storageKey = `tianshu-infra-control:${session?.username ?? ""}:${scope}`;
  const [pending, setPending] = useState<Receipt | null>(() =>
    durable ? readReceipt(storageKey) : null,
  );
  const [submitted, setSubmitted] = useState<Operation | null>(null);
  const [completed, setCompleted] = useState<Operation | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const latch = useRef(false);
  const alive = useRef(true);
  const query = useInfrastructureQuery<{ operation: Operation }>(
    "control/operation",
    { client_request_id: pending?.client_request_id ?? "" },
    Boolean(pending) && !sending,
  );
  const recovery = useInfrastructureAction();
  const candidate = query.data?.operation ?? submitted;
  const operation =
    pending && candidate?.client_request_id === pending.client_request_id
      ? candidate
      : completed;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (
      !pending ||
      !operation ||
      operation.client_request_id !== pending.client_request_id ||
      !operation.settled ||
      operation.state === "unknown"
    )
      return;
    const executionActive =
      operation.result && "execution_active" in operation.result
        ? operation.result.execution_active
        : null;
    const commandState =
      operation.result && "command_state" in operation.result
        ? operation.result.command_state
        : "unknown";
    const rejectedBeforeSend =
      operation.state === "failed" &&
      operation.result === null &&
      ["not_sent", "rejected"].includes(operation.acceptance);
    if (
      ["action", "execute"].includes(operation.kind) &&
      !rejectedBeforeSend &&
      (executionActive !== false || commandState === "unknown")
    )
      return;
    if (durable) saveReceipt(storageKey, null);
    setError("");
    setCompleted(operation);
    setPending(null);
  }, [durable, operation, pending, storageKey]);

  const send = async (kind: "action" | "plan" | "execute", body: object) => {
    if (latch.current || pending || !csrf) return false;
    latch.current = true;
    let receipt: Receipt | null = null;
    try {
      receipt = { client_request_id: requestId() };
      if (durable) saveReceipt(storageKey, receipt);
      setPending(receipt);
      setSubmitted(null);
      setCompleted(null);
      setError("");
      setSending(true);
      const result = await integrationPost<{ operation: Operation }>(
        `infrastructure/control/${kind}`,
        { ...body, ...receipt },
        csrf,
        AbortSignal.timeout(15000),
      );
      if (alive.current) setSubmitted(result.operation);
    } catch (cause) {
      // A definite HTTP rejection occurred before durable acceptance. All lost
      // responses and server failures keep their key for read-only recovery.
      const rejected =
        !receipt ||
        (cause instanceof IntegrationError &&
          cause.status < 500 &&
          cause.executionState !== "unknown");
      if (rejected && durable) saveReceipt(storageKey, null);
      if (alive.current) {
        if (rejected) setPending(null);
        setError(
          receipt ? controlFailure(cause) : "操作请求尚未发出，请重试。",
        );
      }
    } finally {
      latch.current = false;
      if (alive.current) setSending(false);
    }
    return Boolean(receipt);
  };
  const recover = async () => {
    const id = pending?.client_request_id ?? operation?.client_request_id;
    if (!id) return;
    const result = await recovery.send<{ operation: Operation }>(
      "control/operation",
      { client_request_id: id, refresh: operation ? operation.can_refresh : true },
    );
    if (result) {
      if (pending) setSubmitted(result.operation);
      else setCompleted(result.operation);
      await query.reload();
    }
  };
  return {
    send,
    recover,
    operation,
    pending,
    busy: sending || Boolean(pending),
    recovering: recovery.pending,
    error: error || recovery.error || query.error,
  };
}
