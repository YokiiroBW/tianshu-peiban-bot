import { PASSWORD_MIN_LENGTH } from "../../app/passwordPolicy";
import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import {
  IntegrationError,
  integrationPost,
  readFailure,
} from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import "./external.css";

type Kind = "assets" | "home";
type CredentialAction = "keep" | "replace" | "clear";
type Entity = {
  entity_id: string;
  label: string;
  kind: "sensor" | "light" | "switch";
  unit?: string;
};
type LastTest = {
  state: "connected" | "unavailable" | "unauthorized";
  code: string;
  checked_at: string;
  revision: number;
} | null;
type Connection = {
  configured: boolean;
  enabled: boolean;
  url: string | null;
  credential_configured: boolean;
  ca_configured: boolean;
  last_test: LastTest;
  allow_private_http?: boolean;
  entities?: Entity[];
};
type View = {
  revision: number;
  unlocked: boolean;
  assets: Connection;
  home: Connection;
};
type TestResult = Exclude<LastTest, null> & { kind: Kind };
type Draft = {
  enabled: boolean;
  url: string;
  allowPrivateHttp: boolean;
  entities: Entity[];
  keyAction: CredentialAction;
  key: string;
  caAction: CredentialAction;
  caPem: string;
};

function draftOf(view: Connection): Draft {
  return {
    enabled: view.enabled,
    url: view.url ?? "",
    allowPrivateHttp:
      view.allow_private_http ?? Boolean(view.url?.startsWith("http://")),
    entities: view.entities ?? [],
    keyAction: view.credential_configured ? "keep" : "replace",
    key: "",
    caAction: "keep",
    caPem: "",
  };
}

function title(kind: Kind) {
  return kind === "assets" ? "AssetLink 资产连接" : "Home Assistant 家庭连接";
}

function testRail(result: LastTest, revision: number) {
  if (!result) return { tone: "yellow" as const, label: "尚未检测" };
  if (result.revision !== revision)
    return { tone: "yellow" as const, label: "检测已过期" };
  if (result.state === "connected")
    return { tone: "blue" as const, label: "已真实读取" };
  if (result.state === "unauthorized")
    return { tone: "red" as const, label: "对端未授权" };
  return { tone: "red" as const, label: "读取失败" };
}

