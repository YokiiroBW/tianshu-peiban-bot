import { PASSWORD_MIN_LENGTH } from "../../app/passwordPolicy";
import { LoginLink, useSessionGuard } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, ShieldCheck, Unlock } from "lucide-react";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import {
  acceptanceLabels,
  availabilityLabels,
  call,
  observationLabels,
  readingText,
  session as readSession,
  shortTime,
  WebError,
  type ActionTemplate,
  type ControlResult,
  type Entity,
  type HomeView,
  type SessionState,
} from "./api";
import "./home.css";

const kinds: Record<string, string> = {
  light: "灯",
  switch: "开关",
  sensor: "传感器（只读）",
};

const connectionCode: Record<string, string> = {
  ready: "控制已解锁",
  control_required: "控制待验证",
  control_disabled: "仅读取，未开启控制",
  operator_not_authorized: "没有设备控制权限",
  home_disabled: "未登记家庭设备连接",
};

/** Unknown availability is never rendered as healthy, closed or zero. */
function rail(entity: Entity) {
  return (
    availabilityLabels[entity.availability] ?? {
      tone: "gray" as const,
      label: "未知",
      detail: "这个状态无法解释。",
    }
  );
}

function reason(cause: unknown) {
  return cause instanceof Error ? cause.message : "连接中断，请重新连接。";
}

