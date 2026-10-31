import { Drawer } from "../../components/Drawer";
import { DetailCard } from "../../components/DetailCard";
import { useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { LoginLink, useAuth } from "../../app/Auth";
import { requestId } from "../../app/requestId";
import { request } from "./api";
import "./roles.css";

type Role = {
  actor_id: string;
  version: number;
  name: string;
  profile_id: string | null;
  profile_version: number | null;
  profile_revision: string | null;
  provider_id: string | null;
  provider_revision: number | null;
  enabled: boolean;
  capabilities: string[];
  state: "active" | "disabled" | "pending" | "failed";
  stage: string;
  error_code: string | null;
  client_request_id: string;
  legacy: boolean;
  deleted?: boolean;
  deleting?: boolean;
};
type View = {
  roles: Role[];
  legacy_roles: {
    id: string;
    name: string;
    version: number;
    published_revision: string | null;
  }[];
  profiles: {
    id: string;
    name: string;
    version: number;
    revision: string | null;
  }[];
  providers: {
    id: string;
    name: string;
    model: string;
    revision: number;
    available: boolean;
    protocol?: string;
    model_capabilities?: {
      verification_source: string;
      capabilities: Record<
        string,
        { native: boolean; verification: "verified" | "unverified" }
      >;
    };
  }[];
  default_available: boolean;
  capabilities: string[];
};
type Form = {
  name: string;
  profile_id: string;
  provider_key: string;
  enabled: boolean;
  dialogue: boolean;
  memory_read: boolean;
  memory_write: boolean;
};
const empty: Form = {
  name: "",
  profile_id: "",
  provider_key: "",
  enabled: false,
  dialogue: true,
  memory_read: true,
  memory_write: true,
};
const status: Record<Role["state"], string> = {
  active: "已启用",
  disabled: "已停用",
  pending: "配置中",
  failed: "配置失败",
};
const reason: Record<string, string> = {
  dependency_unavailable: "相关服务暂时无法连接，请稍后继续配置。",
  provider_unavailable: "所选模型目前不可用，请选择其他模型。",
  version_conflict: "人格或模型版本已更新，请刷新后重新选择。",
  invalid_input: "设置有误，请检查人格、模型及名称。",
  forbidden: "当前账号没有完成此设置的权限。",
  not_found: "所选档案或角色已不存在，请刷新列表。",
};

function failure(code: string | null) {
  return code
    ? (reason[code] ?? "配置未完成，请检查设置或稍后重试。")
    : "正在核验服务。";
}

function existingName(item: { id: string; name: string }) {
  return item.name === item.id ? `原有角色 · ${item.id.slice(6)}` : item.name;
}

function formFor(role: Role): Form {
  return {
    name: role.name,
    profile_id: role.profile_id ?? "",
    provider_key: role.provider_id
      ? `${role.provider_id}@${role.provider_revision}`
      : "",
    enabled: role.enabled,
    dialogue: role.capabilities.includes("dialogue"),
    memory_read: role.capabilities.includes("memory.read"),
    memory_write: role.capabilities.includes("memory.write"),
  };
}

export default function RoleManager() {
  const { session, loading, error: authError } = useAuth();
  const workspace = useRoleWorkspace();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<Form>(empty);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const role = view?.roles.find((item) => item.actor_id === selected) ?? null;
  const legacy =
    view?.legacy_roles.find((item) => item.id === selected) ?? null;
  const profiles = view?.profiles ?? [];
  const providers = view?.providers ?? [];
  const selectedProvider = providers.find(
    (item) => `${item.id}@${item.revision}` === form.provider_key,
  );
  const visible = useMemo(
    () =>
      [
        ...(view?.roles ?? []).map((item) => ({
          id: item.actor_id,
          name: item.name,
          label: status[item.state],
        })),
        ...(view?.legacy_roles ?? []).map((item) => ({
          id: item.id,
          name: existingName(item),
          label: "待配置",
        })),
      ].filter((item) =>
        `${item.name} ${item.id}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()),
      ),
    [view, search],
  );

  async function refresh() {
    if (!session?.authenticated) return;
    const controller = new AbortController();
    try {
      const next = await request<View>(
        "roles/view",
        controller.signal,
        {},
        session.csrf,
      );
      setView(next);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "角色管理暂不可用。");
    }
  }

  useEffect(() => {
    if (session?.authenticated) void refresh();
  }, [session?.csrf]);

  function select(actor: string | null) {
    if (actor && !workspace.select(actor)) return;
    setSelected(actor);
    const found = view?.roles.find((item) => item.actor_id === actor);
    const existing = view?.legacy_roles.find((item) => item.id === actor);
    setForm(
      found
        ? formFor(found)
        : existing
          ? { ...empty, name: existingName(existing), enabled: true }
          : { ...empty },
    );
    setMobileDetail(true);
    setNotice("");
    setPendingId(null);
  }

  async function save(enabled = form.enabled) {
    if (!session?.authenticated || !view) return;
    setBusy(true);
    setError("");
    setNotice("");
    const profile = profiles.find((item) => item.id === form.profile_id);
    const provider = providers.find(
      (item) => `${item.id}@${item.revision}` === form.provider_key,
    );
    const pinnedVersion =
      role &&
      profile &&
      role.profile_id === profile.id &&
      role.profile_revision === profile.revision
        ? role.profile_version
        : profile?.version;
    if (
      (form.profile_id && !profile?.revision) ||
      (form.provider_key && !provider)
    ) {
      setError("请选择可用的人格档案与模型。");
      setBusy(false);
      return;
    }
    const clientId = pendingId ?? requestId();
    setPendingId(clientId);
    try {
      const result = await request<{ role: Role }>(
        "roles/apply",
        new AbortController().signal,
        {
          client_request_id: clientId,
          actor_id: role?.actor_id ?? legacy?.id ?? null,
          expected_revision: role?.version ?? 0,
          name: form.name.trim(),
          profile_id: profile?.id ?? null,
          profile_version: pinnedVersion ?? null,
          provider_id: provider?.id ?? null,
          provider_revision: provider?.revision ?? null,
          enabled,
          capabilities: [
            ...(form.dialogue ? ["dialogue"] : []),
            ...(form.memory_read ? ["memory.read"] : []),
            ...(form.memory_write ? ["memory.write"] : []),
          ],
        },
        session.csrf,
      );
      await refresh();
      await workspace.refresh();
      workspace.select(result.role.actor_id);
      setSelected(result.role.actor_id);
      setForm(formFor(result.role));
      setPendingId(result.role.state === "pending" ? clientId : null);
      setNotice(
        result.role.state === "pending"
          ? `尚未生效：${failure(result.role.error_code)}可重试继续。`
          : enabled
            ? "角色设置已生效。"
            : "角色已停用，历史记录保留。",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!session?.authenticated || !role) return;
    setBusy(true);
    try {
      const result = await request<{ role: Role }>(
        "roles/retry",
        new AbortController().signal,
        { actor_id: role.actor_id, client_request_id: role.client_request_id },
        session.csrf,
      );
      await refresh();
      await workspace.refresh();
      if (result.role.deleted) {
        setSelected(null);
        setMobileDetail(false);
        setNotice("角色已删除，历史对话与记忆保留。");
        return;
      }
      setPendingId(
        result.role.state === "pending" ? result.role.client_request_id : null,
      );
      setForm(formFor(result.role));
      setNotice(
        result.role.state === "pending"
          ? result.role.stage.startsWith("cancel_")
            ? "仍在停用中，请稍后继续。"
            : "仍在配置中，请稍后重试。"
          : result.role.state === "disabled"
            ? "角色已停用，历史记录保留。"
            : "配置已生效。",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重试失败。");
    } finally {
      setBusy(false);
    }
  }

  async function cancelPending() {
    if (!session?.authenticated || !role) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ role: Role }>(
        "roles/cancel",
        new AbortController().signal,
        {
          actor_id: role.actor_id,
          expected_revision: role.version,
          client_request_id: requestId(),
        },
        session.csrf,
      );
      await refresh();
      setPendingId(null);
      setForm(formFor(result.role));
      setNotice(
        result.role.state === "disabled"
          ? "角色已停用，历史记录保留。"
          : "正在停用角色；完成授权核对前不会显示为已停用。",
      );
    } catch (cause) {
      await refresh();
      setError(
        cause instanceof Error ? cause.message : "停用尚未完成，请刷新后继续。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function deleteRole() {
    if (!session?.authenticated || !role) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ role: Role & { deleted?: boolean } }>(
        "roles/delete",
        new AbortController().signal,
        {
          actor_id: role.actor_id,
          expected_revision: role.version,
          client_request_id: requestId(),
        },
        session.csrf,
      );
      await refresh();
      await workspace.refresh();
      setDeleteOpen(false);
      if (result.role.deleted) {
        setSelected(null);
        setMobileDetail(false);
        setNotice("角色已删除，历史对话与记忆保留。");
      } else {
        setNotice("正在停用并删除角色，完成后将从列表移除。");
      }
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "删除尚未完成，请刷新查看状态。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="role-page glass scroll-page" aria-label="角色管理">
      <header className="role-header">
        <div>
          <p className="eyebrow">角色工作区</p>
          <h2>角色管理</h2>
        </div>
        <button
          className="button"
          onClick={() => void refresh()}
          disabled={busy}
        >
          <RefreshCw aria-hidden="true" /> 刷新
        </button>
      </header>
      {loading ? (
        <p role="status">正在连接…</p>
      ) : authError ? (
        <p role="alert">{authError}</p>
      ) : !session?.authenticated ? (
        <LoginLink />
      ) : (
        <>
          {error && (
            <p className="role-error" role="alert">
              {error}
            </p>
          )}
          {notice && (
            <p className="role-notice" role="status">
              {notice}
            </p>
          )}
          {!view && !error && <p role="status">正在读取角色…</p>}
          {view && (
            <div className="role-layout scroll-layout">
              <aside
                className="role-list scroll-pane"
                aria-label="角色列表"
                tabIndex={0}
              >
                <div className="role-list-top">
                  <input
                    aria-label="搜索角色"
                    placeholder="搜索角色"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                  <button
                    className="button"
                    onClick={() => select(null)}
                    disabled={busy}
                  >
                    <Plus aria-hidden="true" /> 新建
                  </button>
                </div>
                {!profiles.length && (
                  <p className="muted">
                    人格档案可选，可先创建角色，之后再到
                    <a href="#/roles/persona">人格</a>中建立档案。
                  </p>
                )}
                {visible.map((item) => (
                  <button
                    type="button"
                    className="role-list-item"
                    aria-current={
                      workspace.actor === item.id ? "true" : undefined
                    }
                    key={item.id}
                    onClick={() => select(item.id)}
                  >
                    <span className="role-context-avatar" aria-hidden="true">
                      {item.name.slice(0, 1)}
                    </span>
                    <strong>{item.name}</strong>
                    <span>{item.label}</span>
                  </button>
                ))}
              </aside>
              <Drawer
                title={role ? `角色设置 · ${role.name}` : "新建角色"}
                variant="card"
                open={mobileDetail}
                onClose={() => setMobileDetail(false)}
              >
                {error && <p role="alert">{error}</p>}
                {notice && <p role="status">{notice}</p>}
                <div className="role-detail">
                  <button
                    className="role-back"
                    onClick={() => setMobileDetail(false)}
                  >
                    返回角色列表
                  </button>
                  <div className="role-detail-heading">
                    <h3>
                      {role?.name ?? (legacy ? existingName(legacy) : "新角色")}
                    </h3>
                    {(role || legacy) && (
                      <span>{role ? status[role.state] : "待配置"}</span>
                    )}
                  </div>
                  {legacy && (
                    <p className="role-notice">
                      现有角色可在此选择自己的模型。保留原角色身份、人格和历史；应用设置后才由角色管理接管。
                    </p>
                  )}
                  {role?.state === "disabled" && (
                    <p className="role-notice">
                      此角色已停用，历史记录保留。重新启用后才会接收新消息。
                    </p>
                  )}
                  {role?.state === "pending" && (
                    <div className="role-pending">
                      <p>
                        {role.stage.startsWith("cancel_")
                          ? `正在停用角色。${failure(role.error_code)}`
                          : `此设置尚未生效。${failure(role.error_code)}`}
                      </p>
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() => void retry()}
                      >
                        {role.stage.startsWith("cancel_")
                          ? role.deleting
                            ? "继续删除"
                            : "继续停用"
                          : "继续配置"}
                      </button>
                      {!role.stage.startsWith("cancel_") && (
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() => void cancelPending()}
                        >
                          取消配置并停用
                        </button>
                      )}
                    </div>
                  )}
                  {role?.state === "failed" && (
                    <p className="role-pending">
                      此设置未生效。{failure(role.error_code)}
                      更新设置后可重新应用。
                    </p>
                  )}
                  <div className="role-form">
                    <label>
                      名称
                      <input
                        value={form.name}
                        maxLength={80}
                        onChange={(e) =>
                          setForm({ ...form, name: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      人格档案
                      <select
                        value={form.profile_id}
                        onChange={(e) =>
                          setForm({ ...form, profile_id: e.target.value })
                        }
                      >
                        <option value="">
                          {legacy || role?.legacy
                            ? "保留原角色人格"
                            : "无（基础默认行为）"}
                        </option>
                        {profiles.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    {role?.state === "active" && (
                      <p className="muted">
                        当前生效人格：
                        {role.profile_id
                          ? `${profiles.find((item) => item.id === role.profile_id)?.name ?? "所选档案"} · 第 ${role.profile_version} 版`
                          : role.legacy
                            ? "原角色人格 · 保留现有版本"
                            : "无档案 · 基础默认行为"}
                      </p>
                    )}
                    <label>
                      模型
                      <select
                        value={form.provider_key}
                        onChange={(e) =>
                          setForm({ ...form, provider_key: e.target.value })
                        }
                      >
                        <option value="">
                          继承全局默认
                          {view.default_available ? "" : " · 不可用"}
                        </option>
                        {providers.map((item) => (
                          <option
                            key={`${item.id}@${item.revision}`}
                            value={`${item.id}@${item.revision}`}
                            disabled={!item.available}
                          >
                            {item.name} · {item.model}
                            {item.available ? "" : " · 不可用"}
                          </option>
                        ))}
                      </select>
                    </label>
                    <fieldset>
                      <legend>记忆</legend>
                      {selectedProvider?.model_capabilities && (
                        <div className="role-model-capabilities">
                          <h4>所选模型能力</h4>
                          <ul>
                            {Object.entries(
                              selectedProvider.model_capabilities.capabilities,
                            ).map(([name, capability]) => (
                              <li key={name}>
                                {(
                                  {
                                    text: "文字",
                                    dialogue: "对话",
                                    vision: "图片理解",
                                    tools: "工具调用",
                                    reasoning: "推理",
                                    streaming: "增量输出",
                                    cancellation: "取消",
                                    audio: "音频",
                                  } as Record<string, string>
                                )[name] ?? name}{" "}
                                ·{" "}
                                {capability.verification === "verified"
                                  ? "已核验"
                                  : "尚未核验，可尝试"}
                              </li>
                            ))}
                          </ul>
                          <p className="muted">
                            一次文字测试只核验文字通道，其他能力按实际运行结果显示。
                          </p>
                        </div>
                      )}
                      <label className="role-check">
                        <input
                          type="checkbox"
                          checked={form.memory_read}
                          onChange={(e) =>
                            setForm({ ...form, memory_read: e.target.checked })
                          }
                        />
                        使用本角色记忆
                      </label>
                      <label className="role-check">
                        <input
                          type="checkbox"
                          checked={form.memory_write}
                          onChange={(e) =>
                            setForm({ ...form, memory_write: e.target.checked })
                          }
                        />
                        记住新的经历
                      </label>
                      <p className="muted">
                        仅使用此角色的记忆。新的经历会先成为候选，按原有审核规则处理。
                      </p>
                    </fieldset>
                    <label className="role-check">
                      <input
                        type="checkbox"
                        checked={form.enabled}
                        onChange={(e) =>
                          setForm({ ...form, enabled: e.target.checked })
                        }
                      />
                      启用角色
                    </label>
                    <label className="role-check">
                      <input
                        type="checkbox"
                        checked={form.dialogue}
                        onChange={(e) =>
                          setForm({ ...form, dialogue: e.target.checked })
                        }
                      />
                      允许对话
                    </label>
                    <p className="muted">
                      启用后，角色会独立安排日常。关闭对话仍保留生活推进；停用角色会暂停日常。
                    </p>
                    <DetailCard title="可用能力">
                      <p>
                        对话、记忆读取、记忆候选写入。机器人发言仍受各连接的观察与回复策略限制。
                      </p>
                    </DetailCard>
                    {(role || legacy) && (
                      <DetailCard title="诊断信息">
                        <p>角色标识：{role?.actor_id ?? legacy?.id}</p>
                        {role?.profile_revision && (
                          <p>人格修订：{role.profile_revision}</p>
                        )}
                        {role?.error_code && (
                          <p>
                            错误代码：{role.error_code}；阶段：{role.stage}
                          </p>
                        )}
                      </DetailCard>
                    )}
                    <div className="role-form-actions">
                      <button
                        className="button primary"
                        disabled={
                          busy || !form.name.trim() || role?.state === "pending"
                        }
                        onClick={() => void save()}
                      >
                        {busy ? "正在应用…" : "应用设置"}
                      </button>
                      {role?.enabled && role.state === "active" && (
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() => void save(false)}
                        >
                          停用角色
                        </button>
                      )}
                    </div>
                    {role && (
                      <div className="role-danger-zone">
                        <button
                          type="button"
                          className="button"
                          disabled={busy}
                          onClick={() => setDeleteOpen(true)}
                        >
                          <Trash2 aria-hidden="true" /> 删除角色
                        </button>
                        <p className="muted">
                          移除角色并停止运行，历史对话与记忆保留。
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </Drawer>
              <Drawer
                title={`删除 ${role?.name ?? "角色"}`}
                variant="card"
                open={deleteOpen}
                onClose={() => setDeleteOpen(false)}
              >
                <p>
                  删除后，角色将退出列表并停止运行。历史对话、日记和记忆会保留。
                </p>
                <button className="button" onClick={() => setDeleteOpen(false)}>
                  保留角色
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void deleteRole()}
                >
                  {busy ? "正在删除…" : "删除角色"}
                </button>
              </Drawer>
            </div>
          )}
        </>
      )}
    </section>
  );
}
