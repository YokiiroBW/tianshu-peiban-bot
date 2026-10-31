import { useState } from "react";
import {
  useDomainCommand,
  useDomainResource,
  actorScope,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { useIdentityDirectory } from "./api";
import type { Conversation, ConversationLink } from "./types";

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
            },
            link.revision,
          )
        )
          await refreshed();
      }}
    >
      <h4>{conversation.label}</h4>
      <p className="muted">
        {link.connection_id} · {link.external_id} · 当前修订 {link.revision}
      </p>
      <label>
        准入行为
        <select
          value={mode}
          onChange={(event) =>
            setMode(event.target.value as ConversationLink["mode"])
          }
        >
          <option value="ignore">忽略</option>
          <option value="observe">仅观察</option>
          <option value="reply">允许回复</option>
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={mention}
          onChange={(event) => setMention(event.target.checked)}
        />
        仅被提及时回复
      </label>
      <label>
        命令回复归属
        <select
          value={owner}
          onChange={(event) => setOwner(event.target.value)}
        >
          {[...new Set(["tianshu", "external", link.reply_owner])].map(
            (value) => (
              <option key={value}>{value}</option>
            ),
          )}
        </select>
      </label>
      <p className="muted">
        外部命令由登记宿主处理时，天枢只观察，避免双重回复。
      </p>
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
    { ...actorScope(actorId), person_id: personId ?? null, limit: 100, cursor },
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
      <h3>会话准入与回复</h3>
      <p className="muted">
        实际权限由登记渠道、账号和此会话策略决定，人物关系不授予访问或发送权限。
      </p>
      {!directory.manageable && (
        <p>当前身份可读人物资料；会话策略需要身份管理权限。</p>
      )}
      {(directory.error || conversations.error) && (
        <p role="alert">{directory.error || conversations.error}</p>
      )}
      {available.map((row) => {
        const link = directory.data?.conversation_links.find(
          (item) => item.conversation_id === row.conversation_id,
        );
        return link ? (
          <LinkEditor
            key={`${link.connection_id}:${link.external_id}:${link.revision}`}
            link={link}
            conversation={row}
            actorId={actorId}
            refreshed={directory.refresh}
          />
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
