import { readableRecord } from "../../design/readableRecord";
import { useState } from "react";
import { useAuth } from "../../app/Auth";
import {
  useRoleWorkspace,
  type ConversationSelection,
} from "../../app/RoleWorkspace";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainCommand,
  useDomainResource,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { CommandFeedback } from "../../components/CommandFeedback";
import { DetailCard } from "../../components/DetailCard";
import { taskHref } from "../../api/commandRecovery";
import { ChannelIdentityPanel } from "./ChannelIdentityPanel";
import { connectionName } from "../people/types";
import { humanizeCode } from "../../design/humanize";
import {
  adapterLabels,
  splitLines,
  type ChannelConnection,
  type ChannelView,
  type ChannelMatrix,
  type IdentityDirectory,
  type RegisteredConnection,
} from "./channelTypes";
import "./bots.css";

export function BotAdapterPanel() {
  const { session } = useAuth();
  const permissions = session?.principal?.permissions ?? [];
  const canIdentity =
    permissions.includes("*") || permissions.includes("identity.manage");
  const view = useDomainResource<ChannelView>(
    "channels/view",
    GLOBAL_SCOPE,
    true,
    15000,
  );
  const directory = useDomainResource<IdentityDirectory>(
    "identity/directory",
    GLOBAL_SCOPE,
    canIdentity,
  );
  const [selected, setSelected] = useState<string | null>(null);
  const refresh = () => {
    void view.refresh();
    if (canIdentity) void directory.refresh();
  };
  return (
    <div className="bot-adapter-page scroll-page">
      <header className="section-heading">
        <div>
          <h2>机器人接入</h2>
          <p>原生 OneBot、Telegram 与外部宿主共用一个公开表达和投递回执。</p>
        </div>
        <button className="button" onClick={refresh}>
          读取当前状态
        </button>
      </header>
      {view.error && <p role="alert">{view.error}</p>}
      <section className="panel">
        <h3>传输进程</h3>
        <p>
          {view.data?.owner
            ? `${view.data.owner.active ? "连接管理租约有效" : "连接管理租约已失效"} · 到期 ${new Date(view.data.owner.expires_at).toLocaleString("zh-CN")}`
            : "当前没有连接管理记录"}
        </p>
        <p className="muted">
          连接配置与对端账号验证分别记录。外部宿主未提供可核验状态时保持未知。
        </p>
      </section>
      {canIdentity && <ChannelIdentityPanel onChanged={refresh} />}
      <section className="panel">
        <div className="section-heading">
          <h3>传输连接</h3>
          <button
            className="button"
            onClick={() => setSelected("new")}
            disabled={!directory.data?.connections.length}
          >
            配置已登记账号
          </button>
        </div>
        {view.data?.connections.map((connection) => (
          <ChannelCard
            key={connection.id + ":" + connection.revision}
            connection={connection}
            refresh={refresh}
            edit={() => setSelected(connection.id)}
          />
        ))}
        {view.data && !view.data.connections.length && (
          <p className="muted">
            还没有传输配置。先登记机器人账号，再配置连接。
          </p>
        )}
        {selected && (
          <ChannelEditor
            key={
              selected +
              ":" +
              (view.data?.connections.find((x) => x.id === selected)
                ?.revision ?? 0)
            }
            row={view.data?.connections.find((x) => x.id === selected) ?? null}
            registrations={directory.data?.connections ?? []}
            close={() => setSelected(null)}
            done={() => {
              setSelected(null);
              refresh();
            }}
          />
        )}
      </section>
      {view.data && (
        <>
          <NativeApi connections={view.data.connections} />
          <DetailCard title="标准协议目录">
            <Matrix matrix={view.data.matrix} />
          </DetailCard>
          <DetailCard title="真实观察与原投递">
            <div className="domain-grid">
              {view.data.deliveries.map((delivery) => (
                <a
                  key={delivery.expression_id}
                  className="domain-record"
                  href={taskHref({
                    action_id: delivery.action_id,
                    client_request_id: "",
                    target: {
                      actor_id: null,
                      person_id: null,
                      conversation_id: delivery.conversation_id,
                    },
                  })}
                >
                  查看本次投递记录
                </a>
              ))}
            </div>
            {view.data.observations.map((observation, index) => (
              <article className="domain-record" key={index}>
                <strong>
                  {humanizeCode(observation.kind, "连接观察")} · {humanizeCode(observation.name, "服务状态记录")}
                </strong>
                <time>
                  {new Date(observation.observed_at).toLocaleString("zh-CN")}
                </time>
                <details>
                  <summary>实际观察</summary>
                  <pre>{readableRecord(observation.body)}</pre>
                </details>
              </article>
            ))}
          </DetailCard>
        </>
      )}
    </div>
  );
}
function ChannelCard({
  connection,
  refresh,
  edit,
}: {
  connection: ChannelConnection;
  refresh: () => void;
  edit: () => void;
}) {
  const probe = useDomainCommand("channel-probe:" + connection.id, "global");
  return (
    <article className="bot-wizard">
      <div className="section-heading">
        <div>
          <h3>{connectionName(connection)}</h3>
          <p>
            {adapterLabels[connection.adapter] ?? connection.adapter} ·{" "}
            {connection.transport} · {connection.provider}
          </p>
        </div>
        <span className="tasks-chip">
          {connection.enabled ? "已启用" : "已停用"} ·{" "}
          {connection.configured ? "已配置凭据" : "未配置凭据"}
        </span>
      </div>
      <p>
        {connection.self_external_id} · {humanizeCode(connection.state, "状态尚未确认")}
        {connection.reason ? ` · ${humanizeCode(connection.reason, "连接状态有待核对")}` : ""} · 修订
        {connection.revision}
      </p>
      <div className="domain-actions">
        <button className="button" onClick={edit}>
          编辑传输设置
        </button>
        <button
          className="button"
          disabled={probe.busy}
          onClick={() =>
            void probe
              .run(
                "channels/connections/probe",
                {
                  ...actorScope(connection.actor_id),
                  connection_id: connection.id,
                },
                connection.revision,
              )
              .then(() => refresh())
          }
        >
          实际探测账号与协议
        </button>
      </div>
      <CommandFeedback {...probe} />
      {connection.transport === "reverse_http" && (
        <p>
          事件入口：
          <code>{`/channels/onebot/v11/${encodeURIComponent(connection.id)}/events`}</code>
        </p>
      )}
      {connection.transport === "reverse_ws" && (
        <p>
          反向WS入口：
          <code>{`/channels/onebot/v11/${encodeURIComponent(connection.id)}/ws`}</code>
          （连接方使用同一Bearer令牌和真实Self-ID）
        </p>
      )}
      {connection.adapter === "telegram" && (
        <p>
          Webhook入口：
          <code>{`/channels/telegram/${encodeURIComponent(connection.id)}/webhook`}</code>
        </p>
      )}
      {["nonebot", "astrbot"].includes(connection.adapter) && (
        <p>
          在宿主安装对应天枢SDK桥，配置同一连接标识、宿主实例与令牌。外部规则链的实际回复责任以观察回执为准。
        </p>
      )}
      <details>
        <summary>此连接的真实支持矩阵</summary>
        <Matrix matrix={connection.matrix} />
      </details>
    </article>
  );
}
function Matrix({ matrix }: { matrix: ChannelMatrix }) {
  return (
    <div className="channel-matrix">
      <p>
        {matrix.standard}
        {matrix.spec_url && (
          <>
            {" "}
            ·{" "}
            <a href={matrix.spec_url} target="_blank" rel="noreferrer">
              协议来源
            </a>
          </>
        )}
      </p>
      <p className="muted">
        “已实现”表示本地代码能力；对端未声明支持或没有该修订的真实成功回执时，分别显示未知与未验证。
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>API / 消息段</th>
              <th>本地实现</th>
              <th>对端支持</th>
              <th>实际验证</th>
              <th>参数 / 方向</th>
            </tr>
          </thead>
          <tbody>
            {[...matrix.apis, ...(matrix.segments ?? [])].map((item, index) => (
              <tr key={index}>
                <td>
                  {item.name ?? item.type}
                  {item.variant ? ` / ${item.variant}` : ""}
                </td>
                <td>{item.implemented ? "已实现" : "未实现"}</td>
                <td>
                  {item.provider_supported === true
                    ? "已声明支持"
                    : item.provider_supported === false
                      ? "不支持"
                      : "未知"}
                </td>
                <td>
                  {item.verified_at
                    ? new Date(item.verified_at).toLocaleString("zh-CN")
                    : "未验证"}
                </td>
                <td>
                  {item.required?.join("、") ??
                    `${item.receive ? "接收 " : ""}${item.send ? "发送" : ""}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
const transports: Record<string, string[]> = {
  onebot: ["http", "reverse_http", "ws", "reverse_ws"],
  telegram: ["telegram_webhook", "telegram_poll"],
  nonebot: ["bridge"],
  astrbot: ["bridge"],
};
function ChannelEditor({
  row,
  registrations,
  done,
  close,
}: {
  row: ChannelConnection | null;
  registrations: RegisteredConnection[];
  done: () => void;
  close: () => void;
}) {
  const [adapter, setAdapter] = useState(row?.adapter ?? "onebot");
  const [transport, setTransport] = useState(row?.transport ?? "reverse_ws");
  const [error, setError] = useState("");
  const command = useDomainCommand(
    "channel-config:" + (row?.id ?? "new"),
    "global",
  );
  return (
    <form
      className="domain-form bot-wizard"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        const form = event.currentTarget;
        const data = new FormData(form);
        const registered = row
          ? { connection_id: row.id, actor_id: row.actor_id }
          : registrations.find(
              (x) => x.connection_id === data.get("connection"),
            );
        if (!registered) {
          setError("当前登记身份不可读，请刷新身份列表。");
          return;
        }
        let advanced: Record<string, unknown>;
        try {
          advanced = JSON.parse(String(data.get("advanced")));
          if (
            !advanced ||
            Array.isArray(advanced) ||
            typeof advanced !== "object"
          )
            throw new Error();
        } catch {
          setError("高级协议声明必须是JSON对象。");
          return;
        }
        const token = String(data.get("token") ?? "");
        void command
          .run(
            "channels/connections/save",
            {
              ...advanced,
              ...actorScope(registered.actor_id),
              id: registered.connection_id,
              adapter,
              transport,
              provider: String(data.get("provider")),
              endpoint: String(data.get("endpoint")) || null,
              api_endpoint: String(data.get("api_endpoint")) || null,
              enabled: data.get("enabled") === "on",
              allow_at_all: data.get("at_all") === "on",
              extension_apis: splitLines(String(data.get("extensions") ?? "")),
              allowed_media_hosts: splitLines(String(data.get("hosts") ?? "")),
              ...(token ? { token } : {}),
            },
            row?.revision ?? 0,
          )
          .then((answer) => {
            if (answer) {
              form.reset();
              done();
            }
          });
      }}
    >
      <h3>{row ? "编辑连接" : "配置传输"}</h3>
      <div className="domain-grid">
        <label>
          已登记账号
          <select
            name="connection"
            defaultValue={row?.id}
            disabled={!!row}
            required
          >
            {row &&
              !registrations.some(
                (value) => value.connection_id === row.id,
              ) && (
                <option value={row.id}>
                  {connectionName(row)}
                </option>
              )}
            {registrations.map((x) => (
              <option key={x.connection_id} value={x.connection_id}>
                {connectionName(x)}
              </option>
            ))}
          </select>
          {row && <input type="hidden" name="connection" value={row.id} />}
        </label>
        <label>
          适配器
          <select
            value={adapter}
            onChange={(event) => {
              const value = event.target.value;
              setAdapter(value);
              setTransport(transports[value][0]);
            }}
          >
            {Object.entries(adapterLabels).map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label>
          传输
          <select
            value={transport}
            onChange={(event) => setTransport(event.target.value)}
          >
            {transports[adapter].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          对端类型
          <select name="provider" defaultValue={row?.provider ?? "generic"}>
            {[
              "generic",
              "napcat",
              "snowluma",
              "telegram",
              "nonebot",
              "astrbot",
            ].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          服务地址
          <input
            name="endpoint"
            defaultValue={row?.endpoint}
            placeholder="https://… 或 wss://…"
          />
        </label>
        <label>
          独立API地址（可选）
          <input name="api_endpoint" defaultValue={row?.api_endpoint} />
        </label>
        <label>
          令牌（留空保留已保存值）
          <input type="password" name="token" autoComplete="new-password" />
        </label>
      </div>
      <label>
        <input
          type="checkbox"
          name="enabled"
          defaultChecked={row?.enabled ?? true}
        />
        启用连接
      </label>
      <label>
        <input
          type="checkbox"
          name="at_all"
          defaultChecked={row?.allow_at_all ?? false}
        />
        允许明确的@全体请求（仍核对当前群权限与额度）
      </label>
      <label>
        明确扩展API（每行一项）
        <textarea
          name="extensions"
          defaultValue={row?.extension_apis?.join("\n")}
        />
      </label>
      <label>
        允许获取媒体的远程主机（每行一项）
        <textarea
          name="hosts"
          defaultValue={row?.allowed_media_hosts?.join("\n")}
        />
      </label>
      <details>
        <summary>协议声明、长媒体映射与SDK实例</summary>
        <p className="muted">
          本地与远程目录映射只用于已明确共享的媒体，不会把服务器路径发到浏览器预览。
        </p>
        <textarea
          name="advanced"
          rows={10}
          defaultValue={JSON.stringify(
            {
              api_support: row?.api_support ?? {},
              segment_support: row?.segment_support ?? {},
              media_path_mappings: row?.media_path_mappings ?? [],
              max_media_bytes: row?.max_media_bytes ?? 67108864,
              max_inline_bytes: row?.max_inline_bytes ?? 4194304,
              external_commands: row?.external_commands ?? [],
              ...(row?.bridge_instance_id
                ? { bridge_instance_id: row.bridge_instance_id }
                : {}),
              ...(row?.webhook_public_url
                ? { webhook_public_url: row.webhook_public_url }
                : {}),
              ...(row?.webhook_secret_ref
                ? { webhook_secret_ref: row.webhook_secret_ref }
                : {}),
            },
            null,
            2,
          )}
        />
      </details>
      {error && <p role="alert">{error}</p>}
      <div className="domain-actions">
        <button className="button primary" disabled={command.busy}>
          保存连接配置
        </button>
        <button className="button" type="button" onClick={close}>
          关闭
        </button>
      </div>
      <CommandFeedback {...command} />
    </form>
  );
}
function NativeApi({ connections }: { connections: ChannelConnection[] }) {
  const { actor } = useRoleWorkspace();
  const [connectionId, setConnectionId] = useState("");
  const connection = connections.find((x) => x.id === connectionId);
  const [cursor, setCursor] = useState<string | null>(null);
  const conversations = useDomainResource<Page<ConversationSelection>>(
    "identity/conversations",
    { ...actorScope(connection?.actor_id ?? actor), cursor, limit: 100 },
    !!(connection?.actor_id ?? actor),
  );
  const command = useDomainCommand(
    "explicit-channel-api:" + connectionId,
    "global",
  );
  const [error, setError] = useState("");
  return (
    <DetailCard title="明确调用渠道API">
      <p>
        发送操作仍进入唯一公开表达。请选择原逻辑会话，服务端会再次核对连接、目标、权限与当前来源。
      </p>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          setError("");
          if (!connection) return;
          const data = new FormData(event.currentTarget);
          const selected = conversations.data?.items.find(
            (x) => x.conversation_id === data.get("conversation"),
          );
          let params: Record<string, unknown>;
          try {
            params = JSON.parse(String(data.get("params")));
            if (!params || typeof params !== "object" || Array.isArray(params))
              throw new Error();
          } catch {
            setError("API参数必须是JSON对象。");
            return;
          }
          void command.run(
            "channels/actions/invoke",
            {
              ...actorScope(connection.actor_id),
              ...(selected
                ? {
                    conversation_id: selected.conversation_id,
                    person_id: selected.person_id,
                  }
                : {}),
              connection_id: connection.id,
              api_name: String(data.get("api")),
              params,
            },
            null,
          );
        }}
      >
        <div className="domain-grid">
          <label>
            连接
            <select
              value={connectionId}
              onChange={(event) => {
                setConnectionId(event.target.value);
                setCursor(null);
              }}
              required
            >
              <option value="">选择连接</option>
              {connections.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            当前授权会话
            <select name="conversation" key={connectionId + ":" + cursor}>
              <option value="">无会话（仅连接管理/读取）</option>
              {conversations.data?.items.map((x) => (
                <option key={x.conversation_id} value={x.conversation_id}>
                  {x.label} · {x.kind}
                </option>
              ))}
            </select>
          </label>
          <label>
            明确API
            <select name="api" key={connectionId} required>
              <option value="">选择API</option>
              {connection?.matrix.apis
                .filter((x) => x.implemented && x.provider_supported !== false)
                .map((x) => (
                  <option key={x.name} value={x.name}>
                    {x.name} · {x.effect ?? "未知效果"}
                  </option>
                ))}
            </select>
          </label>
        </div>
        {conversations.error && <p role="alert">{conversations.error}</p>}
        {conversations.data?.next_cursor && (
          <button
            type="button"
            className="text-link"
            onClick={() => setCursor(conversations.data!.next_cursor)}
          >
            下一页会话
          </button>
        )}
        {cursor && (
          <button
            type="button"
            className="text-link"
            onClick={() => setCursor(null)}
          >
            回到第一页会话
          </button>
        )}
        <label>
          API参数
          <textarea name="params" rows={5} defaultValue="{}" />
        </label>
        {error && <p role="alert">{error}</p>}
        <button className="button" disabled={command.busy || !connection}>
          执行明确API
        </button>
        <CommandFeedback {...command} />
        {command.result?.data != null && (
          <details>
            <summary>本次实际业务结果</summary>
            <pre>{readableRecord(command.result.data)}</pre>
          </details>
        )}
      </form>
    </DetailCard>
  );
}
