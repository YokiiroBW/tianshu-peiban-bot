import { PASSWORD_MIN_LENGTH } from "../../app/passwordPolicy";
import { DetailCard } from "../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import { call, session, WebError } from "./api";
import "./access.css";

type Access = {
  mode: "http" | "https" | "proxy";
  origin: string;
  certificate: string | null;
};
type View = {
  available: boolean;
  active: Access;
  saved: Access;
  revision: number;
  restart_required: boolean;
  certificates: string[];
  listener_port: number;
};

export function AccessPanel() {
  const [view, setView] = useState<View | null>(null);
  const [draft, setDraft] = useState<Access>({
    mode: "http",
    origin: "",
    certificate: null,
  });
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void (async () => {
      try {
        const login = await session(current.signal);
        if (current.signal.aborted) return;
        if (!login.authenticated) {
          window.dispatchEvent(new Event("tianshu:session-lost"));
          setError("请先通过页面右上角的统一入口登录，再管理访问设置。");
          return;
        }
        const value = await call<View>(
          "access/view",
          {},
          login.csrf,
          current.signal,
        );
        setView(value);
        if (value.available) setDraft(value.saved);
      } catch (e) {
        if (!current.signal.aborted) {
          if (
            e instanceof WebError &&
            ["unauthorized", "session_expired"].includes(e.code)
          )
            window.dispatchEvent(new Event("tianshu:session-lost"));
          setError(e instanceof Error ? e.message : "无法读取访问设置。");
        }
      }
    })();
    return () => current.abort();
  }, []);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!view || busy || !controller.current) return;
    const signal = controller.current.signal;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const login = await session(signal);
      if (signal.aborted) return;
      if (!login.authenticated) {
        window.dispatchEvent(new Event("tianshu:session-lost"));
        return;
      }
      const updated = await call<View>(
        "access/save",
        {
          value: draft,
          revision: view.revision,
          password,
        },
        login.csrf,
        signal,
      );
      if (!signal.aborted) {
        setView(updated);
        setNotice(
          updated.restart_required
            ? "已保存。重启平台后生效；当前入口继续可用。"
            : "已保存，与当前运行配置一致。",
        );
      }
    } catch (e) {
      if (!signal.aborted) {
        if (
          e instanceof WebError &&
          ["unauthorized", "session_expired"].includes(e.code)
        )
          window.dispatchEvent(new Event("tianshu:session-lost"));
        setError(
          e instanceof WebError && e.code === "version_conflict"
            ? "访问设置已被其他操作更新，请刷新页面后重试。"
            : e instanceof WebError && e.code === "invalid_input"
              ? "请检查完整访问地址与证书。HTTPS 证书须有效并匹配该域名。"
              : e instanceof Error
                ? e.message
                : "保存失败，当前入口未改变。",
        );
      }
    } finally {
      if (!signal.aborted) {
        setBusy(false);
        setPassword("");
      }
    }
  }

  return (
    <section className="panel access-settings" aria-labelledby="access-title">
      <div className="section-heading">
        <h2 id="access-title">访问地址与 HTTPS</h2>
      </div>
      <p>
        局域网默认使用 HTTP 和设备 IP。也可以使用域名，或让反向代理提供 HTTPS。
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {!view && !error && <p role="status">正在读取访问设置…</p>}
      {view && !view.available && <p>此部署尚未启用独立网页入口。</p>}
      {view?.available && (
        <>
          <p>
            当前生效：<strong>{view.active.origin}</strong>
          </p>
          {view.restart_required && (
            <p role="status">
              待重启：{view.saved.origin}。请在部署管理中重启平台。
            </p>
          )}
          <DetailCard title={"修改访问地址与 HTTPS"}>
            {error && <p role="alert">{error}</p>}
            {notice && <p role="status">{notice}</p>}
            <form onSubmit={(event) => void save(event)}>
              <fieldset disabled={busy}>
                <legend>访问方式</legend>
                <label htmlFor="access-mode">连接方式</label>
                <select
                  id="access-mode"
                  value={draft.mode}
                  onChange={(event) => {
                    const mode = event.target.value as Access["mode"];
                    setDraft({
                      ...draft,
                      mode,
                      certificate:
                        mode === "https"
                          ? (view.certificates[0] ?? null)
                          : null,
                      origin:
                        mode === "proxy"
                          ? draft.origin
                          : draft.origin.replace(/^https?:/, `${mode}:`),
                    });
                  }}
                >
                  <option value="http">直接 HTTP（默认）</option>
                  <option value="https">直接 HTTPS</option>
                  <option value="proxy">反向代理</option>
                </select>
                <label htmlFor="access-origin">完整访问地址（IP 或域名）</label>
                <input
                  id="access-origin"
                  type="url"
                  value={draft.origin}
                  required
                  maxLength={300}
                  placeholder="http://192.168.31.210:19443"
                  onChange={(event) =>
                    setDraft({ ...draft, origin: event.target.value })
                  }
                />
                <p>
                  只填写协议、地址和端口，不含路径。修改域名不会自动创建 DNS
                  记录。
                </p>
                {draft.mode === "https" && (
                  <>
                    <label htmlFor="access-certificate">已安装证书</label>
                    <select
                      id="access-certificate"
                      value={draft.certificate ?? ""}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          certificate: event.target.value || null,
                        })
                      }
                      required
                    >
                      <option value="" disabled>
                        请选择证书
                      </option>
                      {view.certificates.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                    </select>
                    {!view.certificates.length && (
                      <p>
                        尚未安装证书，请先由部署管理员添加证书后再启用 HTTPS。
                      </p>
                    )}
                  </>
                )}
                {draft.mode === "proxy" && (
                  <p>
                    反代上游填写部署时发布的 HTTP 地址，并保留原始 Host。
                    上方访问地址填写用户实际打开的域名地址；HTTPS
                    证书由反向代理管理。
                  </p>
                )}
                <label htmlFor="access-password">管理员密码</label>
                <input
                  id="access-password"
                  type="password"
                  autoComplete="current-password"
                  required
                  minLength={PASSWORD_MIN_LENGTH}
                  maxLength={256}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  className="button"
                  type="submit"
                  disabled={draft.mode === "https" && !draft.certificate}
                >
                  {busy ? "正在保存…" : "保存，重启后生效"}
                </button>
              </fieldset>
            </form>
          </DetailCard>
        </>
      )}
    </section>
  );
}
