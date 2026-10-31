import { DetailCard } from "../../components/DetailCard";
import { useEffect, useState, type FormEvent } from "react";
import { Archive, RefreshCw, UsersRound, UserRound } from "lucide-react";
import { StatusRail } from "../../components/StatusRail";
import { callOperation } from "../../api/operations";
import { requestId } from "../../app/requestId";
import { type GroupPolicy } from "./GroupPolicyEditor";
import { OneBotOnboarding } from "./OneBotOnboarding";
import {
  adapterPost,
  type AdapterKind,
  type AdapterProbe,
} from "./botAdapterApi";

type Policy = {
  observe: boolean;
  mode: "observe_only" | "whitelist" | "blacklist";
  list: string[];
  actor_id: string | null;
};
type Connection = {
  id: string;
  name: string;
  adapter: AdapterKind;
  account_id: string;
  instance_id: string;
  enabled: boolean;
  revision: number;
  host_revision: number;
  state: string;
  last_error: string | null;
  last_checked_at: string | null;
  read_enabled: boolean;
  host_pending: number;
  host_dropped: number;
  group_policy: GroupPolicy;
  private_policy: Policy;
  pending: boolean;
  group_policy_version: number | null;
  ordinary_claim_available: boolean;
};
type View = {
  available: boolean;
  unlocked: boolean;
  connections: Connection[];
  participation?: {
    available: boolean;
    reason: string | null;
    last_error: string | null;
  } | null;
};
type Found = {
  conversation: string;
  author: string;
  count: number;
  last_at: number;
  decision: {
    observe: boolean;
    reply_permitted: boolean;
    reply_triggered: boolean;
  };
};
type FoundPage = {
  items: Found[];
  next_cursor: { conversation: string; author: string } | null;
};
type Archive = {
  memory_state: string;
  backlog: Record<string, number>;
  archive_next_cursor: string | null;
  items: {
    source_ref: string;
    author: string;
    archive_state: string;
    error_code: string | null;
  }[];
  archive_items: {
    source_ref: string;
    author: string;
    content_state: string;
    text: string;
    sent_at: string;
  }[];
};

async function post<T>(
  operation: string,
  body: object,
  csrf: string,
): Promise<T> {
  return callOperation<T>(`channels/observations/${operation}`, body, csrf, new AbortController().signal);
}

function ids(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\s,，;；]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function modeText(mode: Policy["mode"], list: string[]) {
  if (mode === "observe_only") return "只观察，任何消息都不生成回复";
  if (mode === "whitelist")
    return list.length
      ? `仅 ${list.length} 个会话可触发回复`
      : "白名单为空：所有会话都不回复";
  return list.length
    ? `除 ${list.length} 个会话外均可触发回复`
    : "黑名单为空：所有会话均可触发回复";
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    ready: "运行正常",
    disabled: "已停用",
    pending: "待确认",
    unknown: "状态待核对",
    degraded: "运行受限",
    failed: "连接失败",
    error: "连接异常",
  };
  return labels[state] ?? "状态待核对";
}

function archiveStateLabel(state: string) {
  const labels: Record<string, string> = {
    archived: "已归档",
    pending_memory: "等待 Memory 归档",
    failed: "归档失败",
    revoked: "已撤销",
  };
  return labels[state] ?? "归档状态待核对";
}

function stateTone(state: string): "blue" | "yellow" | "red" | "gray" {
  if (state === "ready") return "blue";
  if (state === "failed" || state === "error") return "red";
  return state === "disabled" ? "gray" : "yellow";
}

