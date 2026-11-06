import { useState } from "react";
import { Bot, MessageCircle, Plug, Send } from "lucide-react";
import { requestId } from "../../app/requestId";
import { Drawer } from "../../components/Drawer";
import { connectionName } from "../people/types";
import { ConnectionIdentityEditor } from "./ChannelIdentityPanel";
import { ChannelConnectionEditor } from "./ChannelConnectionEditor";
import type { RegisteredConnection } from "./channelTypes";

const connectors = [
  {
    id: "onebot",
    title: "QQ / OneBot",
    description: "连接 NapCat、SnowLuma 等 OneBot 服务。",
    namespace: "qq",
    provider: "napcat",
    icon: MessageCircle,
  },
  {
    id: "telegram",
    title: "Telegram",
    description: "使用机器人令牌，通过轮询或 Webhook 接入。",
    namespace: "telegram",
    provider: "telegram",
    icon: Send,
  },
  {
    id: "nonebot",
    title: "NoneBot",
    description: "连接已有 NoneBot 宿主的天枢桥接插件。",
    namespace: "qq",
    provider: "nonebot",
    icon: Plug,
  },
  {
    id: "astrbot",
    title: "AstrBot",
    description: "连接已有 AstrBot 宿主的天枢桥接插件。",
    namespace: "qq",
    provider: "astrbot",
    icon: Bot,
  },
] as const;

type Setup = {
  connector: (typeof connectors)[number];
  draftId: string;
  registeredId: string | null;
};

export function BotConnectorSetup({
  registrations,
  configuredIds,
  canRegister,
  refresh,
}: {
  registrations: RegisteredConnection[];
  configuredIds: string[];
  canRegister: boolean;
  refresh: () => void;
}) {
  const [setup, setSetup] = useState<Setup | null>(null);
  const available = registrations.filter(
    (row) => !configuredIds.includes(row.connection_id),
  );
  const registered = registrations.find(
    (row) => row.connection_id === setup?.registeredId,
  );
  const matchingAccounts = available.filter(
    (row) =>
      !setup ||
      ["nonebot", "astrbot"].includes(setup.connector.id) ||
      row.namespace === setup.connector.namespace,
  );
  const [existingId, setExistingId] = useState("");
  const close = () => setSetup(null);

  return (
    <section className="panel bot-connector-setup">
      <div className="section-heading">
        <div>
          <h3>添加机器人连接器</h3>
          <p>选择连接方式，再填写账号、负责角色和连接设置。</p>
        </div>
      </div>
      <div className="bot-connector-grid">
        {connectors.map((connector) => (
          <button
            className="bot-connector-card"
            type="button"
            key={connector.id}
            disabled={!canRegister && !available.length}
            aria-haspopup="dialog"
            onClick={() => {
              setExistingId("");
              setSetup({ connector, draftId: requestId(), registeredId: null });
            }}
          >
            <connector.icon aria-hidden="true" />
            <strong>{connector.title}</strong>
            <span>{connector.description}</span>
            <span className="bot-connector-add">添加连接器 →</span>
          </button>
        ))}
      </div>
      {!canRegister && (
        <p className="muted">新账号登记需要机器人身份管理权限。</p>
      )}
      <Drawer
        title={
          setup ? `添加 ${setup.connector.title} 连接器` : "添加机器人连接器"
        }
        variant="card"
        open={!!setup}
        onClose={close}
      >
        {setup && (
          <div className="bot-connector-flow">
            <ol className="bot-wizard-steps">
              <li>登记账号与负责角色</li>
              <li>保存连接地址和凭据</li>
            </ol>
            {!setup.registeredId ? (
              <>
                {matchingAccounts.length > 0 && (
                  <div className="domain-form bot-wizard">
                    <h4>继续配置已登记账号</h4>
                    <label>
                      机器人账号
                      <select
                        value={existingId}
                        onChange={(event) => setExistingId(event.target.value)}
                      >
                        <option value="">选择尚未配置传输的账号</option>
                        {matchingAccounts.map((row) => (
                          <option
                            value={row.connection_id}
                            key={row.connection_id}
                          >
                            {connectionName(row)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      className="button"
                      type="button"
                      disabled={!existingId}
                      onClick={() =>
                        setSetup({ ...setup, registeredId: existingId })
                      }
                    >
                      继续配置连接
                    </button>
                  </div>
                )}
                {canRegister && (
                  <ConnectionIdentityEditor
                    key={setup.draftId}
                    row={null}
                    preset={{
                      connectionId: setup.draftId,
                      namespace: setup.connector.namespace,
                    }}
                    done={(connectionId) => {
                      setSetup({ ...setup, registeredId: connectionId });
                      refresh();
                    }}
                    cancel={close}
                  />
                )}
              </>
            ) : registered ? (
              <>
                <p>已登记：{connectionName(registered)}。继续保存连接设置。</p>
                <ChannelConnectionEditor
                  row={null}
                  registrations={[registered]}
                  preset={{
                    adapter: setup.connector.id,
                    provider: setup.connector.provider,
                    connectionId: registered.connection_id,
                  }}
                  done={() => {
                    close();
                    refresh();
                  }}
                  close={close}
                />
              </>
            ) : (
              <div className="domain-form">
                <p role="status">账号已登记，正在读取连接信息…</p>
                <button className="button" onClick={refresh}>
                  重新读取账号
                </button>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </section>
  );
}
