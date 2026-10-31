import { PASSWORD_MIN_LENGTH } from "../../app/passwordPolicy";
import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { integrationPost } from "../../app/integrationApi";
import { StatusRail } from "../../components/StatusRail";
import {
  AdapterApiError,
  adapterErrorMessage,
  adapterLabel,
  adapterPost,
  type AdapterConnection,
  type AdapterKind,
  type AdapterProbe,
  type AdapterView,
} from "./botAdapterApi";
import { BotConnectionsPanel as LegacyBotConnectionsPanel } from "./BotConnectionsPanel";
import { BotObservationPanel } from "./BotObservationPanel";
import "./bots.css";

const protocol = "tianshu.bot-adapter/v1";
type Receipt = { connection: AdapterConnection };

function formatTime(value: string | null) {
  if (!value) return "尚无";
  const time = Date.parse(value);
  return Number.isFinite(time)
    ? new Date(time).toLocaleString("zh-CN", { hour12: false })
    : "时间未知";
}

function rail(connection: AdapterConnection, unverified: boolean) {
  if (unverified || connection.state === "unknown")
    return { tone: "yellow" as const, label: "结果待核对" };
  if (connection.state === "draft")
    return { tone: "yellow" as const, label: "保存待确认" };
  if (!connection.enabled)
    return { tone: "gray" as const, label: "已保存 · 未启用" };
  if (connection.state === "ready")
    return { tone: "blue" as const, label: "已启用" };
  if (connection.state === "degraded")
    return { tone: "red" as const, label: "连接异常" };
  return { tone: "yellow" as const, label: "等待后台确认" };
}

function authorsFrom(value: string) {
  return [
    ...new Set(
      value
        .split(/[\s,，;；]+/)
        .map((id) => id.trim())
        .filter(Boolean),
    ),
  ];
}

function uncertain(cause: unknown) {
  return (
    !(cause instanceof AdapterApiError) ||
    cause.code === "result_unknown" ||
    cause.executionState === "unknown" ||
    cause.status >= 500
  );
}

function settledTogether(
  id: string,
  revision: number,
  receipt: AdapterConnection | undefined,
  readback: AdapterConnection | undefined,
) {
  if (
    !receipt ||
    !readback ||
    receipt.id !== id ||
    readback.id !== id ||
    receipt.revision !== revision ||
    receipt.revision !== readback.revision ||
    receipt.enabled !== readback.enabled ||
    receipt.state !== readback.state
  )
    return false;
  return (
    (receipt.state === "ready" && receipt.enabled) ||
    (receipt.state === "disabled" && !receipt.enabled)
  );
}

function message(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : "连接中断，请刷新后台状态后核对。";
}

