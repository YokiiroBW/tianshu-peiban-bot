import { PASSWORD_MIN_LENGTH } from "../../app/passwordPolicy";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { LoginLink, useSessionGuard } from "../../app/Auth";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import { requestId } from "../../app/requestId";
import {
  call,
  session as readSession,
  WebError,
  type ModelsView,
  type SessionState,
} from "./api";
import { ProviderManager } from "./ProviderManager";
import type { ProviderActions } from "./providerTypes";
import { readProviders, type ProviderView } from "./providerApi";
import { ModelFunctionsPanel } from "./ModelFunctionsPanel";
import "./models.css";

function errorMessage(cause: unknown) {
  if (cause instanceof WebError) {
    if (cause.code === "result_unknown")
      return `${cause.message} 页面不会自动重试。`;
    return cause.executionState === "unknown"
      ? `${cause.message} 本次执行结果未知；请重新读取状态，不会自动重试。`
      : cause.message;
  }
  return "连接中断，本次操作结果未知。请重新读取状态，不会自动重试。";
}

export function ProviderModelsPanel({
  mode = "providers",
}: {
  mode?: "providers" | "defaults";
}) {
  const [session, setSession] = useState<SessionState | null>(null);
  useSessionGuard(session);
  const [management, setManagement] = useState<ModelsView["management"] | null>(
    null,
  );
  const [providerView, setProviderView] = useState<ProviderView | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const active = useRef<AbortController | null>(null);
  const password = useRef<HTMLInputElement>(null);

  const start = useCallback(() => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    return controller;
  }, []);

  const load = useCallback(async (signal: AbortSignal) => {
    setBusy(true);
    setError("");
    try {
      const current = await readSession(signal);
      if (signal.aborted) return;
      setSession(current);
      if (!current.authenticated) {
        setManagement(null);
        setProviderView(null);
        return;
      }
      const view = await call<ModelsView>(
        "models/view",
        {},
        current.csrf,
        signal,
      );
      if (signal.aborted) return;
      setManagement(view.management);
      if (view.management.available && view.management.unlocked) {
        setProviderView(await readProviders(current.csrf, signal));
      } else {
        setProviderView(null);
      }
    } catch (cause) {
      if (!signal.aborted) {
        setProviderView(null);
        setManagement(null);
        setError(errorMessage(cause));
        if (
          cause instanceof WebError &&
          ["session_expired", "unauthorized"].includes(cause.code)
        ) {
          try {
            setSession(await readSession(signal));
          } catch {
            setSession(null);
          }
        }
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }, []);

  useEffect(() => {
    const controller = start();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, start]);

  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    const controller = start();
    setBusy(true);
    setError("");
    try {
      await call(
        "models/unlock",
        { password: password.current?.value ?? "" },
        session.csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setNotice("模型管理已解锁，仅在当前会话有效。");
        await load(controller.signal);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (password.current) password.current.value = "";
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function lock() {
    if (!session) return;
    const controller = start();
    setBusy(true);
    try {
      await call("models/lock", {}, session.csrf, controller.signal);
      if (!controller.signal.aborted) {
        setProviderView(null);
        setNotice("已锁定模型管理；聊天登录保持有效。");
        await load(controller.signal);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function reloadProviders(signal: AbortSignal) {
    if (!session) throw new WebError("session_expired", 401);
    const result = await readProviders(session.csrf, signal);
    if (!signal.aborted) setProviderView(result);
  }

  async function invoke<T>(
    operation: string,
    body: object,
    signal: AbortSignal,
  ): Promise<T> {
    if (!session) throw new WebError("session_expired", 401);
    return call<T>(`providers/${operation}`, body, session.csrf, signal);
  }

  const actions: ProviderActions = {
    saveSource: async (draft, original, signal) => {
      const result = await invoke<{ source_id: string }>(
        "source-save",
        {
          client_request_id: requestId(),
          name: draft.name,
          base_url: draft.baseUrl,
          enabled: draft.enabled,
          ...(original
            ? {
                source_id: original.sourceId,
                expected_revision: original.revision,
              }
            : {}),
          ...(draft.apiKey ? { api_key: draft.apiKey } : {}),
        },
        signal,
      );
      await reloadProviders(signal);
      return result.source_id;
    },
    listModels: async (source, signal) => {
      const result = await invoke<{
        source_id: string;
        revision: number;
        models: string[];
      }>(
        "source-models",
        { source_id: source.sourceId, expected_revision: source.revision },
        signal,
      );
      if (
        result.source_id !== source.sourceId ||
        result.revision !== source.revision ||
        !Array.isArray(result.models) ||
        !result.models.every((item) => typeof item === "string")
      )
        throw new WebError("dependency_unavailable", 502);
      return result.models;
    },
    addModels: async (source, models, kind, signal) => {
      await invoke(
        "model-add",
        {
          client_request_id: requestId(),
          source_id: source.sourceId,
          expected_revision: source.revision,
          models,
          kind,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    saveModel: async (model, draft, signal) => {
      await invoke(
        "model-save",
        {
          client_request_id: requestId(),
          provider_id: model.providerId,
          expected_revision: model.revision,
          name: draft.name,
          model_id: draft.modelId,
          kind: draft.kind,
          enabled: draft.enabled,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    clearKey: async (source, signal) => {
      await invoke(
        "source-clear-key",
        {
          client_request_id: requestId(),
          source_id: source.sourceId,
          expected_revision: source.revision,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    removeSource: async (source, signal) => {
      await invoke(
        "source-delete",
        {
          client_request_id: requestId(),
          source_id: source.sourceId,
          expected_revision: source.revision,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    test: async (provider, signal) => {
      let result: { provider_id: string; revision: number; outcome: string };
      try {
        result = await invoke<{
          provider_id: string;
          revision: number;
          outcome: string;
        }>(
          "test",
          {
            client_request_id: requestId(),
            provider_id: provider.providerId,
            expected_revision: provider.revision,
          },
          signal,
        );
      } catch (cause) {
        if (!signal.aborted) {
          try {
            await reloadProviders(signal);
          } catch {
            // Preserve the test failure if the follow-up read also loses its connection.
          }
        }
        throw cause;
      }
      if (
        result.provider_id !== provider.providerId ||
        result.revision !== provider.revision ||
        result.outcome !== "succeeded"
      )
        throw new WebError("dependency_unavailable", 502);
      await reloadProviders(signal);
    },
    setDefault: async (provider, signal) => {
      if (!providerView) throw new WebError("dependency_unavailable", 503);
      await invoke(
        "default",
        {
          client_request_id: requestId(),
          provider_id: provider.providerId,
          expected_revision: provider.revision,
          expected_default_revision: providerView.defaultRevision,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    setEnabled: async (provider, enabled, signal) => {
      await invoke(
        "model-save",
        {
          client_request_id: requestId(),
          provider_id: provider.providerId,
          expected_revision: provider.revision,
          name: provider.name,
          model_id: provider.modelId,
          kind: provider.kind ?? "text",
          enabled,
        },
        signal,
      );
      await reloadProviders(signal);
    },
    remove: async (provider, signal) => {
      await invoke(
        "delete",
        {
          client_request_id: requestId(),
          provider_id: provider.providerId,
          expected_revision: provider.revision,
        },
        signal,
      );
      await reloadProviders(signal);
    },
  };

  function handleProviderError(cause: unknown) {
    const message = errorMessage(cause);
    setError(message);
    const authorityLost =
      cause instanceof WebError &&
      [
        "session_expired",
        "unauthorized",
        "forbidden",
        "management_required",
        "management_disabled",
        "operator_not_authorized",
      ].includes(cause.code);
    if (authorityLost) {
      setProviderView(null);
      setManagement(null);
    }
    const controller = start();
    void (async () => {
      try {
        if (authorityLost) {
          await load(controller.signal);
        } else {
          await reloadProviders(controller.signal);
        }
      } catch {
        if (!controller.signal.aborted) setProviderView(null);
      } finally {
        if (!controller.signal.aborted) setError(message);
      }
    })();
  }

  return (
    <section
      className="panel models"
      aria-label="模型供应商管理"
      aria-busy={busy}
    >
      <div className="section-heading">
        <h2>模型配置</h2>
        {management && (
          <StatusRail
            tone={
              !management.available
                ? "gray"
                : management.unlocked
                  ? "blue"
                  : "yellow"
            }
            label={
              !management.available
                ? "管理不可用"
                : management.unlocked
                  ? "管理已解锁"
                  : "管理待验证"
            }
          />
        )}
      </div>
      <p className="muted">
        供应商统一管理连接，模型分别添加与测试，各项功能自由选择。
      </p>
      <div role="status" className="models-status">
        {busy
          ? "正在读取模型配置…"
          : session?.authenticated
            ? `已登录 · ${session.username}`
            : "请登录管理员账号"}
      </div>
      {error && (
        <p className="models-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="models-notice" role="status">
          {notice}
        </p>
      )}
      {!session ? (
        <StatePanel kind="loading" title="正在连接模型配置入口">
          <p>稍候。</p>
        </StatePanel>
      ) : !session.authenticated ? (
        <LoginLink />
      ) : (
        <>
          <div className="models-actions">
            <button
              className="button"
              onClick={() => void load(start().signal)}
              disabled={busy}
            >
              <RefreshCw aria-hidden="true" />
              重新读取
            </button>
            {management?.unlocked && (
              <button
                className="button"
                onClick={() => void lock()}
                disabled={busy}
              >
                锁定管理
              </button>
            )}
          </div>
          {management?.available === false && (
            <StatePanel
              kind="unconfigured"
              title={
                management.code === "management_disabled"
                  ? "此部署尚未开启模型管理"
                  : "当前账号没有模型管理权限"
              }
            >
              <p>请检查管理员账号与服务配置。</p>
            </StatePanel>
          )}
          {management?.available && !management.unlocked && (
            <div className="models-unlock">
              <ShieldCheck aria-hidden="true" />
              <h3>验证管理员密码后管理供应商</h3>
              <p className="muted">独立管理授权仅在当前登录会话有效。</p>
              <form onSubmit={(event) => void unlock(event)}>
                <label>
                  管理员密码
                  <input
                    ref={password}
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
                  解锁模型管理
                </button>
              </form>
            </div>
          )}
          {management?.unlocked && providerView && (
            <>
              {mode === "providers" && (
                <ProviderManager
                  providers={providerView.providers}
                  sources={providerView.sources}
                  editable
                  actions={actions}
                  onError={handleProviderError}
                />
              )}
              {mode === "defaults" ? (
                <ModelFunctionsPanel
                  functions={providerView.functions}
                  providers={providerView.providers}
                  onError={handleProviderError}
                  save={async (item, provider, signal) => {
                    await invoke(
                      "function",
                      {
                        client_request_id: requestId(),
                        function_id: item.function_id,
                        provider_id: provider?.providerId ?? null,
                        expected_revision: provider?.revision ?? null,
                        expected_binding_revision: item.revision,
                      },
                      signal,
                    );
                    await reloadProviders(signal);
                  }}
                />
              ) : (
                <p className="muted">
                  具体角色使用的模型请在<a href="#/roles/settings">角色设置</a>
                  中分配。
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
