import { useEffect, useRef, useState } from "react";
import { RefreshCw, Settings2 } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { requestId } from "../../app/requestId";
import { request } from "./api";
import { ProviderModelsPanel } from "../settings/ProviderModelsPanel";
import { SkillsPanel } from "../settings/skills/SkillsPanel";
type FunctionRow = {
  function_id: string;
  name: string;
  description: string;
  connected: boolean;
  provider_id: string | null;
  provider_revision: number | null;
  effective_provider_id: string | null;
  source: string;
  configured: boolean;
};
type View = {
  actor_id: string;
  name: string;
  version: number;
  functions: FunctionRow[];
  providers: {
    id: string;
    name: string;
    model: string;
    revision: number;
    available: boolean;
  }[];
};
const sources: Record<string, string> = {
  role_function: "此角色单独指定",
  role_default: "继承此角色主模型",
  system_function: "继承全局功能默认",
  system_default: "继承系统默认",
};
export default function RoleSettings() {
  const { session } = useAuth(),
    workspace = useRoleWorkspace();
  const [view, setView] = useState<View | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const generation = useRef(0);
  const [defaultsOpen, setDefaultsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [tab, setTab] = useState<"models" | "skills">("models");
  useEffect(() => {
    const controller = new AbortController();
    const mark = ++generation.current;
    setView(null);
    setError("");
    setNotice("");
    setBusy(false);
    if (session?.authenticated && workspace.actor)
      void request<View>(
        "roles/models/view",
        controller.signal,
        { actor_id: workspace.actor },
        session.csrf,
      )
        .then((v) => {
          if (mark === generation.current) setView(v);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        });
    return () => controller.abort();
  }, [workspace.actor, session?.csrf, revision]);
  async function save(row: FunctionRow, key: string) {
    if (!session?.authenticated || !view) return;
    const mark = generation.current;
    setBusy(true);
    setError("");
    try {
      const provider = view.providers.find(
        (p) => `${p.id}@${p.revision}` === key,
      );
      const next = await request<View>(
        "roles/models/apply",
        new AbortController().signal,
        {
          actor_id: view.actor_id,
          function_id: row.function_id,
          provider_id: provider?.id ?? null,
          provider_revision: provider?.revision ?? null,
          expected_revision: view.version,
          client_request_id: requestId(),
        },
        session.csrf,
      );
      if (mark === generation.current) {
        setView(next);
        setNotice(`${row.name}模型已保存。`);
      }
    } catch (e) {
      if (mark === generation.current)
        setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      if (mark === generation.current) setBusy(false);
    }
  }
  return (
    <section className="role-settings-page scroll-page">
      <header className="role-section-heading">
        <div className="role-identity-title">
          <span className="role-context-avatar">
            <Settings2 />
          </span>
          <div>
            <h2>
              {workspace.roles.find((r) => r.id === workspace.actor)?.name ??
                "选择角色"}
              的设置
            </h2>
            <p>这里的模型选择只作用于当前角色。</p>
          </div>
        </div>
        <button
          className="button"
          disabled={busy || !workspace.actor}
          onClick={() => setRevision((n) => n + 1)}
        >
          <RefreshCw aria-hidden="true" />
          刷新设置
        </button>
      </header>
      <nav className="role-settings-tabs" aria-label="角色设置分类">
        <button
          aria-selected={tab === "models"}
          onClick={() => setTab("models")}
        >
          模型分工
        </button>
        <a className="button" href="#/roles/persona">
          人格与世界
        </a>
        <button
          aria-selected={tab === "skills"}
          onClick={() => setTab("skills")}
        >
          角色技能
        </button>
        <a className="button" href="#/roles">
          基本资料
        </a>
      </nav>
      {tab === "skills" ? (
        <SkillsPanel key={workspace.actor} actorId={workspace.actor} />
      ) : (
        <>
          {error && <p role="alert">{error}</p>}
          {error &&
            workspace.roles.find((role) => role.id === workspace.actor)
              ?.state === "legacy" && (
              <p>
                此角色需要先在<a href="#/roles">角色管理</a>
                中应用基本设置，然后即可分配各功能模型。
              </p>
            )}
          {notice && <p role="status">{notice}</p>}
          {!workspace.actor ? (
            <p>请先在侧栏选择或创建角色。</p>
          ) : !view && !error ? (
            <p>正在读取角色设置…</p>
          ) : null}
          <div className="role-model-grid">
            {view?.actor_id === workspace.actor &&
              view.functions.map((row) => (
                <article className="role-model-card" key={row.function_id}>
                  <h3>{row.name}</h3>
                  <p>{row.description}</p>
                  <label>
                    {row.name}使用的模型
                    <select
                      value={
                        row.provider_id
                          ? `${row.provider_id}@${row.provider_revision}`
                          : ""
                      }
                      disabled={busy}
                      onChange={(e) => void save(row, e.target.value)}
                    >
                      <option value="">使用继承设置</option>
                      {row.provider_id &&
                        !view.providers.some(
                          (p) =>
                            p.id === row.provider_id &&
                            p.revision === row.provider_revision &&
                            p.available,
                        ) && (
                          <option
                            value={`${row.provider_id}@${row.provider_revision}`}
                          >
                            原选择当前不可用
                          </option>
                        )}
                      {view.providers
                        .filter((p) => p.available)
                        .map((p) => (
                          <option value={`${p.id}@${p.revision}`} key={p.id}>
                            {p.name} · {p.model}
                          </option>
                        ))}
                    </select>
                  </label>
                  <p>
                    {row.function_id === "chat" && row.provider_id
                      ? "此角色主模型"
                      : (sources[row.source] ?? row.source)}{" "}
                    ·{" "}
                    {view.providers.find(
                      (p) => p.id === row.effective_provider_id,
                    )?.name ?? "未配置"}
                  </p>
                  {!row.connected && <small>该功能尚未接入自动调用</small>}
                  {!row.configured && (
                    <small>
                      当前模型不可用，请选择其他模型或检查供应商连接。
                    </small>
                  )}
                </article>
              ))}
          </div>
          <p className="muted">
            <a href="#/settings/models">管理模型供应商与连接</a>
          </p>
          <details onToggle={(e) => setDefaultsOpen(e.currentTarget.open)}>
            <summary>系统默认模型（影响所有继承默认的角色）</summary>
            {defaultsOpen && <ProviderModelsPanel mode="defaults" />}
          </details>
        </>
      )}
    </section>
  );
}
