import { useState } from "react";
import { actorScope, useDomainCommand } from "../../api/useDomainResource";
import { CommandFeedback } from "../../components/CommandFeedback";
import { connectionName } from "../people/types";
import {
  adapterLabels,
  transportLabels,
  providerLabels,
  splitLines,
  type ChannelConnection,
  type RegisteredConnection,
} from "./channelTypes";

const transports: Record<string, string[]> = {
  onebot: ["http", "reverse_http", "ws", "reverse_ws"],
  telegram: ["telegram_webhook", "telegram_poll"],
  nonebot: ["bridge"],
  astrbot: ["bridge"],
};
export function ChannelConnectionEditor({
  row,
  registrations,
  done,
  close,
  preset,
}: {
  row: ChannelConnection | null;
  registrations: RegisteredConnection[];
  done: () => void;
  close: () => void;
  preset?: { adapter: string; provider: string; connectionId: string };
}) {
  const [adapter, setAdapter] = useState(
    row?.adapter ?? preset?.adapter ?? "onebot",
  );
  const [transport, setTransport] = useState(
    row?.transport ??
      (preset?.adapter && preset.adapter !== "onebot"
        ? transports[preset.adapter][0]
        : "reverse_ws"),
  );
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
            defaultValue={row?.id ?? preset?.connectionId}
            disabled={!!row}
            required
          >
            {row &&
              !registrations.some(
                (value) => value.connection_id === row.id,
              ) && <option value={row.id}>{connectionName(row)}</option>}
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
              <option key={value} value={value}>
                {transportLabels[value] ?? value}
              </option>
            ))}
          </select>
        </label>
        <label>
          对端类型
          <select
            name="provider"
            defaultValue={row?.provider ?? preset?.provider ?? "generic"}
          >
            {[
              "generic",
              "napcat",
              "snowluma",
              "telegram",
              "nonebot",
              "astrbot",
            ].map((value) => (
              <option key={value} value={value}>
                {providerLabels[value] ?? value}
              </option>
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
