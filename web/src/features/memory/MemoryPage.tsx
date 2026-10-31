import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../app/Auth";
import {
  IntegrationError,
  integrationPost,
  readFailure,
} from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { StatusRail } from "../../components/StatusRail";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
import MemoryRecords, { type RecordGroup } from "./MemoryRecords";
import MemoryReview, { type MemoryReviewData } from "./MemoryReview";
import "./memory.css";

type Connection = {
  available: boolean;
  code: string;
  actor_id: string | null;
  roles: RoleChoice[];
  peer?: { configured: boolean; verified_at: string | null; code: string };
};
type RoleChoice = {
  id: string;
  label: string;
  version: number;
  available: boolean;
  reason: string | null;
};
type Overview = {
  memory_group_count: number;
  counts_truncated: boolean;
  verified_at: string;
  scope_version: number;
  review?: MemoryReviewData | null;
  learning?: {
    received: number;
    completed: number;
    waiting: number;
    truncated: boolean;
  } | null;
};
type Records = {
  items: RecordGroup[];
  next_cursor: string | null;
  verified_at: string;
  scope_version: number;
};

function unavailableTitle(code: string) {
  if (code === "memory_identity_not_ready")
    return "当前账号的记忆身份映射尚未就绪";
  if (code.endsWith("_read_required")) return "当前账号未获记忆读取权限";
  if (code.endsWith("_credential_missing")) return "记忆服务凭据缺失";
  return "记忆浏览尚未配置";
}

function roleReason(code: string | null) {
  if (code === "role_disabled") return "角色已停用";
  if (code === "memory_read_disabled") return "该角色未开启记忆读取";
  if (code === "role_configuring") return "角色正在核验，请稍后刷新";
  return "该角色暂不可读取";
}

