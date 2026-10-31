import { apiJson, ApiError } from "../api/client";
import { apiCommandData } from "../api/client";
import {
  readSession,
  presentSession,
  type SessionView,
  type AccountSession,
} from "../api/session";
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
import { saveRolePreference } from "./rolePreference";
import "./auth.css";

export type WebSession = AccountSession;
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
  const endpoint =
    path === "login" || path === "logout" ? `auth/${path}` : path;
  try {
    if (body === undefined)
      return presentSession(await apiJson<SessionView>(endpoint, { signal }));
    const view = await apiCommandData<SessionView>(
      endpoint,
      {
        ...body,
        client_request_id: crypto.randomUUID(),
        expected_revision: null,
      },
      csrf ?? "",
      signal ?? new AbortController().signal,
    );
    return presentSession(view);
  } catch (cause) {
    if (cause instanceof ApiError)
      throw new Error(messages[cause.code] ?? cause.message);
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
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  if (loading || error || !session) return <SessionStatus />;
  if (session.authenticated) return <GettingStarted />;
  const creating = !session.initialized;
  async function submit(form: HTMLFormElement) {
    if (active.current || !session) return;
    const data = new FormData(form);
    const password = String(data.get("password"));
    if (creating && password !== data.get("confirm")) {
      setFailure("两次输入的密码不一致。");
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setFailure("");
    const body = {
      username: String(data.get("username") ?? "").trim(),
      password,
      ...(creating && session.setup_token_required
        ? { setup_token: String(data.get("setup_token") ?? "") }
        : {}),
    };
    form
      .querySelectorAll<HTMLInputElement>('input[type="password"]')
      .forEach((input) => {
        input.value = "";
      });
    try {
      await api(
        creating ? "setup" : "login",
        body,
        session.csrf,
        controller.signal,
      );
      form.reset();
      await refresh();
    } catch (cause) {
      if (!controller.signal.aborted)
        setFailure(
          cause instanceof Error
            ? cause.message
            : "提交结果尚未确认，请读取原账号状态。",
        );
    } finally {
      if (active.current === controller) active.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel glass account-panel" aria-label="天枢账号">
      <p className="eyebrow">从这里开始</p>
      <h2>{creating ? "欢迎来到天枢" : "登录天枢"}</h2>
      <p>
        {creating
          ? "先创建首个管理员账号，再配置模型与角色。"
          : "使用已登记的应用账号登录。"}
      </p>
      <form
        aria-busy={busy}
        onSubmit={(event) => {
          event.preventDefault();
          void submit(event.currentTarget);
        }}
      >
        <label>
          {creating ? "管理员账号" : "账号"}
          <input
            name="username"
            autoComplete="username"
            required
            minLength={3}
            maxLength={96}
            pattern="[A-Za-z0-9_.@-]+"
            title="3到96位英文字母、数字、下划线、点、@或连字符"
            disabled={busy}
          />
        </label>
        <label>
          {creating ? "设置密码" : "密码"}
          <input
            name="password"
            type="password"
            autoComplete={creating ? "new-password" : "current-password"}
            required
            minLength={creating ? PASSWORD_MIN_LENGTH : 1}
            maxLength={1024}
            disabled={busy}
          />
        </label>
        {creating && (
          <>
            <p className="muted">密码至少 {PASSWORD_MIN_LENGTH} 个字符。</p>
            <label>
              确认密码
              <input
                name="confirm"
                type="password"
                autoComplete="new-password"
                required
                minLength={PASSWORD_MIN_LENGTH}
                maxLength={1024}
                disabled={busy}
              />
            </label>
          </>
        )}
        {creating && session.setup_token_required && (
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
        )}
        {failure && (
          <div>
            <p role="alert">{failure}</p>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              读取当前账号状态
            </button>
          </div>
        )}
        <button className="button primary" disabled={busy}>
          {busy ? "正在提交…" : creating ? "创建管理员账号" : "登录"}
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
        <p>先登录应用账号。</p>
        <LoginLink />
      </section>
    );
  const canConfigure = session.principal?.permissions.some(
    (permission) => permission === "*" || permission === "config.publish",
  );
  return (
    <section
      className="panel attention getting-started"
      aria-label="开始使用指引"
    >
      <h2>开始使用天枢</h2>
      <p>已登录 · {session.username}</p>
      <ol>
        <li>
          <a href="#/roles">选择角色与完整作者人格</a>
        </li>
        {canConfigure && (
          <li>
            <a href="#/settings/models">设置功能模型、连接与资源预算</a>
          </li>
        )}
        <li>
          <a href="#/roles/chat">进入已授权会话，开始第一条对话</a>
        </li>
      </ol>
      <p>
        各页面读取实际配置与授权；模型执行、工具结果和消息投递分别显示真实回执。
      </p>
      <div className="account-actions">
        <a className="button primary" href="#/roles/chat">
          开始对话
        </a>
        <button className="button" onClick={() => void refresh()}>
          读取账号状态
        </button>
        <a className="text-link" href="#/roles/room">
          看看小屋
        </a>
      </div>
    </section>
  );
}
