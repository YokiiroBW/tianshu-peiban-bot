import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { useDomainResource, actorScope } from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
export type PublicationSelection = {
  reader_ids: string[];
  conversation_ids: string[];
};
export function PublicationAudience({
  value,
  onChange,
}: {
  value: PublicationSelection;
  onChange: (selection: PublicationSelection) => void;
}) {
  const { actor } = useRoleWorkspace();
  const [cursor, setCursor] = useState<string | null>(null);
  const [conversationCursor, setConversationCursor] = useState<string | null>(
    null,
  );
  const accounts = useDomainResource<
    Page<{ principal_id: string; display_name: string }>
  >("identity/publication-recipients", {
    ...actorScope(actor),
    cursor,
    limit: 100,
  });
  const conversations = useDomainResource<
    Page<{ conversation_id: string; label: string; kind: string }>
  >("identity/conversations", {
    ...actorScope(actor),
    cursor: conversationCursor,
    limit: 100,
  });
  function toggle(
    key: keyof PublicationSelection,
    id: string,
    checked: boolean,
  ) {
    onChange({
      ...value,
      [key]: checked
        ? [...new Set([...value[key], id])]
        : value[key].filter((item) => item !== id),
    });
  }
  return (
    <fieldset className="domain-form">
      <legend>发布给谁</legend>
      <p className="muted">
        留空只给作者。选择接收者后，他们只读到明确发布的这一版内容。
      </p>
      {accounts.error && <p role="alert">{accounts.error}</p>}
      {accounts.data?.items.map((item) => (
        <label className="inline-check" key={item.principal_id}>
          <input
            type="checkbox"
            checked={value.reader_ids.includes(item.principal_id)}
            onChange={(event) =>
              toggle("reader_ids", item.principal_id, event.target.checked)
            }
          />
          {item.display_name}
        </label>
      ))}
      <div className="domain-actions">
        {cursor && (
          <button
            type="button"
            className="text-link"
            onClick={() => setCursor(null)}
          >
            回到首批账号
          </button>
        )}
        {accounts.data?.next_cursor && (
          <button
            type="button"
            className="text-link"
            onClick={() => setCursor(accounts.data!.next_cursor)}
          >
            更多账号
          </button>
        )}
      </div>
      {conversations.error && <p role="alert">{conversations.error}</p>}
      {conversations.data?.items.map((item) => (
        <label className="inline-check" key={item.conversation_id}>
          <input
            type="checkbox"
            checked={value.conversation_ids.includes(item.conversation_id)}
            onChange={(event) =>
              toggle(
                "conversation_ids",
                item.conversation_id,
                event.target.checked,
              )
            }
          />
          {item.label} · {item.kind === "private" ? "私聊" : "会话"}
        </label>
      ))}
      <div className="domain-actions">
        {conversationCursor && (
          <button
            type="button"
            className="text-link"
            onClick={() => setConversationCursor(null)}
          >
            回到首批会话
          </button>
        )}
        {conversations.data?.next_cursor && (
          <button
            type="button"
            className="text-link"
            onClick={() =>
              setConversationCursor(conversations.data!.next_cursor)
            }
          >
            更多会话
          </button>
        )}
      </div>
      {value.reader_ids.length + value.conversation_ids.length > 0 && (
        <p>
          已选择 {value.reader_ids.length} 个账号、
          {value.conversation_ids.length} 个会话。
          <button
            type="button"
            className="text-link"
            onClick={() => onChange({ reader_ids: [], conversation_ids: [] })}
          >
            清空选择
          </button>
        </p>
      )}
    </fieldset>
  );
}