function PolicyEditor({
  title,
  kind,
  value,
  actors,
  disabled,
  onChange,
}: {
  title: string;
  kind: "group" | "private";
  value: Policy;
  actors: { id: string; label: string }[];
  disabled: boolean;
  onChange: (value: Policy) => void;
}) {
  return (
    <fieldset className="bot-observation-policy bot-form" disabled={disabled}>
      <legend>
        {kind === "group" ? (
          <UsersRound aria-hidden="true" />
        ) : (
          <UserRound aria-hidden="true" />
        )}
        {title}策略
      </legend>
      <label className="bot-check">
        <input
          type="checkbox"
          checked={value.observe}
          onChange={(event) =>
            onChange({ ...value, observe: event.target.checked })
          }
        />
        观察新收到的{title}消息
      </label>
      <label>
        回复模式
        <select
          value={value.mode}
          onChange={(event) =>
            onChange({
              ...value,
              mode: event.target.value as Policy["mode"],
              list: [],
            })
          }
        >
          <option value="observe_only">仅观察（默认）</option>
          <option value="whitelist">白名单</option>
          <option value="blacklist">黑名单</option>
        </select>
      </label>
      <label>
        {kind === "group" ? "群号名单" : "私聊对方账号名单"}
        <textarea
          rows={2}
          value={value.list.join("\n")}
          onChange={(event) =>
            onChange({ ...value, list: ids(event.target.value) })
          }
          placeholder="每行一个 QQ 号"
        />
      </label>
      <label>
        默认回复角色
        <select
          value={value.actor_id ?? ""}
          onChange={(event) =>
            onChange({ ...value, actor_id: event.target.value || null })
          }
        >
          <option value="">不指定（仅观察可用）</option>
          {actors.map((actor) => (
            <option key={actor.id} value={actor.id}>
              {actor.label}
            </option>
          ))}
        </select>
      </label>
      <p className="bot-observation-policy-note">
        <strong>{modeText(value.mode, value.list)}。</strong>{" "}
        {kind === "group" ? "群聊还须直接 @机器人。" : "私聊允许时正常触发。"}
        切换回复模式会清空原模式名单。允许回复不会追补旧观察。停止观察只停止新增消息，既有档案仍可按当前读取授权查看。
      </p>
    </fieldset>
  );
}