/** Administrator-only external target setup. Internal Tianshu connections never ask for URLs. */
export function ExternalConnectionsPanel({ kind }: { kind: Kind }) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const active = useRef<AbortController | null>(null);

  function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function load(): Promise<View | null> {
    if (!csrf) return null;
    const controller = start();
    try {
      const next = await integrationPost<View>(
        "external/view",
        {},
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return null;
      setUnavailable(false);
      setView(next);
      setDraft(draftOf(next[kind]));
      return next;
    } catch (cause) {
      if (controller.signal.aborted) return null;
      setView(null);
      setDraft(null);
      setUnavailable(
        cause instanceof IntegrationError &&
          [
            "external_not_configured",
            "external_manage_required",
            "not_found",
          ].includes(cause.code),
      );
      setError(readFailure(cause));
      return null;
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    return () => active.current?.abort();
  }, [csrf, kind]);

  function update(change: Partial<Draft>) {
    setDraft((before) => (before ? { ...before, ...change } : before));
  }

  async function unlock(event: FormEvent) {
    event.preventDefault();
    if (!csrf || !password) return;
    const controller = start();
    setNotice("");
    try {
      await integrationPost(
        "external/unlock",
        { password },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setPassword("");
        const refreshed = await load();
        if (refreshed?.unlocked)
          setNotice("管理操作已解锁。仅当前登录窗口可用。");
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      setPassword("");
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function lock() {
    if (!csrf) return;
    const controller = start();
    try {
      await integrationPost("external/lock", {}, csrf, controller.signal);
      if (!controller.signal.aborted) {
        const refreshed = await load();
        if (refreshed && !refreshed.unlocked) setNotice("管理操作已锁定。");
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!csrf || !view || !draft || !view.unlocked) return;
    if (draft.keyAction === "replace" && !draft.key.trim()) {
      setError("请选择凭据替换后填写新凭据，或选择保留/清除。");
      return;
    }
    if (kind === "home" && draft.entities.length === 0) {
      setError("请至少登记一个要读取的家庭实体。");
      return;
    }
    if (draft.caAction === "replace" && !draft.caPem.trim()) {
      setError("选择替换 CA 后请粘贴 PEM，或选择保留/清除。");
      return;
    }
    const controller = start();
    setNotice("");
    const value =
      kind === "assets"
        ? { enabled: draft.enabled, endpoint: draft.url.trim() }
        : {
            enabled: draft.enabled,
            base_url: draft.url.trim(),
            allow_private_http: draft.allowPrivateHttp,
            entities: draft.entities.map((row) => ({
              entity_id: row.entity_id.trim(),
              label: row.label.trim(),
              kind: row.kind,
              ...(row.unit?.trim() ? { unit: row.unit.trim() } : {}),
            })),
          };
    try {
      const result = await integrationPost<{
        revision: number;
        state: string;
        applied: boolean;
      }>(
        "external/save",
        {
          kind,
          expected_revision: view.revision,
          client_request_id: requestId(),
          value,
          credential:
            draft.keyAction === "replace"
              ? { action: "replace", value: draft.key }
              : { action: draft.keyAction },
          ca:
            draft.caAction === "replace"
              ? { action: "replace", value: draft.caPem.trim() }
              : { action: draft.caAction },
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setDraft((before) =>
        before ? { ...before, key: "", caPem: "" } : before,
      );
      const refreshed = await load();
      if (!refreshed) return;
      if (refreshed.revision !== result.revision) {
        setNotice(
          "本次保存已提交，但当前配置修订又发生变化。请重新检查后检测。",
        );
        return;
      }
      setNotice(
        result.applied
          ? "连接设置已保存并生效，尚未验证业务读取。请点击“检测连接”。"
          : "设置已保存，仍待部署生效；请核对服务状态后检测。",
      );
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof IntegrationError &&
            cause.code === "revision_conflict"
            ? "配置已被其他页面更新，请刷新后重新检查。"
            : !(cause instanceof IntegrationError) ||
                ["timeout", "dependency_unavailable"].includes(cause.code)
              ? "保存结果无法确认。请先刷新配置核对当前修订，不要立即重复提交。"
              : readFailure(cause),
        );
        setDraft((before) =>
          before ? { ...before, key: "", caPem: "" } : before,
        );
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function testConnection() {
    if (!csrf || !view?.unlocked) return;
    const controller = start();
    setNotice("");
    try {
      const result = await integrationPost<TestResult>(
        "external/test",
        { kind },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const refreshing = load();
      const refreshedController = active.current;
      const refreshed = await refreshing;
      if (
        !refreshed ||
        active.current !== refreshedController ||
        refreshedController?.signal.aborted
      )
        return;
      const receipt = refreshed[kind].last_test;
      if (
        result.revision !== refreshed.revision ||
        receipt?.revision !== refreshed.revision ||
        receipt.state !== result.state
      ) {
        setNotice(
          "检测结果对应旧配置，或当前配置的检测回执已变化。请重新检测。",
        );
        return;
      }
      setNotice(
        result.state === "connected"
          ? "一次真实只读检测已通过。仍须在业务页面核对授权范围与内容。"
          : `检测没有通过：${result.code}。请检查目标、凭据和对端授权。`,
      );
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  if (!csrf) return null;
  const connection = view?.[kind];
  const rail = testRail(connection?.last_test ?? null, view?.revision ?? -1);
  return (
    <section className="panel external-panel" aria-label={`${title(kind)}管理`}>
      <div className="section-heading">
        <h2>{title(kind)}</h2>
        <button className="button" disabled={busy} onClick={() => void load()}>
          <RefreshCw aria-hidden="true" />
          刷新配置
        </button>
      </div>
      <p className="muted">
        {kind === "assets"
          ? "在此管理资产库的 AssetLink 接入。保存设置后，请执行只读检测；资产可见范围仍由对端授权决定。"
          : "在此管理家庭设备的 Home Assistant 接入。保存地址与读取实体后，请执行只读检测；设备控制在家庭设备页面另行授权。"}
      </p>
      {busy && !view && (
        <StatePanel kind="loading" title="正在读取外部连接配置">
          <p>请稍候。</p>
        </StatePanel>
      )}
      {error && (
        <StatePanel
          kind="error"
          title={unavailable ? "当前账号无法管理外部连接" : "操作未完成"}
        >
          <p>{error}</p>
        </StatePanel>
      )}
      {notice && (
        <p className="external-notice" role="status">
          {notice}
        </p>
      )}
      {view && draft && connection && (
        <>
          <StatusRail tone={rail.tone} label={rail.label}>
            <p>
              {title(kind)} ·{" "}
              {connection.last_test
                ? `上次检测：${connection.last_test.checked_at}（${connection.last_test.code}）${connection.last_test.revision !== view.revision ? "；配置修订已改变，需重新检测" : ""}`
                : "保存不会自动检测，尚无实际业务读取结果。"}
            </p>
          </StatusRail>
          {!view.unlocked ? (
            <form
              className="external-unlock"
              onSubmit={(event) => void unlock(event)}
            >
              <label>
                管理员密码（二次验证）
                <input
                  type="password"
                  autoComplete="current-password"
                  minLength={PASSWORD_MIN_LENGTH}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <button className="button" disabled={busy || !password}>
                解锁连接管理
              </button>
            </form>
          ) : (
            <>
              <div className="external-actions">
                <p className="muted">
                  管理操作已解锁。地址和凭据只提交给同源平台，不保存在浏览器。
                </p>
                <button
                  className="button"
                  onClick={() => void lock()}
                  disabled={busy}
                >
                  锁定管理
                </button>
              </div>
              <DetailCard title={`编辑${title(kind)}设置`}>
                {error && <p role="alert">{error}</p>}
                {notice && <p role="status">{notice}</p>}
                <form
                  className="external-form"
                  onSubmit={(event) => void save(event)}
                >
                  <label className="external-check">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) =>
                        update({ enabled: event.target.checked })
                      }
                    />
                    启用此连接
                  </label>
                  <label>
                    {kind === "assets"
                      ? "AssetLink HTTPS 端点"
                      : "Home Assistant 基础地址"}
                    <input
                      type="url"
                      value={draft.url}
                      required
                      maxLength={2048}
                      placeholder={
                        kind === "assets"
                          ? "https://资产服务:端口/assetlink/v1/control"
                          : "http://局域网设备:8123"
                      }
                      onChange={(event) => update({ url: event.target.value })}
                    />
                  </label>
                  {kind === "assets" ? (
                    <p className="muted">
                      只接受 HTTPS 的固定 AssetLink
                      控制端点；资产库可见范围仍由对端授权决定。
                    </p>
                  ) : (
                    <>
                      <label className="external-check">
                        <input
                          type="checkbox"
                          checked={draft.allowPrivateHttp}
                          onChange={(event) =>
                            update({ allowPrivateHttp: event.target.checked })
                          }
                        />
                        明确允许经部署审查的局域网 HTTP
                      </label>
                      <p className="muted">
                        只读取下方登记的实体；保存读数配置不会生成设备控制动作。控制仍需独立授权与解锁。
                      </p>
                      <Entities
                        value={draft.entities}
                        onChange={(entities) => update({ entities })}
                      />
                    </>
                  )}
                  <fieldset className="external-credential">
                    <legend>服务凭据</legend>
                    <p className="muted">
                      当前：
                      {connection.credential_configured
                        ? "已保存（不会回显）"
                        : "尚未保存"}
                    </p>
                    {(["keep", "replace", "clear"] as const).map((action) => (
                      <label key={action}>
                        <input
                          type="radio"
                          name={`credential-${kind}`}
                          checked={draft.keyAction === action}
                          onChange={() =>
                            update({ keyAction: action, key: "" })
                          }
                        />
                        {action === "keep"
                          ? "保留现有"
                          : action === "replace"
                            ? "替换凭据"
                            : "明确清除"}
                      </label>
                    ))}
                    {draft.keyAction === "replace" && (
                      <label>
                        新凭据
                        <input
                          type="password"
                          autoComplete="off"
                          minLength={24}
                          maxLength={4096}
                          value={draft.key}
                          onChange={(event) =>
                            update({ key: event.target.value })
                          }
                        />
                      </label>
                    )}
                  </fieldset>
                  <fieldset className="external-credential">
                    <legend>自签证书 CA</legend>
                    <p className="muted">
                      当前：
                      {connection.ca_configured
                        ? "已保存（不会回显）"
                        : "使用系统信任"}
                    </p>
                    {(["keep", "replace", "clear"] as const).map((action) => (
                      <label key={action}>
                        <input
                          type="radio"
                          name={`ca-${kind}`}
                          checked={draft.caAction === action}
                          onChange={() =>
                            update({ caAction: action, caPem: "" })
                          }
                        />
                        {action === "keep"
                          ? "保留现有"
                          : action === "replace"
                            ? "替换 CA"
                            : "明确清除"}
                      </label>
                    ))}
                    {draft.caAction === "replace" && (
                      <label>
                        CA PEM
                        <textarea
                          value={draft.caPem}
                          rows={4}
                          maxLength={16384}
                          onChange={(event) =>
                            update({ caPem: event.target.value })
                          }
                        />
                      </label>
                    )}
                  </fieldset>
                  <div className="external-actions">
                    <button
                      className="button primary"
                      type="submit"
                      disabled={busy}
                    >
                      保存设置
                    </button>
                    <button
                      className="button"
                      type="button"
                      disabled={busy || !connection.configured}
                      onClick={() => void testConnection()}
                    >
                      检测连接（只读一次）
                    </button>
                  </div>
                </form>
              </DetailCard>
            </>
          )}
        </>
      )}
    </section>
  );
}

function Entities({
  value,
  onChange,
}: {
  value: Entity[];
  onChange: (items: Entity[]) => void;
}) {
  function update(index: number, change: Partial<Entity>) {
    onChange(
      value.map((row, at) => (at === index ? { ...row, ...change } : row)),
    );
  }
  return (
    <fieldset className="external-entities">
      <legend>要读取的 HA 实体</legend>
      {value.map((row, index) => (
        <div key={index} className="external-entity">
          <label>
            实体 ID
            <input
              value={row.entity_id}
              placeholder="sensor.living_room_temperature"
              required
              maxLength={71}
              pattern={`${row.kind}\\.[a-z0-9_]{1,64}`}
              onChange={(event) =>
                update(index, { entity_id: event.target.value })
              }
            />
          </label>
          <label>
            显示名称
            <input
              value={row.label}
              required
              maxLength={64}
              onChange={(event) => update(index, { label: event.target.value })}
            />
          </label>
          <label>
            类型
            <select
              value={row.kind}
              onChange={(event) =>
                update(index, { kind: event.target.value as Entity["kind"] })
              }
            >
              <option value="sensor">传感器</option>
              <option value="light">灯</option>
              <option value="switch">开关</option>
            </select>
          </label>
          <label>
            单位（可选）
            <input
              value={row.unit ?? ""}
              maxLength={16}
              onChange={(event) => update(index, { unit: event.target.value })}
            />
          </label>
          <button
            type="button"
            className="button"
            onClick={() => onChange(value.filter((_, at) => at !== index))}
          >
            移除实体
          </button>
        </div>
      ))}
      <button
        type="button"
        className="button"
        disabled={value.length >= 32}
        onClick={() =>
          onChange([...value, { entity_id: "", label: "", kind: "sensor" }])
        }
      >
        添加实体
      </button>
    </fieldset>
  );
}
