import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "./Auth";
import { apiRead } from "../api/client";
import type { Page } from "../api/contracts";
import { readRolePreference, saveRolePreference } from "./rolePreference";

export type WorkspaceRole = { id: string; name: string; state: string };
export type ConversationSelection = {
  conversation_id: string;
  actor_id: string;
  person_id: string | null;
  kind: string;
  label: string;
  revision: number;
};
type Workspace = {
  roles: WorkspaceRole[];
  actor: string;
  select: (id: string) => boolean;
  refresh: () => Promise<void>;
  error: string;
  conversation: ConversationSelection | null;
  selectConversation: (value: ConversationSelection | null) => void;
};
const Context = createContext<Workspace | null>(null);
export function RoleWorkspaceProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const key = `tianshu-workspace-role:${session?.username ?? ""}`;
  const [roles, setRoles] = useState<WorkspaceRole[]>([]);
  const [actor, setActor] = useState("");
  const [error, setError] = useState("");
  const [conversation, setConversation] =
    useState<ConversationSelection | null>(null);
  const active = useRef<AbortController | null>(null);
  const identity = useRef(session?.csrf);
  identity.current = session?.csrf;
  async function refresh() {
    if (!session?.authenticated) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const captured = session.csrf;
    try {
      const list: WorkspaceRole[] = [];
      let cursor: string | null = null;
      do {
        const page: Page<{ actor_id: string; name: string; enabled: boolean }> =
          await apiRead(
            "companion/roles/list",
            { cursor, limit: 100 },
            session.csrf,
            controller.signal,
          );
        if (controller.signal.aborted || identity.current !== captured) return;
        list.push(
          ...page.items.map((role) => ({
            id: role.actor_id,
            name: role.name,
            state: role.enabled ? "active" : "disabled",
          })),
        );
        cursor = page.next_cursor;
      } while (cursor);
      setRoles(list);
      setError("");
      setActor((current) =>
        list.some((r) => r.id === current)
          ? current
          : (list.find((r) => r.id === readRolePreference(key))?.id ??
            list[0]?.id ??
            ""),
      );
    } catch (cause) {
      if (controller.signal.aborted || identity.current !== captured) return;
      setError(cause instanceof Error ? cause.message : "角色列表读取失败");
      setRoles([]);
      setActor("");
    }
  }
  useEffect(() => {
    setRoles([]);
    setActor("");
    setError("");
    void refresh();
    return () => active.current?.abort();
  }, [session?.csrf]);
  useEffect(() => {
    setConversation(null);
  }, [actor, session?.csrf]);
  function select(id: string) {
    if (
      id !== actor &&
      !window.dispatchEvent(
        new CustomEvent("tianshu:before-role", {
          cancelable: true,
          detail: { from: actor, to: id },
        }),
      )
    )
      return false;
    setActor(id);
    saveRolePreference(key, id);
    return true;
  }
  return (
    <Context.Provider
      value={{
        roles,
        actor,
        select,
        refresh,
        error,
        conversation,
        selectConversation: (value) => {
          if (!value || value.actor_id === actor) setConversation(value);
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useRoleWorkspace() {
  const value = useContext(Context);
  if (!value) throw new Error("role_workspace_missing");
  return value;
}
export function RolePicker() {
  const { roles, actor, select, error } = useRoleWorkspace();
  return (
    <div className="role-picker">
      <label>
        <span>当前角色</span>
        <select
          aria-label="当前角色"
          value={actor}
          onChange={(e) => {
            if (!select(e.target.value)) e.currentTarget.value = actor;
          }}
          disabled={!roles.length}
        >
          {!roles.length && (
            <option value="">{error ? "角色暂不可读" : "还没有角色"}</option>
          )}
          {roles.map((role) => (
            <option key={role.id} value={role.id}>
              {role.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
