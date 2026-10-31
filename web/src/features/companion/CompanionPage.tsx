import { useEffect, useState } from "react";
import {
  useRoleWorkspace,
  type ConversationSelection,
} from "../../app/RoleWorkspace";
import { useAuth } from "../../app/Auth";
import {
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Page } from "../../api/contracts";
import { DialoguePanel } from "./DialoguePanel";
import "./companion.css";

export default function CompanionPage() {
  const workspace = useRoleWorkspace();
  const { session } = useAuth();
  const [cursor, setCursor] = useState<string | null>(null);
  const conversations = useDomainResource<Page<ConversationSelection>>(
    "identity/conversations",
    { ...actorScope(workspace.actor), cursor, limit: 100 },
    !!workspace.actor,
  );
  const open = useDomainCommand<ConversationSelection>(
    workspace.actor,
    "actor",
  );
  useEffect(() => setCursor(null), [workspace.actor]);
  useEffect(() => {
    if (!workspace.conversation && conversations.data?.items.length)
      workspace.selectConversation(conversations.data.items[0]);
  }, [conversations.data, workspace.conversation]);
  return (
    <section
      className="chat-page chat-console glass scroll-page"
      aria-label="网页对话"
    >
      <div className="chat-toolbar">
        <div>
          <p className="eyebrow">角色 · 对话</p>
          <h2>
            {workspace.roles.find((role) => role.id === workspace.actor)
              ?.name ?? "选择一位角色"}
          </h2>
        </div>
        <button
          className="button"
          disabled={conversations.loading}
          onClick={() => void conversations.refresh()}
        >
          读取会话
        </button>
      </div>
      {!session?.authenticated ? (
        <p>请先登录。</p>
      ) : !workspace.actor ? (
        <p>先创建或选择一位角色。</p>
      ) : (
        <>
          <div className="chat-selectors">
            <label>
              已授权会话
              <select
                value={workspace.conversation?.conversation_id ?? ""}
                onChange={(event) =>
                  workspace.selectConversation(
                    conversations.data?.items.find(
                      (item) => item.conversation_id === event.target.value,
                    ) ?? null,
                  )
                }
              >
                <option value="">选择会话</option>
                {workspace.conversation &&
                  !conversations.data?.items.some(
                    (item) =>
                      item.conversation_id ===
                      workspace.conversation!.conversation_id,
                  ) && (
                    <option value={workspace.conversation.conversation_id}>
                      {workspace.conversation.label}
                    </option>
                  )}
                {conversations.data?.items.map((item) => (
                  <option
                    key={item.conversation_id}
                    value={item.conversation_id}
                  >
                    {item.label} ·{" "}
                    {item.kind === "private"
                      ? "私人"
                      : item.kind === "group"
                        ? "群聊"
                        : item.kind}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button"
              disabled={open.busy}
              onClick={() =>
                void open
                  .run("identity/conversations/open", {
                    actor_id: workspace.actor,
                  })
                  .then((result) => {
                    if (result?.data) {
                      workspace.selectConversation(result.data);
                      setCursor(null);
                      void conversations.refresh();
                    }
                  })
              }
            >
              开始私人对话
            </button>
          </div>
          <div className="domain-actions">
            {conversations.data?.next_cursor && (
              <button
                className="button"
                onClick={() => setCursor(conversations.data!.next_cursor)}
              >
                下一页会话
              </button>
            )}
            {cursor && (
              <button className="button" onClick={() => setCursor(null)}>
                会话第一页
              </button>
            )}
          </div>
          {conversations.error && (
            <p className="chat-error" role="alert">
              {conversations.error}
            </p>
          )}
          <CommandFeedback {...open} />
          {workspace.conversation ? (
            <DialoguePanel
              key={`${workspace.actor}:${workspace.conversation.conversation_id}`}
              actor={workspace.actor}
              conversation={workspace.conversation}
            />
          ) : (
            <p className="muted">选择一个已有会话，或开始与你的私人对话。</p>
          )}
        </>
      )}
    </section>
  );
}
