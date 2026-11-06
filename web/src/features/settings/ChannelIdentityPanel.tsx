import { useState } from "react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import type {
  IdentityDirectory,
  RegisteredConnection,
  ConversationLink,
} from "./channelTypes";
import { splitLines } from "./channelTypes";
import { connectionName } from "../people/types";
import { humanizeCode } from "../../design/humanize";

export function ChannelIdentityPanel({ onChanged }: { onChanged: () => void }) {
  const directory = useDomainResource<IdentityDirectory>(
    "identity/directory",
    GLOBAL_SCOPE,
  );
  const [editing, setEditing] = useState<RegisteredConnection | "new" | null>(
    null,
  );
  const [link, setLink] = useState<ConversationLink | "new" | null>(null);
  const refresh = () => {
    void directory.refresh();
    onChanged();
  };
  return (
    <DetailCard title="机器人身份与会话范围">
      {directory.error && <p role="alert">{directory.error}</p>}
      <p>
        登记平台账号与负责的角色，再为群或私聊选择处理方式。私聊账号到人物的绑定在人物页管理。
      </p>
      <div className="domain-actions">
        <button className="button" onClick={() => setEditing("new")}>
          登记机器人账号
        </button>
        <button
          className="button"
          onClick={() => setLink("new")}
          disabled={!directory.data?.connections.length}
        >
          绑定群或私聊
        </button>
        <a className="button" href="#/people">
          人物与平台账号
        </a>
      </div>
      {directory.data?.connections.map((row) => (
        <article className="domain-record" key={row.connection_id}>
          <strong>{connectionName(row)}</strong>
          <p>
            {row.namespace} · {row.self_external_id} ·{" "}
            {row.enabled ? "已启用" : "已停用"} · 修订{row.revision}
          </p>
          <button className="text-link" onClick={() => setEditing(row)}>
            编辑身份与事件权限
          </button>
        </article>
      ))}
      {editing && (
        <ConnectionIdentity
          key={
            typeof editing === "string"
              ? editing
              : editing.connection_id + editing.revision
          }
          row={editing === "new" ? null : editing}
          done={() => {
            setEditing(null);
            refresh();
          }}
          cancel={() => setEditing(null)}
        />
      )}
      {directory.data?.conversation_links.map((row) => (
        <article
          className="domain-record"
          key={row.connection_id + ":" + row.external_id}
        >
          <strong>
            {row.label || "未命名会话"} · 账号 {row.external_id}
          </strong>
          <p>
            {{ reply: "允许回复", observe: "仅观察", ignore: "忽略" }[row.mode]}{" "}
            · {row.mention_required ? "需要被提及" : "不要求提及"} · 回复责任：
            {humanizeCode(row.reply_owner, "已登记回复服务")}
          </p>
          <p>
            {humanizeCode(row.kind, "聊天会话")}
          </p>
          {row.reply_keywords.length > 0 && (
            <p>关键词：{row.reply_keywords.join("、")}</p>
          )}
          {row.inherited_private_policy && (
            <p className="muted">
              当前继承连接的新私聊策略；保存此会话后成为明确覆盖。
            </p>
          )}
          <button className="text-link" onClick={() => setLink(row)}>
            编辑会话策略
          </button>
          {row.kind === "group" && <GroupParticipationControl link={row} />}
        </article>
      ))}
      {link && directory.data && (
        <ConversationPolicy
          key={
            typeof link === "string"
              ? link
              : link.connection_id + link.external_id + link.revision
          }
          row={link === "new" ? null : link}
          connections={directory.data.connections}
          done={() => {
            setLink(null);
            refresh();
          }}
          cancel={() => setLink(null)}
        />
      )}
    </DetailCard>
  );
}
function ConnectionIdentity({
  row,
  done,
  cancel,
}: {
  row: RegisteredConnection | null;
  done: () => void;
  cancel: () => void;
}) {
  const { roles } = useRoleWorkspace();
  const command = useDomainCommand(
    "channel-identity:" + (row?.connection_id ?? "new"),
    "global",
  );
  return (
    <form
      className="domain-form bot-wizard"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const actor_id = String(data.get("actor_id"));
        void command
          .run(
            "identity/connections.save",
            {
              ...actorScope(actor_id),
              connection_id: String(data.get("id")),
              namespace: String(data.get("namespace")),
              self_external_id: String(data.get("self")),
              enabled: data.get("enabled") === "on",
              default_mode: String(data.get("default_mode")),
              ...(row || String(data.get("permissions") ?? "").trim()
                ? {
                    event_permissions: splitLines(
                      String(data.get("permissions")),
                    ),
                  }
                : {}),
            },
            row?.revision ?? 0,
          )
          .then((answer) => {
            if (answer) done();
          });
      }}
    >
      <h4>{row ? "编辑机器人身份" : "登记机器人身份"}</h4>
      <div className="domain-grid">
        <label>
          连接标识
          <input
            name="id"
            defaultValue={row?.connection_id}
            readOnly={!!row}
            required
            maxLength={256}
          />
        </label>
        <label>
          负责角色
          <select
            name="actor_id"
            defaultValue={row?.actor_id ?? roles[0]?.id}
            required
          >
            {roles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          身份命名空间
          <input
            name="namespace"
            defaultValue={row?.namespace ?? "qq"}
            required
            placeholder="qq / telegram"
          />
        </label>
        <label>
          机器人平台账号ID
          <input name="self" defaultValue={row?.self_external_id} required />
        </label>
      </div>
      <label>
        <input
          name="enabled"
          type="checkbox"
          defaultChecked={row?.enabled ?? true}
        />
        启用身份
      </label>
      <label>
        新私聊处理方式
        <select name="default_mode" defaultValue={row?.default_mode ?? "reply"}>
          <option value="reply">正常回复并登记实际来访者</option>
          <option value="observe">仅观察</option>
          <option value="ignore">忽略</option>
        </select>
      </label>
      <label>
        事件可用权限（每行一项，首次留空采用服务器默认）
        <textarea
          name="permissions"
          defaultValue={row?.event_permissions.join("\n")}
        />
      </label>
      <p className="muted">
        平台事件只获得此连接明确允许的业务权限，不能使用管理员配置权限或通配符。
      </p>
      <div className="domain-actions">
        <button className="button primary" disabled={command.busy}>
          保存身份
        </button>
        <button type="button" className="button" onClick={cancel}>
          关闭
        </button>
      </div>
      <CommandFeedback {...command} />
    </form>
  );
}
function ConversationPolicy({
  row,
  connections,
  done,
  cancel,
}: {
  row: ConversationLink | null;
  connections: RegisteredConnection[];
  done: () => void;
  cancel: () => void;
}) {
  const [kind, setKind] = useState(row?.kind ?? "group");
  const command = useDomainCommand(
    "channel-policy:" + (row?.conversation_id ?? "new"),
    "global",
  );
  return (
    <form
      className="domain-form bot-wizard"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        const connection = connections.find(
          (x) => x.connection_id === data.get("connection"),
        );
        if (!connection) return;
        void command
          .run(
            "identity/conversations.link",
            {
              ...actorScope(connection.actor_id),
              connection_id: connection.connection_id,
              external_id: String(data.get("external")),
              kind,
              label: row?.label ?? String(data.get("label")),
              target_ref: row?.target_ref ?? String(data.get("target")),
              reply_keywords: splitLines(String(data.get("keywords") ?? "")),
              ...(data.get("sender")
                ? { sender_external_id: String(data.get("sender")) }
                : {}),
              mode: String(data.get("mode")),
              mention_required: data.get("mention") === "on",
              reply_owner: String(data.get("owner")),
            },
            row?.revision ?? 0,
          )
          .then((answer) => {
            if (answer) done();
          });
      }}
    >
      <h4>{row ? "编辑会话策略" : "绑定会话"}</h4>
      <div className="domain-grid">
        <label>
          登记连接
          <select
            name="connection"
            defaultValue={row?.connection_id}
            disabled={!!row}
            required
          >
            {connections.map((x) => (
              <option key={x.connection_id} value={x.connection_id}>
                {connectionName(x)}
              </option>
            ))}
          </select>
          {row && (
            <input type="hidden" name="connection" value={row.connection_id} />
          )}
        </label>
        <label>
          平台会话ID
          <input
            name="external"
            defaultValue={row?.external_id}
            readOnly={!!row}
            required
          />
        </label>
        {!row && (
          <>
            <label>
              会话类型
              <select
                name="kind"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
              >
                <option value="group">群</option>
                <option value="private">私聊</option>
              </select>
            </label>
            <label>
              会话名称
              <input name="label" required />
            </label>
            <label>
              明确投递地址
              <input
                name="target"
                placeholder="group:123 / private:123 / telegram:123"
                required
              />
            </label>
          </>
        )}
        {!row && kind === "private" && (
          <label>
            私聊发送者平台账号（可选）
            <input name="sender" placeholder="目标private:123对应发送者123" />
          </label>
        )}
        <label>
          处理方式
          <select name="mode" defaultValue={row?.mode ?? "observe"}>
            <option value="reply">允许回复</option>
            <option value="observe">仅观察</option>
            <option value="ignore">忽略</option>
          </select>
        </label>
        <label>
          回复责任
          <select name="owner" defaultValue={row?.reply_owner ?? "tianshu"}>
            <option value="tianshu">天枢</option>
            <option value="external">外部宿主</option>
          </select>
        </label>
      </div>
      <label>
        <input
          type="checkbox"
          name="mention"
          key={kind}
          defaultChecked={row?.mention_required ?? kind === "group"}
        />
        仅被提及或命中明确关键词时普通回复
      </label>
      <label>
        原文回复关键词（每行一个，不用正则）
        <textarea
          name="keywords"
          defaultValue={row?.reply_keywords.join("\n") ?? ""}
        />
      </label>
      <div className="domain-actions">
        <button className="button primary" disabled={command.busy}>
          保存会话策略
        </button>
        <button className="button" type="button" onClick={cancel}>
          关闭
        </button>
      </div>
      <CommandFeedback {...command} />
    </form>
  );
}

