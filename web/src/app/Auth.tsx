import { apiJson, ApiError } from "../api/client";
import { apiCommandData } from "../api/client";
import { readSession, presentSession, type SessionView } from "../api/session";
import { PASSWORD_MIN_LENGTH } from "./passwordPolicy";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { StatePanel } from "../components/StatePanel";
import { resolveRoute } from "./modules";
import type { Session } from "../features/companion/api";
import { saveRolePreference } from "./rolePreference";
import "./auth.css";

export type WebSession = Session & {
  onboarding?: {
    state: "create_admin" | "claim_admin" | "ready" | "sign_in";
    credential_source: "setup" | "deployment" | "account";
    setup_token_required: boolean;
  };
};
type AuthState = {
  session: WebSession | null;
  loading: boolean;
  error: string;
  refresh: (background?: boolean) => Promise<WebSession | null>;
  logout: () => Promise<void>;
};
const AuthContext = createContext<AuthState | null>(null);
export function useAuth() {
  return useContext(AuthContext)!;
}
export function authRoute(hash = window.location.hash) {
  return /^#\/(login|setup)(?:\?|$)/.test(hash);
}
function target(hash: string) {
  return resolveRoute(hash) ? hash || "#/workbench" : "#/workbench";
}
export function loginHref(hash = window.location.hash) {
  return `#/login?next=${encodeURIComponent(target(hash))}`;
}
export function returnTarget(hash = window.location.hash) {
  return target(
    new URLSearchParams(hash.split("?")[1]).get("next") ?? "#/workbench",
  );
}

