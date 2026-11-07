import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  actorScope,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useIdentityDirectory } from "./api";
import { humanizeCode } from "../../design/humanize";
import type { Conversation, ConversationLink } from "./types";
import { GroupParticipationControl } from "./GroupParticipationControl";
import { PrivateContactControl } from "./PrivateContactControl";

function LinkEditor({
  link,
  conversation,
  actorId,
  refreshed,
}: {
  link: ConversationLink;
  conversation: Conversation;
  actorId: string;
  refreshed: () => Promise<void>;
}) {
  const [mode, setMode] = useState(link.mode);
  const [mention, setMention] = useState(link.mention_required);
  const [owner, setOwner] = useState(link.reply_owner);
  const [keywords, setKeywords] = useState(
    (link.reply_keywords ?? []).join("\n"),
  );
  const command = useDomainCommand<ConversationLink>(
    `channel-policy:${link.connection_id}:${link.external_id}`,
    "global",
  );
  return (
    <form
      className="people-config-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (
          await command.run(
            "identity/conversations.link",
            {
              actor_id: actorId,
              person_id: null,
              conversation_id: null,
              connection_id: link.connection_id,
              external_id: link.external_id,
              kind: conversation.kind,
              mode,
              mention_required: mention,
              reply_owner: owner,
              reply_keywords: keywords
                .split("\n")
                .map((word) => word.trim())
                .filter(Boolean),
            },
            link.revision,
          )
        )
          await refreshed();
      }}
    >
      <h4>{conversation.label}</h4>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={mode !== "ignore"}
          onChange={(event) =>
            setMode(event.target.checked ? "observe" : "ignore")
          }
        />
        接收此会话
      </label>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={mode === "reply"}
          onChange={(event) =>
            setMode(event.target.checked ? "reply" : "observe")
          }
        />
        允许普通回复
      </label>
      <label>
        <input
          type="checkbox"
          checked={mention}
          onChange={(event) => setMention(event.target.checked)}
        />
        仅被提及或命中关键词时回复
      </label>
      <label>
        回复关键词（每行一个）
        <textarea
          value={keywords}
          onChange={(event) => setKeywords(event.target.value)}
          rows={3}
        />
      </label>
      <label>
        命令回复归属
        <select
          value={owner}
          onChange={(event) => setOwner(event.target.value)}
        >
          {[...new Set(["tianshu", "external", link.reply_owner])].map(
            (value) => (
              <option key={value} value={value}>
                {humanizeCode(value, "已登记的回复服务")}
              </option>
            ),
          )}
        </select>
      </label>
      <button className="button" disabled={command.busy}>
        保存此会话策略
      </button>
      <CommandFeedback {...command} />
    </form>
  );
}
export function ReplyPermissions({
  actorId,
  personId,
  conversationId,
}: {
  actorId: string;
  personId?: string;
  conversationId?: string;
}) {
  const directory = useIdentityDirectory();
  const [cursor, setCursor] = useState<string | null>(null);
  const conversations = useDomainResource<Page<Conversation>>(
    "identity/conversations",
    {
      ...actorScope(actorId),
      person_id: personId ?? null,
      conversation_id: conversationId ?? null,
      limit: 100,
      cursor,
    },
    directory.manageable,
  );
  const available =
    conversations.data?.items.filter(
      (row) =>
        (!personId || row.person_id === personId) &&
        (!conversationId || row.conversation_id === conversationId),
    ) ?? [];
  return (
    <section>
      <h3>会话权限</h3>
      <p className="muted">
        决定当前角色是否接收和回复此会话。关闭接收后不再处理新消息。
      </p>
      {personId && (
        <PrivateContactControl
          scope={{
            actor_id: actorId,
            person_id: personId,
            conversation_id: null,
          }}
        />
      )}
      {!directory.manageable && (
        <p>当前身份可读人物资料；会话策略需要身份管理权限。</p>
      )}
      {(directory.error || conversations.error) && (
        <p role="alert">{directory.error || conversations.error}</p>
      )}
      {conversations.loading && <p role="status">正在读取会话权限…</p>}
      {directory.manageable && conversations.data && !available.length && (
        <p>尚无与此人物关联的私聊。群内接收与回复可在“群画像”的权限页调整。</p>
      )}
      {available.map((row) => {
        if (row.kind === "private" && personId) return null;
        const links = directory.data?.conversation_links.filter(
          (item) => item.conversation_id === row.conversation_id,
        );
        return links?.length ? (
          links.map((link) => (
            <div
              key={`${link.connection_id}:${link.external_id}:${link.revision}`}
            >
              <LinkEditor
                key={`${link.connection_id}:${link.external_id}:${link.revision}`}
                link={link}
                conversation={row}
                actorId={actorId}
                refreshed={directory.refresh}
              />
              {row.kind === "group" && (
                <GroupParticipationControl
                  inline
                  link={{ ...link, actor_id: actorId }}
                />
              )}
            </div>
          ))
        ) : (
          <p key={row.conversation_id}>
            {row.label} ·{" "}
            {row.adapter === "web"
              ? "网页会话由当前身份授权，未连接外部渠道。"
              : "没有可读取的渠道绑定。"}
          </p>
        );
      })}
      <div className="models-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批会话
          </button>
        )}
        {conversations.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(conversations.data!.next_cursor!)}
          >
            下一批会话
          </button>
        )}
      </div>
    </section>
  );
}