export function BotAdapterPanel() {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const active = useRef<AbortController | null>(null);
  const [view, setView] = useState<AdapterView | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [adapter, setAdapter] = useState<AdapterKind>("astrbot");
  const [address, setAddress] = useState("");
  const [accessKey, setAccessKey] = useState("");
  const [privateHttp, setPrivateHttp] = useState(false);
  const [caPem, setCaPem] = useState("");
  const [probe, setProbe] = useState<AdapterProbe | null>(null);
  const [expiryTick, setExpiryTick] = useState(0);
  const [accountId, setAccountId] = useState("");
  const [name, setName] = useState("");
  const [actorId, setActorId] = useState("");
  const [conversationKind, setConversationKind] = useState<"group" | "private">(
    "group",
  );
  const [conversationId, setConversationId] = useState("");
  const [authors, setAuthors] = useState("");
  const [unverifiedIds, setUnverifiedIds] = useState<Set<string>>(
    () => new Set(),
  );

  function markUnverified(id: string | undefined) {
    if (id) setUnverifiedIds((before) => new Set(before).add(id));
  }

  function clearUnverified(id: string) {
    setUnverifiedIds((before) => {
      const next = new Set(before);
      next.delete(id);
      return next;
    });
  }

  function start(operation: string) {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(operation);
    setError("");
    setNotice("");
    return controller;
  }

  async function readView(controller: AbortController) {
    const result = await adapterPost<AdapterView>(
      "view",
      {},
      csrf,
      controller.signal,
    );
    if (
      typeof result.available !== "boolean" ||
      typeof result.unlocked !== "boolean" ||
      !Array.isArray(result.actors) ||
      !Array.isArray(result.connections)
    )
      throw new Error("机器人管理接口返回不完整，请联系部署管理员。");
    if (!controller.signal.aborted) {
      setView(result);
      setUnavailable(!result.available);
      if (!result.unlocked) {
        setProbe(null);
        setAccessKey("");
        setCaPem("");
      }
    }
    return result;
  }

  async function refresh() {
    if (!csrf) return;
    const controller = start("refresh");
    try {
      await readView(controller);
      if (!controller.signal.aborted) setUnverifiedIds(new Set());
    } catch (cause) {
      if (controller.signal.aborted) return;
      setView(null);
      setUnavailable(
        cause instanceof AdapterApiError &&
          [
            "web_not_configured",
            "management_disabled",
            "not_configured",
            "not_found",
          ].includes(cause.code),
      );
      setError(message(cause));
    } finally {
      if (!controller.signal.aborted) setBusy("");
    }
  }

  useEffect(() => {
    void refresh();
    return () => active.current?.abort();
  }, [csrf]);

  useEffect(() => {
    if (!probe) return;
    const delay = Date.parse(probe.expires_at) - Date.now();
    if (delay <= 0) return;
    const timer = window.setTimeout(
      () => setExpiryTick((tick) => tick + 1),
      delay,
    );
    return () => window.clearTimeout(timer);
  }, [probe, expiryTick]);

  function invalidateProbe() {
    setProbe(null);
    setAccountId("");
    setError("");
    setNotice("");
  }

  function requireUnlock(cause: unknown) {
    if (
      !(cause instanceof AdapterApiError) ||
      cause.code !== "management_required"
    )
      return;
    setView((before) => (before ? { ...before, unlocked: false } : before));
    setOpen(false);
    setPassword("");
    setAddress("");
    setAccessKey("");
    setCaPem("");
    setPrivateHttp(false);
    setProbe(null);
    setAccountId("");
    setName("");
    setActorId("");
    setConversationId("");
    setAuthors("");
    setConversationKind("group");
  }

  function cancel() {
    const pendingWrite = busy === "create";
    active.current?.abort();
    setBusy("");
    setOpen(false);
    setAddress("");
    setAccessKey("");
    setCaPem("");
    setPrivateHttp(false);
    setProbe(null);
    setAccountId("");
    setName("");
    setActorId("");
    setConversationId("");
    setAuthors("");
    setConversationKind("group");
    setError("");
    setNotice(
      pendingWrite
        ? "已停止等待保存回执；请求可能已经提交。请刷新列表核对，不要立即重复提交。"
        : "",
    );
  }

  async function unlock(event: FormEvent) {
    event.preventDefault();
    if (!password) return;
    const controller = start("unlock");
    try {
      await integrationPost(
        "bots/unlock",
        { password },
        csrf,
        controller.signal,
      );
      setPassword("");
      const next = await readView(controller);
      if (!controller.signal.aborted && !next.unlocked)
        setError("管理员解锁未生效，请刷新后检查权限。");
    } catch (cause) {
      if (!controller.signal.aborted) {
        requireUnlock(cause);
        setError(message(cause));
      }
    } finally {
      setPassword("");
      if (!controller.signal.aborted) setBusy("");
    }
  }

  async function detect(event: FormEvent) {
    event.preventDefault();
    if (!view?.available || !view.unlocked) return;
    let url: URL;
    try {
      url = new URL(address.trim());
    } catch {
      setError("请输入完整的插件地址，例如 https://bot.example.com:8080。");
      return;
    }
    if (
      !["https:", "http:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      setError(
        "插件地址只能使用 HTTP(S)，且不能包含账号、密码、查询参数或片段。",
      );
      return;
    }
    if (url.protocol === "http:" && !privateHttp) {
      setError("局域网 HTTP 需要显式勾选；公共地址必须使用 HTTPS。");
      return;
    }
    if (!accessKey.trim()) {
      setError("请填写插件连接密钥。");
      return;
    }
    const key = accessKey;
    const ca = caPem.trim();
    setAccessKey("");
    setCaPem("");
    setProbe(null);
    setAccountId("");
    const controller = start("probe");
    try {
      const result = await adapterPost<AdapterProbe>(
        "probe",
        {
          adapter,
          address: address.trim(),
          access_key: key,
          allow_private_http: privateHttp,
          ca_pem: ca || null,
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (result.protocol !== protocol) {
        setError("插件协议版本不兼容，请更新对应的天枢插件。");
        return;
      }
      if (
        !result.draft_id ||
        !Number.isFinite(Date.parse(result.expires_at)) ||
        Date.parse(result.expires_at) <= Date.now() ||
        !Array.isArray(result.accounts)
      ) {
        setError("检测回执不完整或已过期，请重新检测连接。");
        return;
      }
      const qqAccounts = result.accounts.filter(
        (account) => account.platform === "qq" && Boolean(account.id),
      );
      setProbe({ ...result, accounts: qqAccounts });
      setAccountId(qqAccounts[0]?.id ?? "");
      setNotice(
        qqAccounts.length
          ? "插件检测通过，请选择实际在线账号并填写允许范围。保存后仍需单独启用。"
          : "插件已响应，但 SDK 未提供在线机器人账号。请在宿主中登录机器人后重新检测。",
      );
    } catch (cause) {
      if (!controller.signal.aborted) {
        requireUnlock(cause);
        setError(message(cause));
      }
    } finally {
      if (!controller.signal.aborted) setBusy("");
    }
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!probe || !view?.unlocked || busy) return;
    if (Date.parse(probe.expires_at) <= Date.now()) {
      setError("检测草稿已过期，请重新检测连接。");
      return;
    }
    if (!probe.accounts.some((account) => account.id === accountId)) {
      setError("请从插件实际返回的账号中选择一个。");
      return;
    }
    const allow =
      conversationKind === "private"
        ? [conversationId.trim()]
        : authorsFrom(authors);
    if (name.trim().length > 64) {
      setError("连接名称最多 64 个字符。");
      return;
    }
    if (!name.trim() || !actorId || !conversationId.trim() || !allow.length) {
      setError("请填写连接名称、角色、会话 ID 和明确允许的作者 ID。");
      return;
    }
    const controller = start("create");
    let submitted = false;
    try {
      const result = await adapterPost<Receipt>(
        "create",
        {
          draft_id: probe.draft_id,
          name: name.trim(),
          account_id: accountId,
          conversation: { kind: conversationKind, id: conversationId.trim() },
          allowed_authors: allow,
          actor_id: actorId,
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      submitted = true;
      const latest = await readView(controller);
      if (controller.signal.aborted) return;
      const confirmed = latest.connections.find(
        (item) => item.id === result.connection?.id,
      );
      if (
        latest.unlocked &&
        result.connection?.enabled === false &&
        result.connection.state === "disabled" &&
        confirmed?.enabled === false &&
        confirmed.state === "disabled" &&
        confirmed.revision === result.connection.revision
      ) {
        clearUnverified(result.connection.id);
        setNotice("连接已保存且保持停用。核对允许范围后，再单独点击“启用”。");
        setProbe(null);
        setOpen(false);
      } else {
        markUnverified(result.connection?.id);
        setNotice(
          "保存回执与当前状态未能一致核对。请刷新连接列表，确认后再操作。",
        );
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        requireUnlock(cause);
        setError(
          submitted || uncertain(cause)
            ? `保存结果无法确认。请刷新列表核对，不要立即重复提交。${message(cause)}`
            : message(cause),
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy("");
    }
  }

  async function change(
    connection: AdapterConnection,
    operation: "enable" | "disable",
  ) {
    if (
      !view?.unlocked ||
      busy ||
      unverifiedIds.has(connection.id) ||
      connection.state === "unknown" ||
      connection.state === "draft"
    )
      return;
    const controller = start(operation);
    let submitted = false;
    try {
      const result = await adapterPost<Receipt>(
        operation,
        {
          id: connection.id,
          expected_revision: connection.revision,
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      submitted = true;
      const latest = await readView(controller);
      if (controller.signal.aborted) return;
      const confirmed = latest.connections.find(
        (item) => item.id === connection.id,
      );
      const verified = Boolean(
        latest.unlocked &&
        confirmed &&
        confirmed.revision === result.connection?.revision &&
        confirmed.enabled === (operation === "enable") &&
        result.connection.enabled === confirmed.enabled &&
        confirmed.state === (operation === "enable" ? "ready" : "disabled") &&
        result.connection.state === confirmed.state,
      );
      if (verified) {
        clearUnverified(connection.id);
        setNotice(
          operation === "enable"
            ? "适配器已启用，插件与后台状态已确认。"
            : "适配器已停用，后台状态已确认。",
        );
      } else {
        markUnverified(connection.id);
        setNotice(
          "操作回执与当前状态尚未一致确认。请刷新状态并核对，勿直接重复操作。",
        );
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (submitted || uncertain(cause)) markUnverified(connection.id);
        requireUnlock(cause);
        setError(
          submitted || uncertain(cause)
            ? `操作结果无法确认。请刷新状态核对，不要立即重复提交。${message(cause)}`
            : message(cause),
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy("");
    }
  }

  async function reconcile(connection: AdapterConnection) {
    if (
      !view?.unlocked ||
      busy ||
      unverifiedIds.has(connection.id) ||
      connection.state !== "unknown"
    )
      return;
    const controller = start("reconcile");
    let submitted = false;
    try {
      const result = await adapterPost<Receipt>(
        "reconcile",
        {
          id: connection.id,
          expected_revision: connection.revision,
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      submitted = true;
      const latest = await readView(controller);
      if (controller.signal.aborted) return;
      const confirmed = latest.connections.find(
        (item) => item.id === connection.id,
      );
      if (
        latest.unlocked &&
        settledTogether(
          connection.id,
          connection.revision,
          result.connection,
          confirmed,
        )
      ) {
        clearUnverified(connection.id);
        setNotice(
          confirmed?.state === "ready"
            ? "上一次配置已恢复，适配器已启用。"
            : "上一次配置已核对，适配器已停用。",
        );
      } else {
        markUnverified(connection.id);
        setNotice("恢复结果仍待核对。请刷新状态后检查，不要立即重复提交。");
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        markUnverified(connection.id);
        requireUnlock(cause);
        setError(
          submitted || uncertain(cause)
            ? `恢复结果无法确认。请刷新状态核对，不要立即重复提交。${message(cause)}`
            : message(cause),
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy("");
    }
  }

  const expired = probe ? Date.parse(probe.expires_at) <= Date.now() : false;
  return (
    <div className="bot-adapter-page">
      <section className="panel bot-adapter" aria-label="机器人适配器管理">
        <div className="section-heading">
          <h2>机器人接入</h2>
          <button
            className="button"
            type="button"
            disabled={Boolean(busy)}
            onClick={() => void refresh()}
          >
            刷新状态
          </button>
        </div>
        <p className="muted">
          创建机器人后，将 WS 地址和 Token 填入 SnowLuma
          等客户端；连接成功后自动关联账号。
        </p>
        {busy === "refresh" && !view && (
          <p role="status">正在读取适配器状态…</p>
        )}
        {error && (
          <p className="bot-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="bot-notice" role="status">
            {notice}
          </p>
        )}
        {unavailable && (
          <p role="status">
            当前部署尚未启用网页适配器管理，请由部署管理员启用后台能力后刷新。本页不会保存未生效的表单。
          </p>
        )}
        {view?.available && !view.unlocked && (
          <form className="bot-form" onSubmit={(event) => void unlock(event)}>
            <label>
              管理员密码（二次验证）
              <input
                type="password"
                autoComplete="current-password"
                minLength={PASSWORD_MIN_LENGTH}
                maxLength={256}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <button className="button" disabled={Boolean(busy)}>
              解锁连接管理
            </button>
          </form>
        )}
        <BotObservationPanel
          csrf={csrf}
          unlocked={Boolean(view?.unlocked)}
          actors={view?.actors ?? []}
        />
        <DetailCard
          title="高级兼容：精确范围适配器"
          className="panel bot-legacy"
        >
          <p className="muted">
            仅在需要按会话、作者和角色逐项绑定时使用。新增观察账号请使用上方主入口。
          </p>
          {view?.available && view.unlocked && (
            <>
              <div className="bot-adapter-actions">
                <p className="muted">
                  管理已解锁。密钥由后台安全保存供后续连接使用；页面提交后清除且不回显。
                </p>
                {!open && (
                  <button
                    className="button"
                    type="button"
                    disabled={Boolean(busy)}
                    onClick={() => {
                      setOpen(true);
                      setError("");
                      setNotice("");
                    }}
                  >
                    添加适配器
                  </button>
                )}
              </div>
              {open && (
                <div className="bot-wizard">
                  <div className="bot-wizard-heading">
                    <h3>添加适配器</h3>
                    <button className="button" type="button" onClick={cancel}>
                      取消
                    </button>
                  </div>
                  <ol className="bot-wizard-steps" aria-label="添加步骤">
                    <li>选择平台并检测</li>
                    <li>选择账号与范围</li>
                    <li>保存后启用</li>
                  </ol>
                  <form
                    className="bot-form"
                    onSubmit={(event) => void detect(event)}
                  >
                    <label>
                      机器人平台
                      <select
                        value={adapter}
                        disabled={Boolean(busy)}
                        onChange={(event) => {
                          setAdapter(event.target.value as AdapterKind);
                          invalidateProbe();
                        }}
                      >
                        <option value="astrbot">AstrBot</option>
                        <option value="nonebot">NoneBot</option>
                        <option value="onebot">OneBot / NapCat</option>
                      </select>
                    </label>
                    <label>
                      插件地址
                      <input
                        type="url"
                        required
                        placeholder="https://bot.example.com:8080"
                        value={address}
                        disabled={Boolean(busy)}
                        onChange={(event) => {
                          setAddress(event.target.value);
                          invalidateProbe();
                        }}
                      />
                    </label>
                    <p className="muted">
                      {adapter === "onebot"
                        ? "填写天枢 OneBot 接入服务的 HTTP 地址。NapCat 通过反向 WebSocket 连接该服务；这里不填 NapCat 管理页面地址。"
                        : "填写插件监听地址即可，协议路径会自动补全。"}
                    </p>
                    <label>
                      插件连接密钥
                      <input
                        type="password"
                        autoComplete="off"
                        required
                        value={accessKey}
                        disabled={Boolean(busy)}
                        onChange={(event) => {
                          setAccessKey(event.target.value);
                          invalidateProbe();
                        }}
                      />
                    </label>
                    <label className="bot-check">
                      <input
                        type="checkbox"
                        checked={privateHttp}
                        disabled={Boolean(busy)}
                        onChange={(event) => {
                          setPrivateHttp(event.target.checked);
                          invalidateProbe();
                        }}
                      />
                      允许局域网 HTTP（仅私有或本机地址）
                    </label>
                    <DetailCard title="高级连接选项" className="bot-advanced">
                      <p>
                        协议路径由天枢自动处理。HTTPS
                        始终验证证书；仅自签发证书需要填写可信 CA。
                      </p>
                      <label>
                        可信 CA 证书（可选，PEM）
                        <textarea
                          rows={5}
                          value={caPem}
                          disabled={Boolean(busy)}
                          onChange={(event) => {
                            setCaPem(event.target.value);
                            invalidateProbe();
                          }}
                        />
                      </label>
                    </DetailCard>
                    <button className="button" disabled={Boolean(busy)}>
                      检测连接并读取账号
                    </button>
                  </form>
                  {probe && (
                    <div className="bot-probe-result">
                      <h4>检测结果</h4>
                      <p>
                        {adapterLabel(adapter)} 插件已响应。检测草稿有效至{" "}
                        {formatTime(probe.expires_at)}。
                      </p>
                      {expired ? (
                        <p className="bot-error" role="alert">
                          草稿已过期，请重新填写密钥并检测。
                        </p>
                      ) : probe.accounts.length === 0 ? (
                        <p role="status">
                          SDK 当前没有在线 QQ
                          机器人账号。请先登录机器人，再重新检测。
                        </p>
                      ) : view.actors.length === 0 ? (
                        <p role="status">
                          当前没有可选角色，请先准备角色后刷新状态。
                        </p>
                      ) : (
                        <form
                          className="bot-form"
                          onSubmit={(event) => void create(event)}
                        >
                          <label>
                            连接名称
                            <input
                              required
                              maxLength={64}
                              value={name}
                              disabled={Boolean(busy)}
                              onChange={(event) => setName(event.target.value)}
                            />
                          </label>
                          <label>
                            在线机器人账号
                            <select
                              value={accountId}
                              disabled={Boolean(busy)}
                              onChange={(event) =>
                                setAccountId(event.target.value)
                              }
                            >
                              {probe.accounts.map((account) => (
                                <option key={account.id} value={account.id}>
                                  {account.label || account.id} · {account.id} ·{" "}
                                  {account.platform}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            回复角色
                            <select
                              value={actorId}
                              required
                              disabled={Boolean(busy)}
                              onChange={(event) =>
                                setActorId(event.target.value)
                              }
                            >
                              <option value="">请选择角色</option>
                              {view.actors.map((actor) => (
                                <option key={actor.id} value={actor.id}>
                                  {actor.label}
                                </option>
                              ))}
                            </select>
                          </label>
                          <label>
                            会话类型
                            <select
                              value={conversationKind}
                              disabled={Boolean(busy)}
                              onChange={(event) =>
                                setConversationKind(
                                  event.target.value as "group" | "private",
                                )
                              }
                            >
                              <option value="group">QQ群</option>
                              <option value="private">QQ私聊</option>
                            </select>
                          </label>
                          <label>
                            {conversationKind === "group"
                              ? "群 ID"
                              : "联系人 ID"}
                            <input
                              required
                              value={conversationId}
                              disabled={Boolean(busy)}
                              onChange={(event) =>
                                setConversationId(event.target.value)
                              }
                            />
                          </label>
                          {conversationKind === "group" ? (
                            <label>
                              明确允许的作者 ID
                              <textarea
                                required
                                rows={3}
                                value={authors}
                                disabled={Boolean(busy)}
                                onChange={(event) =>
                                  setAuthors(event.target.value)
                                }
                                aria-describedby="bot-authors-help"
                              />
                            </label>
                          ) : (
                            <label>
                              私聊允许作者（由联系人 ID 确定）
                              <input
                                readOnly
                                value={conversationId.trim()}
                                aria-describedby="bot-authors-help"
                              />
                            </label>
                          )}
                          <p id="bot-authors-help" className="muted">
                            {conversationKind === "group"
                              ? "用逗号或换行分隔，逐个填写允许的群成员 ID。未列出的作者不会触发回复。"
                              : "私聊只允许上方联系人触发回复，作者 ID 与联系人 ID 保持一致。"}
                          </p>
                          <button className="button" disabled={Boolean(busy)}>
                            保存为停用
                          </button>
                        </form>
                      )}
                    </div>
                  )}
                </div>
              )}
              <div className="bot-adapter-list-heading">
                <h3>已添加的适配器</h3>
                <p className="muted">
                  启用前请核对账号、会话与作者。检测连接不会发送消息。
                </p>
              </div>
              {view.connections.length === 0 ? (
                <p>尚无适配器连接。</p>
              ) : (
                <ul className="bot-list bot-adapter-list">
                  {view.connections.map((connection) => {
                    const pending = unverifiedIds.has(connection.id);
                    const status = rail(connection, pending);
                    return (
                      <li key={connection.id}>
                        <div>
                          <strong>{connection.name}</strong>
                          <p>
                            {adapterLabel(connection.adapter)} · 机器人{" "}
                            {connection.account_id} ·{" "}
                            {connection.conversation.kind === "group"
                              ? "群"
                              : "私聊"}{" "}
                            {connection.conversation.id}
                          </p>
                          <p>
                            角色：
                            {view.actors.find(
                              (actor) => actor.id === connection.actor_id,
                            )?.label ?? connection.actor_id}{" "}
                            · 允许作者：{connection.allowed_authors.join("、")}
                          </p>
                          <small>
                            最近检查：{formatTime(connection.last_checked_at)}
                            {connection.last_error
                              ? ` · ${adapterErrorMessage(connection.last_error)}`
                              : ""}
                          </small>
                          {connection.state === "unknown" && (
                            <p className="muted">
                              上一次配置操作尚未确认。“核对并恢复”会继续完成上一次操作；若上次是启用，恢复成功后将恢复正常消息处理。此操作不会发送测试消息。
                            </p>
                          )}
                          {pending && connection.state !== "unknown" && (
                            <p className="muted">
                              页面结果待核对，请先刷新状态。
                            </p>
                          )}
                        </div>
                        <StatusRail tone={status.tone} label={status.label} />
                        <div className="bot-actions">
                          <button
                            className="button"
                            type="button"
                            disabled={
                              Boolean(busy) ||
                              pending ||
                              connection.state === "unknown" ||
                              connection.state === "draft"
                            }
                            onClick={() =>
                              void change(
                                connection,
                                connection.enabled ? "disable" : "enable",
                              )
                            }
                          >
                            {connection.enabled ? "停用" : "启用"}
                          </button>
                          {connection.state === "unknown" && (
                            <button
                              className="button"
                              type="button"
                              disabled={Boolean(busy) || pending}
                              onClick={() => void reconcile(connection)}
                            >
                              核对并恢复
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </DetailCard>
      </section>
      <DetailCard
        title="旧连接管理（已配置的连接）"
        className="panel bot-legacy"
      >
        <p className="muted">
          仅管理此前由部署方登记的连接。新适配器连接不会重复出现在这里。
        </p>
        <LegacyBotConnectionsPanel />
      </DetailCard>
    </div>
  );
}