const messages: Record<string, string> = {
  session_expired: "登录页面已过期，请刷新账号状态后重新输入密码。",
  unauthorized: "账号或密码不正确，请重试。",
  forbidden: "验证未通过，请重新连接后重试。",
  invalid_input: "请检查账号和密码是否符合要求。",
  too_many_requests: "尝试次数过多，请稍后再试。",
  setup_unavailable: "首次设置状态已变化，请重新连接。",
  setup_closed:
    "首次创建已关闭，账号可能已由其他人完成设置。请刷新账号状态后登录。",
  setup_required: "需要先完成首次账号设置，请刷新账号状态。",
  account_unavailable: "当前无法接管账号，请刷新账号状态后重试。",
  setup_already_completed: "账号已创建，请重新连接后登录。",
};
async function api(
  path: string,
  body?: object,
  csrf?: string,
  signal?: AbortSignal,
): Promise<WebSession> {
  if (path === "session") return readSession(signal);
  const endpoint = path === "login" || path === "logout" ? `auth/${path}` : path;
  try {
    if (body === undefined) return presentSession(await apiJson<SessionView>(endpoint, { signal }));
    const view = await apiCommandData<SessionView>(endpoint, { ...body, client_request_id: crypto.randomUUID(), expected_revision: null }, csrf ?? "", signal ?? new AbortController().signal);
    return presentSession(view);
  } catch (cause) {
    if (cause instanceof ApiError) throw new Error(messages[cause.code] ?? cause.message);
    throw cause;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<WebSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const exiting = useRef(false);
  const refresh = useCallback(async (background = false) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    if (!background) setLoading(true);
    setError("");
    try {
      const next = await api(
        "session",
        undefined,
        undefined,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setSession((before) => {
          if (before?.authenticated && !next.authenticated) {
            window.dispatchEvent(new Event("tianshu:session-ended"));
            saveRolePreference(
              `tianshu-memory-role:${encodeURIComponent(before.username || before.csrf)}`,
              null,
            );
          }
          return next;
        });
        return next;
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setSession((before) => {
          if (before?.authenticated)
            saveRolePreference(
              `tianshu-memory-role:${encodeURIComponent(before.username || before.csrf)}`,
              null,
            );
          return null;
        });
        setError(cause instanceof Error ? cause.message : "连接中断，请重试。");
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
    return null;
  }, []);
  useEffect(() => {
    void refresh();
    const lost = async () => {
      if (exiting.current) return;
      // A 401 can also mean a wrong unlock password. Ask the authority before
      // ending the shared session, without unmounting a still-authenticated page.
      const next = await refresh(true);
      if (next && !next.authenticated && !authRoute())
        window.location.hash = loginHref();
    };
    const visible = () => {
      if (!document.hidden && !exiting.current) void refresh(true);
    };
    window.addEventListener("tianshu:session-lost", lost);
    window.addEventListener("online", visible);
    document.addEventListener("visibilitychange", visible);
    const channel =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel("tianshu-session");
    if (channel) channel.onmessage = lost;
    return () => {
      active.current?.abort();
      channel?.close();
      window.removeEventListener("tianshu:session-lost", lost);
      window.removeEventListener("online", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  const logout = useCallback(async () => {
    if (!session || exiting.current) return;
    exiting.current = true;
    saveRolePreference(
      `tianshu-memory-role:${encodeURIComponent(session.username || session.csrf)}`,
      null,
    );
    active.current?.abort();
    window.dispatchEvent(new Event("tianshu:session-ended"));
    setSession(null);
    setLoading(true);
    setError("");
    window.location.hash = "#/login";
    try {
      await api("logout", {}, session.csrf);
      if (typeof BroadcastChannel !== "undefined") {
        const channel = new BroadcastChannel("tianshu-session");
        channel.postMessage("logout");
        channel.close();
      }
      await refresh();
    } catch {
      setError("退出未确认，当前页面已清空。请重新连接后重试退出。");
      setLoading(false);
    } finally {
      exiting.current = false;
    }
  }, [session, refresh]);
  return (
    <AuthContext.Provider value={{ session, loading, error, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function SessionStatus() {
  const { loading, error, refresh } = useAuth();
  return (
    <StatePanel
      kind={loading ? "loading" : "error"}
      title={loading ? "正在确认账号状态" : "暂时无法连接天枢"}
      action={
        !loading && (
          <button className="button" onClick={() => void refresh()}>
            重新连接
          </button>
        )
      }
    >
      <p>
        {loading
          ? "请稍候，正在安全读取本次会话。"
          : error || "请重新连接后继续。"}
      </p>
    </StatePanel>
  );
}

export function useSessionGuard(session: { authenticated: boolean } | null) {
  const { session: shell } = useAuth();
  useEffect(() => {
    if (session?.authenticated === false && shell?.authenticated)
      window.dispatchEvent(new Event("tianshu:session-lost"));
  }, [session, shell]);
}

export function LoginLink() {
  return (
    <a className="button primary" href={loginHref()}>
      前往统一登录
    </a>
  );
}

export function AccountPage() {
  const { session, loading, error, refresh } = useAuth();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const submitting = useRef(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  if (loading || error || !session) return <SessionStatus />;
  const creating =
    session.onboarding?.state === "create_admin" && !session.authenticated;
  const claiming =
    session.onboarding?.state === "claim_admin" && session.authenticated;
  if (session.authenticated && !claiming) return <GettingStarted />;
  const setup = creating || claiming;
  const deployment =
    !session.onboarding ||
    session.onboarding.credential_source === "deployment";
  async function submit(form: HTMLFormElement) {
    if (submitting.current || !session) return;
    const data = new FormData(form);
    const password = String(data.get("password"));
    if (setup && password !== data.get("confirm")) {
      setFailure("两次输入的密码不一致，请重新确认。");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setFailure("");
    const controller = new AbortController();
    active.current = controller;
    const body = {
      username: String(data.get("username")).trim(),
      password,
      ...(creating && session.onboarding?.setup_token_required
        ? { setup_token: String(data.get("setup_token")) }
        : {}),
      ...(claiming
        ? { current_password: String(data.get("current_password")) }
        : {}),
    };
    // Keep secrets only in this one request, never in application state or storage.
    form
      .querySelectorAll<HTMLInputElement>('input[type="password"]')
      .forEach((input) => {
        input.value = "";
      });
    try {
      await api(
        creating ? "setup" : claiming ? "account/claim" : "login",
        body,
        session.csrf,
        controller.signal,
      );
      form.reset();
      await refresh();
    } catch (cause) {
      if (!controller.signal.aborted)
        setFailure(
          cause instanceof Error ? cause.message : "操作失败，请重试。",
        );
    } finally {
      if (!controller.signal.aborted) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <section className="panel glass account-panel" aria-label="天枢账号">
      <p className="eyebrow">从这里开始</p>
      <h2>
        {creating ? "欢迎来到天枢" : claiming ? "设置你自己的账号" : "登录天枢"}
      </h2>
      <p>
        {creating
          ? "先创建管理员账号。完成后配置模型，再选择角色开始对话。"
          : claiming
            ? "你已通过部署账号登录。验证原密码后，设置日常使用的账号和新密码。"
            : deployment
              ? "部署时已设置管理员账号。请使用安装时填写的账号和密码登录。"
              : "使用已有管理员账号登录，一次登录即可访问各工作区。"}
      </p>
      {!setup && (
        <p className="muted">
          {deployment
            ? "此入口不开放新账号注册。若不记得部署账号，请向安装者确认安装时保存的凭据。"
            : "此入口不开放新账号注册。请使用首次设置时保存的账号和密码。"}
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(event.currentTarget);
        }}
        aria-busy={busy}
      >
        <label>
          {claiming ? "新管理员账号" : "管理员账号"}
          <input
            name="username"
            autoComplete="username"
            required
            maxLength={128}
            disabled={busy}
          />
        </label>
        {claiming && (
          <label>
            原部署密码
            <input
              name="current_password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
              disabled={busy}
            />
          </label>
        )}
        <label>
          {setup ? "设置密码" : "密码"}
          <input
            name="password"
            type="password"
            autoComplete={setup ? "new-password" : "current-password"}
            required
            minLength={PASSWORD_MIN_LENGTH}
            maxLength={256}
            disabled={busy}
          />
        </label>
        {setup && (
          <>
            <p className="muted">
              密码至少 {PASSWORD_MIN_LENGTH} 个字符，请保存到自己的密码管理器。
            </p>
            <label>
              确认密码
              <input
                name="confirm"
                type="password"
                autoComplete="new-password"
                required
                minLength={PASSWORD_MIN_LENGTH}
                maxLength={256}
                disabled={busy}
              />
            </label>
          </>
        )}
        {creating && session.onboarding?.setup_token_required && (
          <>
            <label>
              安装验证码
              <input
                name="setup_token"
                type="password"
                autoComplete="off"
                required
                disabled={busy}
              />
            </label>
            <p className="muted">
              用于确认首次创建权限。请查看部署完成时保存的私有初始化信息，或向安装者索取。
            </p>
          </>
        )}
        {failure && (
          <div>
            <p role="alert">{failure}</p>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => {
                setFailure("");
                void refresh();
              }}
            >
              刷新账号状态
            </button>
          </div>
        )}
        <button className="button primary" type="submit" disabled={busy}>
          {busy
            ? "正在提交…"
            : creating
              ? "创建管理员账号"
              : claiming
                ? "保存自己的账号"
                : "登录"}
        </button>
      </form>
    </section>
  );
}

export function GettingStarted() {
  const { session, loading, error, refresh } = useAuth();
  if (loading || error || !session) return <SessionStatus />;
  if (!session.authenticated)
    return (
      <section className="panel attention">
        <h2>开始使用天枢</h2>
        <p>先登录账号，再配置模型，最后选择角色开始对话。</p>
        <LoginLink />
        <p className="muted">登录后即可进入个人空间。</p>
      </section>
    );
  const modelMissing = session.dialogue?.model === "not_configured";
  const modelState = session.dialogue?.model;
  const ready =
    session.dialogue?.available === true &&
    ["ready", "configured", "unverified"].includes(modelState ?? "");
  const verified = modelState === "ready";
  const actorReady = session.conversations?.some(
    (item) => item.actors.length > 0,
  );
  return (
    <section
      className="panel attention getting-started"
      aria-label="开始使用指引"
    >
      <h2>开始使用天枢</h2>
      <ol>
        <li>账号已就绪 · {session.username}</li>
        <li>
          {modelMissing
            ? "配置模型：尚未完成"
            : ready
              ? verified
                ? "对话服务报告可用"
                : "模型配置已登记，尚未验证实际回复"
              : "检查对话连接：尚未就绪"}
        </li>
        <li>{actorReady ? "选择角色，开始对话" : "角色与会话授权待设置"}</li>
      </ol>
      <p>
        {modelMissing
          ? "还没有可用模型。前往模型与用量选择并发布已登记的配置。当前网页使用安装者预设的模型模板；没有模板时，需要安装者先登记服务提供商。"
          : !ready
            ? "账号可以使用，但对话服务尚未连接或状态未确认。请检查连接设置。"
            : !actorReady
              ? "配置已登记，当前账号还没有可用角色。请检查角色与会话授权。"
              : "配置已登记，可以尝试第一条对话。实际回复以发送后的结果为准。"}
      </p>
      <div className="account-actions">
        <a
          className="button primary"
          href={
            modelMissing
              ? "#/settings/models"
              : ready && actorReady
                ? "#/roles/chat"
                : "#/settings/connections"
          }
        >
          {modelMissing
            ? "配置模型"
            : ready && actorReady
              ? "选择角色 / 开始对话"
              : "检查连接与授权"}
        </a>
        <button className="button" onClick={() => void refresh()}>
          刷新准备状态
        </button>
        <a className="text-link" href="#/roles/room">
          稍后配置，先看看小屋
        </a>
      </div>
    </section>
  );
}
