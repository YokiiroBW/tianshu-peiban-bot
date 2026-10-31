import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  MessagesSquare,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react";
import { useAuth } from "../../app/Auth";
import { writeFailure } from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { GraphBoard, type GraphEdge } from "./GraphBoard";
import { saveGroupActor, useGraphRead } from "./api";
import {
  formatTime,
  stateLabel,
  type Conversation,
  type Relationships,
} from "./types";

const bindingLabels = {
  explicit: "单独绑定",
  inherited: "继承账号默认角色",
  registered: "登记绑定",
  unbound: "尚未绑定角色",
};

export default function RelationshipView() {
  const query = useGraphRead<Relationships>("relationships", {});
  const { session } = useAuth();
  const [kind, setKind] = useState("all");
  const [account, setAccount] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState("");
  const [draftActor, setDraftActor] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState("");
  const savingRequest = useRef<AbortController | null>(null);
  useEffect(() => () => savingRequest.current?.abort(), []);
  const data = query.data;
  useEffect(() => setSelected(""), [kind, account, search]);
  const visible = useMemo(() => {
    if (!data)
      return {
        accounts: [],
        roles: [],
        conversations: [],
        edges: [] as GraphEdge[],
        related: new Set<string>(),
      };
    const term = search.trim().toLocaleLowerCase();
    const conversations = data.conversations.filter((item) => {
      const labels = [
        item.label,
        item.conversation_id,
        ...item.actor_ids.map(
          (id) => data.roles.find((role) => role.id === id)?.label ?? id,
        ),
        data.accounts.find((entry) => entry.id === item.account_id)?.label ??
          "",
      ];
      return (
        (kind === "all" || item.kind === kind) &&
        (!account || item.account_id === account) &&
        (!term ||
          labels.some((label) => label.toLocaleLowerCase().includes(term)))
      );
    });
    const accountIds = new Set(conversations.map((item) => item.account_id));
    const roleIds = new Set(conversations.flatMap((item) => item.actor_ids));
    data.defaults.forEach((entry) => {
      if (entry.actor_id && entry.account_id === account)
        roleIds.add(entry.actor_id);
    });
    const filtering = Boolean(term || account || kind !== "all");
    const accounts = data.accounts.filter(
      (entry) => !filtering || accountIds.has(entry.id) || entry.id === account,
    );
    const roles = data.roles.filter(
      (entry) => !filtering || roleIds.has(entry.id),
    );
    const allEdges: GraphEdge[] = [];
    const edgeKeys = new Set<string>();
    const add = (from: string, to: string, dashed = false) => {
      const key = `${from}:${to}`;
      if (!edgeKeys.has(key)) {
        allEdges.push({ from, to, dashed, active: false });
        edgeKeys.add(key);
      }
    };
    conversations.forEach((item) => {
      const actorIds = item.actor_ids.length ? item.actor_ids : ["unbound"];
      actorIds.forEach((id) => {
        add(item.account_id, id, id === "unbound");
        add(id, item.id, item.binding === "inherited" || id === "unbound");
      });
    });
    const related = new Set<string>(selected ? [selected] : []);
    data.defaults.forEach((entry) => {
      if (
        entry.actor_id &&
        accounts.some((item) => item.id === entry.account_id) &&
        roles.some((item) => item.id === entry.actor_id)
      )
        add(entry.account_id, entry.actor_id, true);
    });
    if (selected) {
      data.defaults.forEach((entry) => {
        if (
          entry.actor_id &&
          (selected === entry.account_id || selected === entry.actor_id)
        ) {
          related.add(entry.account_id);
          related.add(entry.actor_id);
        }
      });
      const selectedWindow = conversations.find((item) => item.id === selected);
      const selectedAccount = accounts.some((entry) => entry.id === selected);
      conversations.forEach((item) => {
        if (
          item.id === selectedWindow?.id ||
          (item.account_id === selected && selectedAccount) ||
          item.actor_ids.includes(selected) ||
          (selected === "unbound" && !item.actor_ids.length)
        ) {
          related.add(item.id);
          related.add(item.account_id);
          (item.actor_ids.length ? item.actor_ids : ["unbound"]).forEach((id) =>
            related.add(id),
          );
        }
      });
    }
    return {
      accounts,
      roles,
      conversations,
      related,
      edges: allEdges.map((edge) => ({
        ...edge,
        active: Boolean(
          selected && related.has(edge.from) && related.has(edge.to),
        ),
      })),
    };
  }, [data, kind, account, search, selected]);
  const conversation = data?.conversations.find(
    (entry) => entry.id === selected,
  );
  const role = data?.roles.find((entry) => entry.id === selected);
  const selectedAccount = data?.accounts.find((entry) => entry.id === selected);
  const dim = (id: string) =>
    selected && !visible.related.has(id) ? "is-muted" : "";
  function select(id: string) {
    setSelected((previous) => (previous === id ? "" : id));
    setDraftActor(
      data?.conversations.find((entry) => entry.id === id)?.actor_ids[0] ?? "",
    );
    setSaveError("");
    setNotice("");
  }
  async function save(item: Conversation) {
    if (!session?.authenticated || saving) return;
    const controller = new AbortController();
    savingRequest.current = controller;
    setSaving(true);
    setSaveError("");
    try {
      const result = await saveGroupActor(
        item,
        draftActor || null,
        session.csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setNotice(
          result.connection.pending || result.connection.state === "unknown"
            ? "映射已提交，宿主结果待确认；请刷新核对。"
            : "映射已保存。图中状态来自重新读取。",
        );
        await query.reload();
      }
    } catch (cause) {
      if (!controller.signal.aborted) setSaveError(writeFailure(cause));
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }
  if (query.loading && !data)
    return (
      <StatePanel kind="loading" title="正在读取配置关系">
        核对已登记账号、角色和会话映射。
      </StatePanel>
    );
  if (query.error)
    return (
      <StatePanel
        kind="error"
        title="配置关系读取失败"
        action={
          <button className="button" onClick={() => void query.reload()}>
            重试
          </button>
        }
      >
        {query.error}
      </StatePanel>
    );
  if (!data) return null;
  const counts = {
    all: data.conversations.length,
    private: data.conversations.filter((entry) => entry.kind === "private")
      .length,
    group: data.conversations.filter((entry) => entry.kind === "group").length,
  };
  return (
    <div className="flow-relationships">
      <div className="flow-toolbar">
        <p>
          {data.accounts.length} 个账号 · {data.roles.length} 个角色 ·{" "}
          {data.conversations.length} 个已列明会话
        </p>
        <button
          className="button"
          disabled={query.loading}
          onClick={() => void query.reload()}
        >
          <RefreshCw aria-hidden="true" />
          {query.loading ? "读取中" : "刷新关系"}
        </button>
      </div>
      <div className="flow-filters">
        <label className="flow-search">
          <Search aria-hidden="true" />
          <input
            aria-label="搜索关系"
            placeholder="搜索角色、账号或会话"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label>
          账号
          <select
            aria-label="按账号筛选"
            value={account}
            onChange={(event) => setAccount(event.target.value)}
          >
            <option value="">全部账号</option>
            {data.accounts.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}（{entry.account_id}）
              </option>
            ))}
          </select>
        </label>
        <div className="flow-segments" role="group" aria-label="会话类型">
          {(
            [
              ["all", "全部"],
              ["private", "私聊"],
              ["group", "群聊"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={kind === value}
              onClick={() => setKind(value)}
            >
              {label} {counts[value]}
            </button>
          ))}
        </div>
      </div>
      {!data.discovered_complete && (
        <p className="flow-hint">
          当前列出明确配置的会话；已发现的继承会话只在原机器人管理授权有效时读取，发现列表也可能尚未读完。默认策略不计作实际会话。
        </p>
      )}
      {data.sources.every((entry) => !entry.available) ? (
        <StatePanel
          kind="unconfigured"
          title="尚未配置机器人连接"
          action={
            <a className="button" href="#/settings/channels">
              配置机器人接入
            </a>
          }
        >
          连接配置后，这里显示其实际关系。
        </StatePanel>
      ) : (
        <GraphBoard edges={visible.edges}>
          <section className="flow-column" aria-label="机器人账号">
            <h3>
              <span>01</span>
              <Bot aria-hidden="true" />
              机器人账号 <small>{visible.accounts.length}</small>
            </h3>
            <div className="flow-column-body">
              {visible.accounts.map((entry) => (
                <button
                  type="button"
                  key={entry.id}
                  data-flow-node={entry.id}
                  aria-pressed={selected === entry.id}
                  className={`flow-node flow-account ${dim(entry.id)}`}
                  onClick={() => select(entry.id)}
                >
                  <strong>{entry.label}</strong>
                  <span>
                    {entry.adapter} · {entry.account_id}
                  </span>
                  <small>{entry.states.map(stateLabel).join(" / ")}</small>
                  <span className="flow-tag">
                    {
                      data.conversations.filter(
                        (item) => item.account_id === entry.id,
                      ).length
                    }{" "}
                    个会话
                  </span>
                </button>
              ))}
              {!visible.accounts.length && (
                <p className="flow-empty-column">没有匹配账号</p>
              )}
            </div>
          </section>
          <section className="flow-column" aria-label="角色与人格">
            <h3>
              <span>02</span>
              <UserRound aria-hidden="true" />
              角色 / 人格 <small>{visible.roles.length}</small>
            </h3>
            <div className="flow-column-body">
              {visible.roles.map((entry) => (
                <button
                  type="button"
                  key={entry.id}
                  data-flow-node={entry.id}
                  aria-pressed={selected === entry.id}
                  className={`flow-node flow-role ${dim(entry.id)}`}
                  onClick={() => select(entry.id)}
                >
                  <strong>{entry.label}</strong>
                  <small>{stateLabel(entry.state)}</small>
                  <div className="flow-persona">
                    <strong>人格</strong>
                    <span>
                      {entry.profile_id ?? "未提供人格投影"}
                      {entry.profile_version
                        ? ` · v${entry.profile_version}`
                        : ""}
                    </span>
                  </div>
                  <span>
                    {
                      data.conversations.filter((item) =>
                        item.actor_ids.includes(entry.id),
                      ).length
                    }{" "}
                    个关联会话
                  </span>
                </button>
              ))}
              {visible.conversations.some((item) => !item.actor_ids.length) && (
                <button
                  type="button"
                  data-flow-node="unbound"
                  aria-pressed={selected === "unbound"}
                  className={`flow-node flow-unbound ${dim("unbound")}`}
                  onClick={() => select("unbound")}
                >
                  <strong>尚未绑定角色</strong>
                  <span>
                    {
                      visible.conversations.filter(
                        (item) => !item.actor_ids.length,
                      ).length
                    }{" "}
                    个会话
                  </span>
                </button>
              )}
              {!visible.roles.length &&
                !visible.conversations.some(
                  (item) => !item.actor_ids.length,
                ) && <p className="flow-empty-column">没有匹配角色</p>}
            </div>
          </section>
          <section className="flow-column" aria-label="会话窗口">
            <h3>
              <span>03</span>
              <MessagesSquare aria-hidden="true" />
              会话窗口 <small>{visible.conversations.length}</small>
            </h3>
            <div className="flow-column-body">
              {visible.conversations.map((entry) => (
                <button
                  type="button"
                  key={entry.id}
                  data-flow-node={entry.id}
                  aria-pressed={selected === entry.id}
                  className={`flow-node flow-window ${dim(entry.id)}`}
                  onClick={() => select(entry.id)}
                >
                  <strong>{entry.label}</strong>
                  <span>
                    <span className="flow-tag">
                      {entry.kind === "group" ? "群聊" : "私聊"}
                    </span>{" "}
                    {bindingLabels[entry.binding]}
                  </span>
                  <small>
                    {entry.kind === "private"
                      ? !entry.observe
                        ? "未观察"
                        : entry.reply_permitted
                          ? "允许触发回复"
                          : "仅观察，不回复"
                      : entry.mode === "active"
                        ? stateLabel("active_mode")
                        : stateLabel(entry.mode)}{" "}
                    · {stateLabel(entry.state)}
                  </small>
                  <span>
                    {entry.actor_ids.length
                      ? entry.actor_ids
                          .map(
                            (id) =>
                              data.roles.find((item) => item.id === id)
                                ?.label ?? id,
                          )
                          .join("、")
                      : "无角色映射"}
                  </span>
                </button>
              ))}
              {!visible.conversations.length && (
                <p className="flow-empty-column">没有匹配会话</p>
              )}
            </div>
          </section>
        </GraphBoard>
      )}
      <div className="flow-legend">
        <span className="flow-line-sample" />
        登记或单独映射
        <span className="flow-line-sample is-dashed" />
        继承或未绑定<span>点击卡片查看相关联系；就绪不代表当前在线。</span>
      </div>
      <section
        className="flow-details"
        aria-label="关系详情"
        aria-live="polite"
      >
        <h3>
          {conversation?.label ??
            role?.label ??
            selectedAccount?.label ??
            (selected === "unbound" ? "未绑定会话" : "选择一个节点")}
        </h3>
        {conversation ? (
          <>
            <p>
              {bindingLabels[conversation.binding]} · 来源：
              {conversation.source} · {conversation.conversation_id}
            </p>
            {conversation.edit ? (
              <div className="flow-mapping-editor">
                <label>
                  该群角色
                  <select
                    aria-label="该群角色"
                    value={draftActor}
                    disabled={saving || !conversation.edit.unlocked}
                    onChange={(event) => setDraftActor(event.target.value)}
                  >
                    <option value="">不绑定</option>
                    {data.roles.map((entry) => (
                      <option
                        value={entry.id}
                        key={entry.id}
                        disabled={!entry.assignable}
                      >
                        {entry.label}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="button button-primary"
                  disabled={
                    saving ||
                    !conversation.edit.unlocked ||
                    draftActor === (conversation.actor_ids[0] ?? "")
                  }
                  onClick={() => void save(conversation)}
                >
                  {saving ? "保存中" : "保存角色映射"}
                </button>
                {!conversation.edit.unlocked && (
                  <p>
                    保存沿用机器人管理权限。
                    <a href="#/settings/channels">前往原接入页面解锁</a>
                  </p>
                )}
              </div>
            ) : (
              <p>
                此类绑定由原接入页面管理。
                <a href="#/settings/channels">查看机器人接入</a>
              </p>
            )}
          </>
        ) : role ? (
          <>
            <p>角色：{role.id}</p>
            <p>
              已登记能力：
              {role.capabilities.length
                ? role.capabilities.join("、")
                : "未提供能力投影"}
            </p>
            <a href="#/roles">打开角色管理</a>
          </>
        ) : selectedAccount ? (
          <>
            <p>
              {selectedAccount.adapter} · {selectedAccount.account_id} ·{" "}
              {selectedAccount.states.map(stateLabel).join(" / ")}
            </p>
            {data.defaults
              .filter((entry) => entry.account_id === selectedAccount.id)
              .map((entry) => (
                <p key={entry.kind}>
                  {entry.kind === "group" ? "群聊" : "私聊"}默认：
                  {stateLabel(entry.mode)} ·{" "}
                  {data.roles.find((item) => item.id === entry.actor_id)
                    ?.label ??
                    entry.actor_id ??
                    "未绑定"}
                </p>
              ))}
          </>
        ) : (
          <p>
            实线表示已登记关系，虚线表示继承或缺少角色。图中不推断在线状态或消息已送达。
          </p>
        )}
        {saveError && (
          <p role="alert" className="inline-error">
            {saveError}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
      </section>
      <p className="flow-stamp">配置读取于 {formatTime(data.generated_at)}</p>
    </div>
  );
}