export default function HomePage() {
  const [session, setSession] = useState<SessionState | null>(null);
  useSessionGuard(session);
  const [view, setView] = useState<HomeView | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState("");
  const [absent, setAbsent] = useState("");
  const active = useRef<AbortController | null>(null);
  const controlling = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  const start = useCallback(() => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    return controller;
  }, []);

  /** A read is a real observation round; without one the page only shows the last known one. */
  const observe = useCallback(
    async (state: SessionState, signal: AbortSignal) =>
      call<HomeView>("home/refresh", {}, state.csrf, signal),
    [],
  );

  const load = useCallback(
    async (signal: AbortSignal, read: boolean, retry = true) => {
      setBusy(true);
      try {
        const state = await readSession(signal);
        setSession(state);
        if (!state.authenticated) {
          setView(null);
          return;
        }
        try {
          const result = read
            ? await observe(state, signal)
            : await call<HomeView>("home/view", {}, state.csrf, signal);
          setView(result);
          setAbsent("");
        } catch (cause) {
          setView(null);
          if (cause instanceof WebError && cause.code === "home_disabled") {
            setAbsent(cause.message);
            return;
          }
          // A revoked or expired login is a state, not a stale panel: re-read once, then show it.
          if (
            retry &&
            cause instanceof WebError &&
            ["session_expired", "unauthorized"].includes(cause.code)
          ) {
            await load(signal, read, false);
            setError(cause.message);
            return;
          }
          throw cause;
        }
      } catch (cause) {
        if (!signal.aborted) setError(reason(cause));
      } finally {
        if (!signal.aborted) setBusy(false);
      }
    },
    [observe],
  );

  useEffect(() => {
    const controller = start();
    void load(controller.signal, true);
    return () => {
      controller.abort();
      controlling.current?.abort();
    };
  }, [load, start]);

  /** Re-read the registered entities from Home Assistant. */
  const readAgain = useCallback(async () => {
    if (!session) return;
    const controller = start();
    setBusy(true);
    setError("");
    try {
      setView(await observe(session, controller.signal));
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (
        cause instanceof WebError &&
        ["session_expired", "unauthorized"].includes(cause.code)
      ) {
        setError(cause.message);
        await load(controller.signal, true);
      } else {
        setError(reason(cause));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }, [load, observe, session]);

  /** The local projection: shows what is already recorded, without contacting a device. */
  const reread = useCallback(async () => {
    if (!session) return;
    try {
      setView(
        await call<HomeView>(
          "home/view",
          {},
          session.csrf,
          new AbortController().signal,
        ),
      );
    } catch (cause) {
      setError(reason(cause));
    }
  }, [session]);

  const unlock = useCallback(
    async (form: HTMLFormElement) => {
      if (!session) return;
      const data = new FormData(form);
      const controller = start();
      setBusy(true);
      setError("");
      setNotice("");
      try {
        const result = await call<{ code: string; expires_in: number }>(
          "home/unlock",
          { password: String(data.get("password")) },
          session.csrf,
          controller.signal,
        );
        form.reset();
        setNotice(
          `设备控制已解锁，${Math.round(result.expires_in / 60)} 分钟内有效。`,
        );
        await reread();
      } catch (cause) {
        if (!controller.signal.aborted) setError(reason(cause));
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    },
    [reread, session, start],
  );

  const lock = useCallback(async () => {
    if (!session) return;
    const controller = start();
    setBusy(true);
    setError("");
    setNotice("设备控制已锁定。");
    try {
      await call("home/lock", {}, session.csrf, controller.signal);
      await reread();
    } catch (cause) {
      if (!controller.signal.aborted) setError(reason(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }, [reread, session, start]);

  /** One registered template at the revision this browser actually saw. */
  const control = useCallback(
    async (template: ActionTemplate, entity: Entity) => {
      if (!session) return;
      const controller = new AbortController();
      controlling.current?.abort();
      controlling.current = controller;
      setPending(template.template_id);
      setError("");
      setNotice("");
      try {
        const result = await call<ControlResult>(
          "home/control",
          {
            template_id: template.template_id,
            expected_revision: entity.revision,
            client_request_id: requestId(),
          },
          session.csrf,
          controller.signal,
        );
        setNotice(
          `${result.label}：${acceptanceLabels[result.acceptance] ?? result.acceptance}。${
            result.durable ? "" : "本次回执未能写入本地账本。"
          }`,
        );
        await reread();
      } catch (cause) {
        if (controller.signal.aborted) {
          // Cancelling only stops this page from waiting; it never claims the command failed.
          setNotice(
            "已取消等待。指令是否送达尚未确认，不会自动重发；请重新读取设备状态。",
          );
          await reread();
          return;
        }
        const message = reason(cause);
        setError(message);
        if (cause instanceof WebError && cause.code === "state_conflict") {
          // The reading this page held is no longer current: only a real read can move on,
          // and the reason the read happened must stay visible afterwards.
          await readAgain();
          setError(message);
          setNotice("已按设备当前状态重新读取；请确认后再操作。");
        } else {
          await reread();
        }
      } finally {
        setPending("");
      }
    },
    [readAgain, reread, session],
  );

  const connector = view?.connector;
  const canControl = connector?.code === "ready";
  const observable = connector?.readable !== false;

  return (
    <section
      className="home-console glass"
      aria-label="家庭设备"
      aria-busy={busy}
    >
      <div className="home-toolbar">
        <div>
          <p className="eyebrow">同源 · 已登记连接</p>
          <h2 ref={heading} tabIndex={-1}>
            家里现在怎样
          </h2>
        </div>
        <div className="home-actions">
          <button
            className="button"
            onClick={() => void readAgain()}
            disabled={busy || !session?.authenticated || !observable}
          >
            <RefreshCw aria-hidden="true" />
            重新读取设备状态
          </button>
          {pending && (
            <button
              className="button"
              onClick={() => controlling.current?.abort()}
            >
              取消等待
            </button>
          )}
        </div>
      </div>
      <div role="status" className="home-status">
        {busy
          ? "正在连接…"
          : session?.authenticated
            ? `已登录 · ${session.username}${
                connector
                  ? ` · ${connectionCode[connector.code] ?? connector.code}`
                  : ""
              }`
            : "请登录家庭管理员账号"}
      </div>
      {error && (
        <p className="home-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="home-notice" role="status">
          {notice}
        </p>
      )}
      {absent ? (
        <StatePanel kind="unconfigured" title="这个部署没有登记家庭设备连接">
          <p>
            登记 Home Assistant 地址、实体和动作模板后，这里才会显示设备状态。
          </p>
        </StatePanel>
      ) : !session?.authenticated ? (
        <LoginLink />
      ) : (
        <>
          {connector && (
            <div className="home-connector">
              <StatusRail
                tone={
                  connector.code === "ready"
                    ? "blue"
                    : connector.code === "control_required"
                      ? "yellow"
                      : "gray"
                }
                label={connectionCode[connector.code] ?? connector.code}
              />
              <dl className="home-facts">
                <div>
                  <dt>登记实体</dt>
                  <dd>
                    {connector.entities} 个 · {connector.templates} 个动作模板
                  </dd>
                </div>
                <div>
                  <dt>读数有效期</dt>
                  <dd>超过 {connector.stale_after_seconds} 秒标为过期</dd>
                </div>
                <div>
                  <dt>单次请求上限</dt>
                  <dd>{connector.timeout_seconds} 秒</dd>
                </div>
              </dl>
              {connector.control_available && !connector.unlocked && (
                <form
                  className="home-unlock"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void unlock(event.currentTarget);
                  }}
                >
                  <label>
                    管理员密码
                    <input
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      minLength={PASSWORD_MIN_LENGTH}
                      maxLength={256}
                      required
                      disabled={busy}
                    />
                  </label>
                  <button
                    className="button primary"
                    type="submit"
                    disabled={busy}
                  >
                    <Unlock aria-hidden="true" />
                    解锁设备控制
                  </button>
                </form>
              )}
              {canControl && (
                <button
                  className="button"
                  onClick={() => void lock()}
                  disabled={busy}
                >
                  <ShieldCheck aria-hidden="true" />
                  锁定控制
                </button>
              )}
              {!connector.control_available && (
                <p className="muted">
                  {connector.code === "control_disabled"
                    ? "这个部署只读取状态：不会向 Home Assistant 发出任何服务调用。"
                    : "当前账号只读取状态，控制按钮不会出现。"}
                </p>
              )}
            </div>
          )}
          <h3 className="home-heading">登记实体</h3>
          <div className="home-entities">
            {view?.entities.map((entity) => {
              const state = rail(entity);
              return (
                <article className="home-entity" key={entity.entity_id}>
                  <div className="home-entity-head">
                    <div>
                      <h3>{entity.label}</h3>
                      <p className="muted">
                        {entity.entity_id} · {kinds[entity.kind] ?? entity.kind}
                      </p>
                    </div>
                    <StatusRail tone={state.tone} label={state.label} />
                  </div>
                  <dl className="home-facts">
                    <div>
                      <dt>读数</dt>
                      <dd data-field="reading">{readingText(entity)}</dd>
                    </div>
                    <div>
                      <dt>采样时间</dt>
                      <dd data-field="observed">
                        {shortTime(entity.observed_at)}
                      </dd>
                    </div>
                    <div>
                      <dt>可用性</dt>
                      <dd>{state.detail}</dd>
                    </div>
                  </dl>
                  {entity.control ? (
                    canControl ? (
                      entity.templates.length ? (
                        <div className="home-entity-actions">
                          {entity.templates.map((template) => (
                            <button
                              className="button"
                              key={template.template_id}
                              disabled={Boolean(pending)}
                              onClick={() => void control(template, entity)}
                            >
                              {template.label}
                            </button>
                          ))}
                        </div>
                      ) : (
                        <p className="muted">这个设备没有登记动作模板。</p>
                      )
                    ) : (
                      <p className="muted">
                        {connector?.code === "control_disabled"
                          ? "这个部署只读取状态，不显示开关动作。"
                          : connector?.code === "operator_not_authorized"
                            ? "当前账号没有设备控制权限，不显示开关动作。"
                            : "设备控制未解锁：验证管理员密码后才显示动作按钮。"}
                      </p>
                    )
                  ) : (
                    <p className="muted">
                      只读传感器：不提供开关动作，也不会被写入。
                    </p>
                  )}
                </article>
              );
            })}
          </div>
          <h3 className="home-heading">最近操作</h3>
          {view?.controls.length ? (
            <ul className="home-controls">
              {view.controls.map((entry) => (
                <li key={entry.control_id}>
                  <div className="home-control-head">
                    <strong>{entry.label}</strong>
                    <StatusRail
                      tone={
                        entry.acceptance === "accepted" ||
                        entry.acceptance === "observed"
                          ? entry.observation.status === "contradicted"
                            ? "red"
                            : "yellow"
                          : entry.acceptance === "rejected"
                            ? "gray"
                            : "gray"
                      }
                      label={
                        acceptanceLabels[entry.acceptance] ?? entry.acceptance
                      }
                    />
                  </div>
                  <p className="muted">
                    {entry.entity_id} · 目标{" "}
                    {entry.requested_state === "on" ? "开启" : "关闭"} ·{" "}
                    {shortTime(entry.recorded_at)}
                  </p>
                  <p>
                    回执：
                    {entry.target_reported
                      ? "Home Assistant 报告目标状态"
                      : "未包含目标状态"}
                    ； 观测：
                    {observationLabels[entry.observation.status] ??
                      entry.observation.status}
                    {entry.observation.observed_at
                      ? `（${shortTime(entry.observation.observed_at)}）`
                      : ""}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">还没有发出过设备动作。</p>
          )}
          <p className="muted">
            这里只调用已登记设备的明确开关动作；受理回执不等于设备已执行，只有后续观测才
            表示实际状态。不提供门锁、门、报警或脚本动作。
          </p>
        </>
      )}
    </section>
  );
}
