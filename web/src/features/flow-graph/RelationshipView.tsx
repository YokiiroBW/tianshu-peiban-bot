import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useRoleWorkspace,
  type ConversationSelection,
} from "../../app/RoleWorkspace";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import type { IdentityDirectory } from "../settings/channelTypes";
import { GraphBoard, type GraphEdge } from "./GraphBoard";

type Conversation = ConversationSelection & {
  scope_id: string;
  adapter: string;
  target_ref: string;
};
export default function RelationshipView() {
  const workspace = useRoleWorkspace();
  const { session } = useAuth();
  const canReadDirectory =
    session?.principal?.permissions.some(
      (value) => value === "*" || value === "identity.manage",
    ) === true;
  const directory = useDomainResource<IdentityDirectory>(
    "identity/directory",
    GLOBAL_SCOPE,
    canReadDirectory,
  );
  const [cursor, setCursor] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const conversations = useDomainResource<Page<Conversation>>(
    "identity/conversations",
    { ...actorScope(workspace.actor), cursor, limit: 100 },
    !!workspace.actor,
  );
  const term = search.trim().toLocaleLowerCase();
  const matches = (...values: string[]) =>
    !term || values.some((value) => value.toLocaleLowerCase().includes(term));
  const connections = (directory.data?.connections ?? []).filter((row) =>
    matches(row.connection_id, row.self_external_id, row.namespace),
  );
  const roles = workspace.roles.filter((row) => matches(row.id, row.name));
  const rows = (conversations.data?.items ?? []).filter((row) =>
    matches(row.label, row.conversation_id, row.kind),
  );
  const edges: GraphEdge[] = [
    ...connections.map((row) => ({
      from: "connection:" + row.connection_id,
      to: "actor:" + row.actor_id,
      active:
        selected === "connection:" + row.connection_id ||
        selected === "actor:" + row.actor_id,
      dashed: !row.enabled,
    })),
    ...rows.map((row) => ({
      from: "actor:" + row.actor_id,
      to: "conversation:" + row.conversation_id,
      active:
        selected === "conversation:" + row.conversation_id ||
        selected === "actor:" + row.actor_id,
    })),
  ];
  return (
    <div className="flow-relationships">
      <div className="flow-toolbar">
        <label>
          查找实际配置
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="账号、角色或会话"
          />
        </label>
        <button
          className="button"
          onClick={() => {
            void directory.refresh();
            void conversations.refresh();
            void workspace.refresh();
          }}
        >
          读取配置关系
        </button>
      </div>
      {directory.error && <p role="alert">账号目录：{directory.error}</p>}
      {conversations.error && <p role="alert">{conversations.error}</p>}
      {!canReadDirectory && (
        <p className="muted">
          当前账号没有全局身份目录权限；仍可查看已授权角色与会话。
        </p>
      )}
      <GraphBoard edges={edges}>
        <section className="flow-column">
          <h3>
            平台账号 <small>{connections.length}</small>
          </h3>
          <div className="flow-column-body">
            {connections.map((row) => (
              <button
                key={row.connection_id}
                data-flow-node={"connection:" + row.connection_id}
                className="flow-node"
                aria-pressed={selected === "connection:" + row.connection_id}
                onClick={() => setSelected("connection:" + row.connection_id)}
              >
                <strong>{row.connection_id}</strong>
                <span>
                  {row.namespace} · {row.self_external_id}
                </span>
                <small>
                  {row.enabled ? "身份启用" : "身份停用"} · 新私聊{" "}
                  {row.default_mode}
                </small>
              </button>
            ))}
            {!connections.length && (
              <p className="flow-empty-column">当前未读取到账号登记。</p>
            )}
          </div>
        </section>
        <section className="flow-column">
          <h3>
            角色 <small>{roles.length}</small>
          </h3>
          <div className="flow-column-body">
            {roles.map((row) => (
              <button
                key={row.id}
                data-flow-node={"actor:" + row.id}
                className="flow-node flow-role"
                aria-pressed={workspace.actor === row.id}
                onClick={() => {
                  workspace.select(row.id);
                  setCursor(null);
                  setSelected("actor:" + row.id);
                }}
              >
                <strong>{row.name}</strong>
                <span>{row.id}</span>
                <small>{row.state === "active" ? "已启用" : "已停用"}</small>
              </button>
            ))}
          </div>
        </section>
        <section className="flow-column">
          <h3>
            所选角色的真实会话 <small>{rows.length}</small>
          </h3>
          <div className="flow-column-body">
            {rows.map((row) => {
              const link = directory.data?.conversation_links.find(
                (item) => item.conversation_id === row.conversation_id,
              );
              return (
                <button
                  key={row.conversation_id}
                  data-flow-node={"conversation:" + row.conversation_id}
                  className="flow-node"
                  aria-pressed={
                    selected === "conversation:" + row.conversation_id
                  }
                  onClick={() =>
                    setSelected("conversation:" + row.conversation_id)
                  }
                >
                  <strong>{row.label}</strong>
                  <span>
                    {row.kind} · {row.adapter}
                  </span>
                  <small>
                    {link
                      ? `${link.mode} · 回复责任 ${link.reply_owner}`
                      : row.adapter === "web"
                        ? "网页会话"
                        : "策略当前未读取"}
                  </small>
                  <code>{row.conversation_id}</code>
                </button>
              );
            })}
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
                回到第一页
              </button>
            )}
          </div>
        </section>
      </GraphBoard>
      {selected && (
        <section className="flow-details">
          <h3>实际关联</h3>
          {selected.startsWith("connection:") ? (
            <>
              <p>
                账号负责角色来自Identity登记，传输配置与对端验证在渠道页读取。
              </p>
              <a className="button" href="#/settings/channels">
                编辑身份、传输与群策略
              </a>
            </>
          ) : selected.startsWith("actor:") ? (
            <>
              <p>人格、模型绑定和逐角色能力分别由其唯一配置域管理。</p>
              <div className="domain-actions">
                <a className="button" href="#/roles">
                  角色档案
                </a>
                <a className="button" href="#/roles/settings">
                  模型与能力
                </a>
              </div>
            </>
          ) : (
            <>
              <p>
                会话绑定、关键词、回复责任和主动参与由渠道设置中的真实Identity/B策略保存。
              </p>
              <a className="button" href="#/settings/channels">
                管理会话绑定与处理方式
              </a>
            </>
          )}
        </section>
      )}
      <p className="flow-hint">
        连线表示已登记配置关联，不代表实际调用、对端在线或消息已经投递。当前会话列表按真实授权分页读取。
      </p>
    </div>
  );
}
