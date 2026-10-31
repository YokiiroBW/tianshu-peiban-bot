import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import type { Conversation } from "./types";

export function useGraphRead<T>(
  operation: string,
  body: object,
  enabled = true,
) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const bodyKey = JSON.stringify(body);
  const active = useRef<AbortController | null>(null);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(enabled);
  const reload = useCallback(async () => {
    if (!csrf || !enabled) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError("");
    try {
      const result = await integrationPost<T>(
        `flow-graph/${operation}`,
        JSON.parse(bodyKey),
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setData(result);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setData(null);
        setError(readFailure(cause));
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [csrf, operation, bodyKey, enabled]);
  useEffect(() => {
    setData(null);
    setError("");
    setLoading(enabled);
    void reload();
    return () => active.current?.abort();
  }, [reload, enabled]);
  return { data, error, loading, reload };
}

type GroupRule = { mode: string; actor_id: string | null; keywords: string[] };
type ObservationRow = {
  id: string;
  revision: number;
  group_policy: {
    version: number;
    default: GroupRule;
    groups: Record<string, GroupRule>;
  };
  private_policy: object;
};

/** Change the existing policy owner; never save browser graph state as authority. */
export async function saveGroupActor(
  conversation: Conversation,
  actorId: string | null,
  csrf: string,
  signal: AbortSignal,
) {
  if (!conversation.edit) throw new Error("此会话请在机器人接入中修改绑定。");
  const current = await integrationPost<{ connections: ObservationRow[] }>(
    "bot-observation/view",
    {},
    csrf,
    signal,
  );
  const row = current.connections.find(
    (item) => item.id === conversation.edit!.connection_id,
  );
  if (!row || row.revision !== conversation.edit.revision)
    throw new Error("映射已在其他页面更改，请刷新链路图后再保存。");
  const groupId = conversation.conversation_id.replace(/^group:/, "");
  const policy = row.group_policy;
  const rule = policy.groups[groupId] ?? policy.default;
  return integrationPost<{ connection: { state: string; pending: boolean } }>(
    "bot-observation/policy",
    {
      id: row.id,
      expected_revision: row.revision,
      group_policy: {
        ...policy,
        groups: { ...policy.groups, [groupId]: { ...rule, actor_id: actorId } },
      },
      private_policy: row.private_policy,
      client_request_id: requestId(),
    },
    csrf,
    signal,
  );
}