function GroupParticipationControl({ link }: { link: ConversationLink }) {
  const scope = {
    ...actorScope(link.actor_id),
    target_conversation_id: link.conversation_id,
  };
  const view = useDomainResource<{
    enabled: boolean;
    revision: number;
    can_manage: boolean;
  }>("conversation/participation", scope);
  const command = useDomainCommand(
    "group-participation:" + link.conversation_id,
    "actor",
  );
  return (
    <DetailCard title="主动加入群话题">
      {view.error && <p role="alert">{view.error}</p>}
      {view.data && (
        <form
          key={String(view.data.revision)}
          className="domain-form"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            void command
              .run(
                "conversation/participation.save",
                { ...scope, enabled: data.get("enabled") === "on" },
                view.data!.revision,
              )
              .then((answer) => {
                if (answer) void view.refresh();
              });
          }}
        >
          <label>
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={view.data.enabled}
              disabled={!view.data.can_manage}
            />
            允许角色在适合的实时群话题中主动发起参与
          </label>
          <p className="muted">
            复用生活页联系时段、冷却、日额度和真实未回复状态。模型决定是否适合加入；外部宿主负责或会话仅观察时不主动投递。
          </p>
          {link.mode === "reply" && !link.mention_required && (
            <p>
              当前每条消息都会进入普通回复；主动候选用于@与关键词门槛之外的实时观察消息。
            </p>
          )}
          <button
            className="button"
            disabled={command.busy || !view.data.can_manage}
          >
            保存参与策略
          </button>
          <a className="text-link" href="#/roles/life">
            查看联系节律
          </a>
          <CommandFeedback {...command} />
        </form>
      )}
    </DetailCard>
  );
}