export function BotObservationPanel({
  csrf,
  unlocked,
  actors,
}: {
  csrf: string;
  unlocked: boolean;
  actors: { id: string; label: string }[];
}) {
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState("");
  const [managedConnections, setManagedConnections] = useState<string[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [serverEpoch, setServerEpoch] = useState(0);
  const [group, setGroup] = useState<GroupPolicy | null>(null);
  const [privatePolicy, setPrivate] = useState<Policy | null>(null);
  const [probe, setProbe] = useState<AdapterProbe | null>(null);
  const [adapter, setAdapter] = useState<AdapterKind | "builtin">("builtin");
  const [address, setAddress] = useState("");
  const [key, setKey] = useState("");
  const [privateHttp, setPrivateHttp] = useState(false);
  const [caPem, setCaPem] = useState("");
  const [account, setAccount] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [found, setFound] = useState<Found[]>([]);
  const [cursor, setCursor] = useState<FoundPage["next_cursor"]>(null);
  const [archive, setArchive] = useState<Archive | null>(null);
  const [archiveConversation, setArchiveConversation] = useState("");

  const current = view?.connections.find((item) => item.id === selected);

  async function refresh() {
    if (!csrf) return;
    const value = await post<View>("view", {}, csrf);
    setView(value);
    const row =
      value.connections.find((item) => item.id === selected) ??
      value.connections[0];
    if (row) {
      setSelected(row.id);
      setGroup(row.group_policy);
      setPrivate(row.private_policy);
    }
  }

  useEffect(() => {
    void refresh().catch((cause) => setError(String(cause)));
  }, [csrf, unlocked]);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "连接中断，结果待核对，请刷新状态。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function detect(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const result = await adapterPost<AdapterProbe>(
        "probe",
        {
          adapter,
          address: address.trim(),
          access_key: key,
          allow_private_http: privateHttp,
          ca_pem: caPem.trim() || null,
        },
        csrf,
        new AbortController().signal,
      );
      setKey("");
      setProbe(result);
      setAccount(
        result.accounts.find(
          (item) =>
            !view?.connections.some(
              (connection) =>
                connection.instance_id === result.instance_id &&
                connection.account_id === item.id,
            ),
        )?.id ?? "",
      );
      setNotice(
        result.accounts.length
          ? "已检测在线账号。新群默认停用，仅登记基础资料；私聊按原策略观察。"
          : "尚未检测到在线账号，请在 SnowLuma 连接后重新检测。",
      );
    });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!probe || !account) return;
    await run(async () => {
      const receipt = await post<{ connection: Connection }>(
        "create",
        {
          draft_id: probe.draft_id,
          account_id: account,
          name: name.trim(),
          client_request_id: requestId(),
        },
        csrf,
      );
      await refresh();
      setSelected(receipt.connection.id);
      setGroup(receipt.connection.group_policy);
      setPrivate(receipt.connection.private_policy);
      setProbe(null);
      setNotice(
        receipt.connection.state === "ready"
          ? "账号已接入。新群仅登记基础资料，可在群画像页开启；私聊仅观察。"
          : "保存已提交，但插件状态待核对；请刷新后检查积压。",
      );
    });
  }

  async function savePolicy() {
    if (!current || !group || !privatePolicy) return;
    await run(async () => {
      const receipt = await post<{ connection: Connection }>(
        "policy",
        {
          id: current.id,
          expected_revision: current.revision,
          group_policy: group,
          private_policy: privatePolicy,
          client_request_id: requestId(),
        },
        csrf,
      );
      await refresh();
      setNotice(
        receipt.connection.state === "ready"
          ? "群聊与私聊策略已保存并从服务端读回。"
          : "策略保存结果待插件确认，请刷新核对；在此之前不会放宽回复。",
      );
    });
  }

  async function loadFound(next: FoundPage["next_cursor"] = null) {
    if (!current) return;
    await run(async () => {
      const page = await post<FoundPage>(
        "discovered",
        { id: current.id, limit: 20, cursor: next },
        csrf,
      );
      setFound(next ? [...found, ...page.items] : page.items);
      setCursor(page.next_cursor);
    });
  }

  async function loadArchive(conversation: string, next: string | null = null) {
    if (!current) return;
    await run(async () => {
      const result = await post<Archive>(
        "archive",
        {
          id: current.id,
          conversation_id: conversation,
          limit: 50,
          cursor: next,
        },
        csrf,
      );
      setArchiveConversation(conversation);
      setArchive(
        next && archive
          ? {
              ...result,
              items: [...archive.items, ...result.items],
              archive_items: [
                ...archive.archive_items,
                ...result.archive_items,
              ],
            }
          : result,
      );
    });
  }

  function addToList(conversation: string) {
    const [kind, id] = conversation.split(":");
    if (kind === "group") {
      window.location.hash = "#/groups";
      return;
    }
    const value = privatePolicy;
    if (!value) return;
    const updated = {
      ...value,
      mode: "whitelist" as const,
      list: [
        ...new Set([...(value.mode === "whitelist" ? value.list : []), id]),
      ],
    };
    setPrivate(updated);
    setNotice("已加入待保存白名单。选择默认回复角色后点击“保存私聊策略”。");
  }

  async function setHistory(readEnabled: boolean) {
    if (!current) return;
    await run(async () => {
      const receipt = await post<{ connection: Connection }>(
        "history",
        {
          id: current.id,
          expected_revision: current.revision,
          read_enabled: readEnabled,
          client_request_id: requestId(),
        },
        csrf,
      );
      await refresh();
      setFound([]);
      setCursor(null);
      setArchive(null);
      setNotice(
        receipt.connection.read_enabled
          ? "历史读取已恢复，仅能查看当前授权版本的新档案。"
          : "历史读取已撤销，旧档案和发现列表立即不可见，新增观察暂停。",
      );
    });
  }

  if (!view?.available) return null;
  return (
    <section
      className="bot-adapter bot-observation"
      aria-label="账号级观察与回复策略"
    >
      <div className="section-heading">
        <div>
          <h2>机器人账号</h2>
          <p className="muted">
            连接后，在卡片里管理每个账号的观察与回复范围。
          </p>
        </div>
        <button
          className="button"
          type="button"
          disabled={busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} aria-hidden="true" /> 刷新状态
        </button>
      </div>
      <details className="bot-policy-hint">
        <summary>默认回复规则</summary>
        <p className="bot-observation-intro">
          新连接收到群消息时仅登记基础资料。群聊默认停用，可在群画像页逐群开启；私聊默认仅观察。
          天枢仅控制自己的回复，宿主中的其他插件仍可能独立回复。旧精确范围连接不会自动升级。
        </p>
      </details>
      {error && (
        <p role="alert" className="bot-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="bot-notice">
          {notice}
        </p>
      )}
      {unlocked && (
        <OneBotOnboarding
          key={serverEpoch}
          csrf={csrf}
          onChanged={() => void refresh()}
          mode="cards"
          onConnections={setManagedConnections}
          onConfigure={(id) => {
            const row = view?.connections.find((item) => item.id === id);
            if (!row) return;
            setSelected(row.id);
            setGroup(row.group_policy);
            setPrivate(row.private_policy);
            setFound([]);
            setCursor(null);
            setArchive(null);
          }}
        />
      )}
      {unlocked && (
        <DetailCard
          title={"添加机器人账号"}
          open={addOpen}
          onOpenChange={setAddOpen}
        >
          {error && <p role="alert">{error}</p>}
          {notice && <p role="status">{notice}</p>}
          <div className="bot-form bot-observation-card">
            <h3>连接与账号</h3>
            <label>
              接入方式
              <select
                value={adapter}
                onChange={(event) => {
                  setAdapter(event.target.value as AdapterKind | "builtin");
                  setProbe(null);
                  setAccount("");
                }}
              >
                <option value="builtin">内置 OneBot v11（QQ）</option>
                <option value="astrbot">外部 AstrBot</option>
                <option value="nonebot">外部 NoneBot</option>
                <option value="onebot">外部 OneBot / NapCat</option>
              </select>
            </label>
            {adapter === "builtin" ? (
              <OneBotOnboarding
                csrf={csrf}
                onChanged={() => void refresh()}
                mode="form"
                onCreated={() => {
                  setAddOpen(false);
                  setServerEpoch((n) => n + 1);
                  setNotice(
                    "机器人已创建。展开卡片中的连接信息，将地址和 Token 填入 SnowLuma。",
                  );
                  void refresh();
                }}
              />
            ) : (
              <form
                className="bot-form"
                onSubmit={(event) => void detect(event)}
              >
                <label>
                  观察服务 URL
                  <input
                    type="url"
                    required
                    value={address}
                    onChange={(event) => setAddress(event.target.value)}
                  />
                  {adapter === "onebot" && (
                    <span className="muted">
                      填写天枢 OneBot 接入服务的 HTTP 地址；QQ
                      账号由该服务连接的 NapCat 等 OneBot 实现提供。
                    </span>
                  )}
                </label>
                <label>
                  观察服务密钥
                  <input
                    type="password"
                    required
                    autoComplete="off"
                    value={key}
                    onChange={(event) => setKey(event.target.value)}
                  />
                </label>
                <label className="bot-check">
                  <input
                    type="checkbox"
                    checked={privateHttp}
                    onChange={(event) => setPrivateHttp(event.target.checked)}
                  />
                  允许已审查的局域网 HTTP
                </label>
                <label>
                  插件 TLS CA（如需）
                  <textarea
                    rows={3}
                    value={caPem}
                    onChange={(event) => setCaPem(event.target.value)}
                  />
                </label>
                <button className="button" disabled={busy}>
                  检测在线账号
                </button>
              </form>
            )}
          </div>
          {probe && (
            <form
              className="bot-form bot-observation-card"
              onSubmit={(event) => void create(event)}
            >
              <h3>确认在线账号</h3>
              <label>
                账号观察名称
                <input
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <label>
                真实在线机器人账号
                <select
                  value={account}
                  onChange={(event) => setAccount(event.target.value)}
                >
                  {!account && <option value="">没有可添加的在线账号</option>}
                  {probe.accounts.map((item) => {
                    const added = view.connections.some(
                      (connection) =>
                        connection.instance_id === probe.instance_id &&
                        connection.account_id === item.id,
                    );
                    return (
                      <option key={item.id} value={item.id} disabled={added}>
                        {item.label}（{item.id}）{added ? " · 已添加" : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
              <button className="button" disabled={busy || !account}>
                保存账号连接
              </button>
            </form>
          )}
        </DetailCard>
      )}
      {view.connections.length > 0 && (
        <div className="bot-connection-cards">
          {view.connections
            .filter((row) => !unlocked || !managedConnections.includes(row.id))
            .map((row) => (
              <article className="bot-connection-card" key={row.id}>
                <header>
                  <span className="role-context-avatar">
                    {row.name.slice(0, 1)}
                  </span>
                  <h3>{row.name}</h3>
                </header>
                <p className="bot-card-qq">QQ {row.account_id}</p>
                <p>{stateLabel(row.state)}</p>
                <button
                  type="button"
                  className="button"
                  aria-pressed={selected === row.id}
                  onClick={() => {
                    setSelected(row.id);
                    setGroup(row.group_policy);
                    setPrivate(row.private_policy);
                    setFound([]);
                    setCursor(null);
                    setArchive(null);
                  }}
                >
                  观察与回复设置
                </button>
              </article>
            ))}
        </div>
      )}
      {current && group && privatePolicy && (
        <>
          <div
            className="bot-observation-card bot-observation-summary"
            role="status"
          >
            <div className="bot-observation-summary-head">
              <div>
                <h3>{current.name}</h3>
                <p className="muted">
                  观察账号{" "}
                  <span className="bot-observation-id">
                    {current.account_id}
                  </span>
                </p>
              </div>
              <StatusRail
                tone={stateTone(current.state)}
                label={stateLabel(current.state)}
              />
            </div>
            <dl className="bot-observation-facts">
              <div>
                <dt>宿主待转交</dt>
                <dd>{current.host_pending}</dd>
              </div>
              <div>
                <dt>容量拒收</dt>
                <dd>{current.host_dropped}</dd>
              </div>
              <div>
                <dt>历史读取</dt>
                <dd>{current.read_enabled ? "允许" : "已撤销"}</dd>
              </div>
            </dl>
            {current.last_error && (
              <p role="alert" className="bot-observation-alert">
                最近失败：{current.last_error}。积压可能尚未归档。
              </p>
            )}
            <button
              className="button bot-observation-history"
              type="button"
              disabled={!unlocked || busy}
              onClick={() => void setHistory(!current.read_enabled)}
            >
              {current.read_enabled
                ? "撤销历史读取并暂停新增观察"
                : "恢复历史读取与新增观察"}
            </button>
          </div>
          <DetailCard title="群聊与私聊策略">
            {error && <p role="alert">{error}</p>}
            {notice && <p role="status">{notice}</p>}
            <div className="bot-observation-policies">
              <p>
                群聊状态、负责角色和回复关键词已移至
                <a href="#/groups">用户 → 群画像</a>
                。未单独配置的群仅登记基础资料，默认停用。
              </p>
              <p role="status">
                主机群策略能力：
                {current.group_policy_version === 2
                  ? "支持四态"
                  : "尚未确认支持四态，请更新主机插件"}
                ；普通消息领取：
                {current.ordinary_claim_available
                  ? "主机已支持"
                  : "主机未报告能力，主动参与不可用"}
                。参与消费者：
                {view.participation?.available
                  ? "已接通"
                  : (view.participation?.reason ?? "尚未确认")}
                {view.participation?.last_error
                  ? `（${view.participation.last_error}）`
                  : ""}
                。
              </p>
              <PolicyEditor
                title="私聊"
                kind="private"
                value={privatePolicy}
                actors={actors}
                disabled={!unlocked || busy}
                onChange={setPrivate}
              />
            </div>
            <div className="bot-observation-save">
              <button
                className="button primary"
                type="button"
                disabled={!unlocked || busy || current.state === "unknown"}
                onClick={() => void savePolicy()}
              >
                保存私聊策略
              </button>
            </div>
          </DetailCard>
          <div className="bot-observation-discovered-heading">
            <div>
              <h3>已发现会话</h3>
              <p className="muted">按需读取收到消息的会话与归档状态。</p>
            </div>
            <button
              className="button"
              type="button"
              disabled={busy || !current.read_enabled}
              onClick={() => void loadFound()}
            >
              读取发现列表
            </button>
          </div>
          <ul className="bot-observation-list">
            {found.map((item) => (
              <li key={`${item.conversation}:${item.author}`}>
                <div className="bot-observation-item-head">
                  <strong className="bot-observation-id">
                    {item.conversation}
                  </strong>
                  <StatusRail
                    tone={item.decision.reply_permitted ? "blue" : "gray"}
                    label={
                      item.decision.reply_permitted ? "可触发回复" : "只观察"
                    }
                  />
                </div>
                <p className="muted">
                  发言人{" "}
                  <span className="bot-observation-id">{item.author}</span> ·
                  已收到 {item.count} 条 · 最近{" "}
                  {new Date(item.last_at * 1000).toLocaleString("zh-CN")}
                </p>
                <div className="bot-observation-item-actions">
                  <button
                    className="button"
                    type="button"
                    disabled={busy || !unlocked}
                    onClick={() => addToList(item.conversation)}
                  >
                    {item.conversation.startsWith("group:")
                      ? "配置此群状态"
                      : "加入回复白名单"}
                  </button>
                  <button
                    className="button"
                    type="button"
                    disabled={busy || !current.read_enabled}
                    onClick={() => void loadArchive(item.conversation)}
                  >
                    查看归档状态
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {cursor && (
            <button
              className="button"
              type="button"
              disabled={busy}
              onClick={() => void loadFound(cursor)}
            >
              加载更多
            </button>
          )}
          {archive && (
            <div
              className="bot-observation-card bot-observation-archive"
              role="status"
            >
              <div className="bot-observation-item-head">
                <h4>
                  <Archive size={18} aria-hidden="true" />{" "}
                  <span className="bot-observation-id">
                    {archiveConversation}
                  </span>{" "}
                  的归档
                </h4>
                <StatusRail
                  tone={
                    archive.memory_state === "available" ? "blue" : "yellow"
                  }
                  label={
                    archive.memory_state === "available"
                      ? "Memory 可读取"
                      : "Memory 暂不可读取"
                  }
                />
              </div>
              <dl className="bot-observation-facts">
                <div>
                  <dt>已确认归档</dt>
                  <dd>{archive.archive_items.length}</dd>
                </div>
                <div>
                  <dt>待归档</dt>
                  <dd>{archive.backlog.pending_memory ?? 0}</dd>
                </div>
                <div>
                  <dt>失败</dt>
                  <dd>{archive.backlog.failed ?? 0}</dd>
                </div>
                <div>
                  <dt>撤销</dt>
                  <dd>{archive.backlog.revoked ?? 0}</dd>
                </div>
              </dl>
              <ul className="bot-observation-archive-list">
                {archive.items.map((item) => (
                  <li key={item.source_ref}>
                    <span className="bot-observation-id">
                      来源 {item.source_ref}
                    </span>{" "}
                    · 发言人{" "}
                    <span className="bot-observation-id">{item.author}</span> ·{" "}
                    {archiveStateLabel(item.archive_state)}
                    {item.error_code ? ` · ${item.error_code}` : ""}
                  </li>
                ))}
              </ul>
              <h5>授权可读正文</h5>
              <ul className="bot-observation-archive-list">
                {archive.archive_items.map((item) => (
                  <li key={item.source_ref}>
                    {item.sent_at} · 发言人 {item.author} ·{" "}
                    {item.content_state === "text"
                      ? item.text
                      : "非文本内容，仅存元数据"}
                  </li>
                ))}
              </ul>
              {archive.archive_next_cursor && (
                <button
                  className="button"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void loadArchive(
                      archiveConversation,
                      archive.archive_next_cursor,
                    )
                  }
                >
                  加载更多归档
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