/** A scoped, read-only view; no approval/forget controls are exposed to a service reader. */
export default function MemoryPage({ section }: { section: number }) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const storageKey = `tianshu-memory-role:${encodeURIComponent(session?.username || csrf)}`;
  const [overview, setOverview] = useState<Overview | null>(null);
  const [state, setState] = useState<Connection | null>(null);
  const [roleId, setRoleId] = useState<string | null>(null);
  const [records, setRecords] = useState<RecordGroup[]>([]);
  const [recordsCursor, setRecordsCursor] = useState<string | null>(null);
  const [recordsScope, setRecordsScope] = useState("");
  const [busy, setBusy] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [error, setError] = useState("");
  const [stateError, setStateError] = useState("");
  const current = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const recordsVersion = useRef<number | null>(null);
  const catalogVersion = useRef<number | null>(null);
  const role = state?.roles.find((item) => item.id === roleId);

  function clearResults() {
    generation.current++;
    recordsVersion.current = null;
    catalogVersion.current = null;
    current.current?.abort();
    setRecords([]);
    setOverview(null);
    setRecordsCursor(null);
    setRecordsScope("");
    setError("");
    setBusy(false);
    setRevalidating(false);
  }

  function roleRequest() {
    if (!role || !role.available) return null;
    return { role_id: role.id, role_version: role.version };
  }

  function failRead(cause: unknown) {
    const message = readFailure(cause);
    clearResults();
    if (
      cause instanceof IntegrationError &&
      [
        "scope_changed",
        "upstream_forbidden",
        "memory_identity_not_ready",
        "role_disabled",
        "role_configuring",
        "role_unavailable",
        "memory_read_disabled",
        "memory_read_required",
        "session_expired",
      ].includes(cause.code)
    ) {
      setState((before) =>
        before && roleId
          ? {
              ...before,
              roles: before.roles.map((item) =>
                item.id === roleId
                  ? { ...item, available: false, reason: cause.code }
                  : item,
              ),
            }
          : before,
      );
    }
    setError(message);
  }

  function chooseRole(next: string) {
    clearResults();
    setRoleId(next);
    saveRolePreference(storageKey, next);
  }

  function start() {
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function loadState() {
    if (!csrf) return;
    const wasListed = state?.roles.some((item) => item.id === roleId);
    const previousRoleId = roleId;
    clearResults();
    const controller = start();
    setState(null);
    setRoleId(null);
    setStateError("");
    try {
      const next = await integrationPost<Connection>(
        "memory/state",
        {},
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setState(next);
        const remembered = readRolePreference(storageKey);
        setRoleId(
          next.roles.length === 0
            ? null
            : next.roles.some((item) => item.id === remembered) ||
                (wasListed && remembered === previousRoleId)
              ? remembered
              : next.actor_id,
        );
      }
    } catch (cause) {
      if (!controller.signal.aborted) setStateError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    clearResults();
    if (!csrf) saveRolePreference(storageKey, null);
    void loadState();
    return () => current.current?.abort();
  }, [csrf, storageKey]);

  async function listRecords(cursor: string | null = null) {
    const selection = roleRequest();
    if (!csrf || !selection) return;
    const controller = start();
    const mark = generation.current;
    const key = "本人记忆";
    if (!cursor) {
      setRecords([]);
      setRecordsCursor(null);
      setRecordsScope(key);
    }
    try {
      const answer = await integrationPost<Records>(
        "memory/records",
        { ...selection, subject: null, limit: 20, cursor },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted || mark !== generation.current) return;
      if (
        catalogVersion.current !== null &&
        answer.scope_version < catalogVersion.current
      ) {
        void listRecords();
        return;
      }
      catalogVersion.current = answer.scope_version;
      recordsVersion.current = answer.scope_version;
      setOverview((before) =>
        before?.scope_version === answer.scope_version ? before : null,
      );
      setRecords((before) =>
        cursor ? [...before, ...answer.items] : answer.items,
      );
      setRecordsCursor(answer.next_cursor);
    } catch (cause) {
      if (!controller.signal.aborted && mark === generation.current) {
        failRead(cause);
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    if (!state) return;
    clearResults();
    if (!state?.available || !role?.available) return;
    if (section === 3) void listRecords();
  }, [state, roleId, section, csrf]);

  useEffect(() => {
    const selection = roleRequest();
    if (!csrf || !state?.available || !selection) return;
    let pending: AbortController | null = null;
    const verify = async (resumed = false) => {
      if (document.hidden || pending) return;
      const controller = new AbortController();
      const mark = generation.current;
      pending = controller;
      try {
        const next = await integrationPost<Overview>(
          "memory/overview",
          selection,
          csrf,
          controller.signal,
        );
        if (controller.signal.aborted || mark !== generation.current) return;
        if (
          catalogVersion.current !== null &&
          next.scope_version < catalogVersion.current
        )
          return;
        const changed =
          recordsVersion.current !== null &&
          recordsVersion.current !== next.scope_version;
        if (changed) clearResults();
        catalogVersion.current = next.scope_version;
        setOverview(next);
        if (section === 3 && (resumed || changed)) await listRecords();
      } catch (cause) {
        if (!controller.signal.aborted && mark === generation.current)
          failRead(cause);
      } finally {
        if (pending === controller) pending = null;
        if (
          !controller.signal.aborted &&
          mark === generation.current &&
          resumed
        )
          setRevalidating(false);
      }
    };
    const visible = () => {
      if (document.hidden) return;
      pending?.abort();
      pending = null;
      clearResults();
      setRevalidating(true);
      void verify(true);
    };
    void verify();
    const timer = window.setInterval(() => void verify(), 15000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      pending?.abort();
    };
  }, [csrf, state?.available, role?.id, role?.version, section]);

  if (!csrf)
    return (
      <StatePanel kind="error" title="请先登录">
        <p>记忆内容只对已授权账号开放。</p>
      </StatePanel>
    );
  if (stateError)
    return (
      <StatePanel
        kind="error"
        title="记忆连接状态没有读到"
        action={
          <button className="button" onClick={() => void loadState()}>
            重新读取
          </button>
        }
      >
        <p>{stateError}</p>
      </StatePanel>
    );
  if (!state)
    return (
      <StatePanel kind="loading" title="正在读取记忆连接">
        <p>请稍候。</p>
      </StatePanel>
    );
  if (!state.available)
    return (
      <StatePanel
        kind={state.code.endsWith("_not_configured") ? "unconfigured" : "error"}
        title={unavailableTitle(state.code)}
        action={
          <a className="button" href="#/settings/connections">
            查看连接状态
          </a>
        }
      >
        <p>
          部署端需要为当前账号配置精确的 Memory
          读取身份与范围。网页无需填写服务地址、账号映射或令牌。
        </p>
        <p>状态码：{state.code}</p>
      </StatePanel>
    );

  const rolePicker = (
    <section className="panel memory-role-panel">
      <div className="section-heading">
        <h2>当前角色</h2>
        <button
          className="button"
          onClick={() => void loadState()}
          disabled={busy}
        >
          <RefreshCw aria-hidden="true" />
          刷新角色
        </button>
      </div>
      <label htmlFor="memory-role-select">查看哪位角色的记忆</label>
      <select
        id="memory-role-select"
        value={roleId ?? ""}
        onChange={(event) => chooseRole(event.target.value)}
      >
        {roleId && !role && (
          <option value={roleId}>原角色已不在授权列表</option>
        )}
        {!roleId && (
          <option value="">{state.roles.length ? "请选择角色" : ""}</option>
        )}
        {state.roles.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
            {item.available ? "" : ` · ${roleReason(item.reason)}`}
          </option>
        ))}
      </select>
      {role && !role.available && (
        <p className="muted">
          {roleReason(role.reason)}。已隐藏此角色的记忆内容。
        </p>
      )}
    </section>
  );

  if (!role || !role.available)
    return (
      <div className="memory-page">
        {rolePicker}
        <StatePanel
          kind="error"
          title={role ? roleReason(role.reason) : "角色不可用"}
        >
          <p>
            {roleId && !role
              ? "原角色已不在授权列表。"
              : "此角色当前不可读取。"}
            请刷新角色列表或选择当前可读取的角色。
          </p>
        </StatePanel>
      </div>
    );

  if (revalidating)
    return (
      <div className="memory-page">
        {rolePicker}
        <StatePanel kind="loading" title="正在重新核验角色记忆">
          <p>请稍候，核验完成后会显示当前角色的记忆。</p>
        </StatePanel>
      </div>
    );

  return (
    <div className="memory-page">
      {rolePicker}
      <p className="muted">
        这里是用户与群的记忆。角色自己的经历、兴趣与更正记录在
        <a href="#/roles/life">角色生活</a>中单独查看。
      </p>
      <section className="panel">
        <div className="section-heading">
          <h2>授权记忆范围</h2>
          <button
            className="button"
            onClick={() => void listRecords()}
            disabled={busy}
          >
            <RefreshCw aria-hidden="true" />
            重新读取
          </button>
        </div>
        <StatusRail tone="yellow" label="按当前账号授权读取">
          <p>
            这里展示当前角色可以读取的记忆。来源或授权变化后，会重新核验并隐藏失效内容。
          </p>
        </StatusRail>
      </section>
      {error && (
        <StatePanel
          kind="error"
          title="本次记忆读取失败"
          action={
            <button className="button" onClick={() => void listRecords()}>
              从第一页重读
            </button>
          }
        >
          <p>{error}</p>
          <p>失败没有被当作空记忆。</p>
        </StatePanel>
      )}
      {section === 3 && (
        <section className="panel">
          <h2>本人记忆</h2>
          {overview?.learning && (
            <p className="muted" role="status">
              本次可见材料 {overview.learning.received} 条，已整理{" "}
              {overview.learning.completed} 条，待整理{" "}
              {overview.learning.waiting} 条。
              {overview.learning.truncated && "这里只统计部分已接收材料。"}
              整理完成可以不产生新记忆，尚未进入记忆服务的消息不在此统计。
            </p>
          )}
          {overview && (
            <p className="muted">
              来源核验于{" "}
              {new Date(overview.verified_at).toLocaleString("zh-CN")}
              ；失效来源的内容会隐藏。
            </p>
          )}
          {overview?.review && <MemoryReview review={overview.review} />}
          <MemoryRecords
            scope={recordsScope || "本人记忆"}
            records={records}
            cursor={recordsCursor}
            busy={busy}
            error={error}
            more={() => void listRecords(recordsCursor)}
          />
        </section>
      )}
    </div>
  );
}
