import { useEffect, useRef, useState, type ReactNode } from "react";
import { RefreshCw, UsersRound } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { requestId } from "../../app/requestId";
import { StatePanel } from "../../components/StatePanel";
import { DetailCard } from "../../components/DetailCard";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
import { ObservationUnlock } from "../people/ObservationUnlock";
import {
  GROUP_MODE_LABELS,
  type GroupPolicy,
  type GroupRule,
} from "../settings/GroupPolicyEditor";
import GroupInsights from "./GroupInsights";
import { GroupOverview } from "./GroupOverview";
import type { GroupEntry } from "./groupTypes";
import "../people/people.css";
import "./groups.css";

type Directory = {
  available: boolean;
  unlocked: boolean;
  actors: { id: string; label: string }[];
  groups: GroupEntry[];
  next_cursor: { owner_id: string; group_id: string } | null;
};
type MemoryState = {
  available: boolean;
  roles: { id: string; version: number; available: boolean }[];
};

export default function GroupDirectoryPage() {
  const { session } = useAuth();
  const csrf = session?.csrf ?? "";
  const storageKey = `tianshu-memory-role:${encodeURIComponent(session?.username || csrf)}`;
  const [roleId, setRoleId] = useState(
    () => readRolePreference(storageKey) ?? "",
  );
  const [view, setView] = useState<Directory | null>(null);
  const [actors, setActors] = useState<Directory["actors"]>([]);
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [unbound, setUnbound] = useState<GroupEntry[]>([]);
  const [unboundCursor, setUnboundCursor] =
    useState<Directory["next_cursor"]>(null);
  const [bindingId, setBindingId] = useState("");
  const active = useRef<AbortController | null>(null);
  const loadedPages = useRef(1);
  const directoryGeneration = useRef(0);
  const current = view?.groups.find(
    (group) => group.id === selected && group.rule.actor_id === roleId,
  );
  const role = memory?.roles.find((item) => item.id === roleId);

  async function load(signal: AbortSignal, more = false) {
    const generation = ++directoryGeneration.current;
    let cursor: Directory["next_cursor"] = null;
    let next: Directory | null = null;
    const found = new Map<string, GroupEntry>();
    const count = loadedPages.current + (more ? 1 : 0);
    for (let page = 0; page < count; page++) {
      next = await integrationPost<Directory>(
        "bot-observation/groups",
        { limit: 100, cursor, role_id: roleId || null },
        csrf,
        signal,
      );
      next.groups.forEach((item) => found.set(item.id, item));
      cursor = next.next_cursor;
      if (!cursor || !next.available) break;
    }
    if (!next) return;
    if (signal.aborted || generation !== directoryGeneration.current) return;
    const result = next;
    setActors(result.actors);
    if (!result.actors.some((actor) => actor.id === roleId)) {
      const remembered = readRolePreference(storageKey);
      const chosen =
        result.actors.find((actor) => actor.id === remembered)?.id ??
        result.actors[0]?.id ??
        "";
      if (chosen && chosen !== roleId) {
        setRoleId(chosen);
        return;
      }
    }
    if (more) loadedPages.current = count;
    setView((previous) => {
      // A directory request started before a save cannot undo its read-back.
      for (const prior of previous?.groups ?? []) {
        const incoming = found.get(prior.id);
        if (incoming && incoming.revision < prior.revision)
          found.set(prior.id, prior);
      }
      return { ...result, groups: result.available ? [...found.values()] : [] };
    });
    setSelected((previous) =>
      found.has(previous) ? previous : (found.values().next().value?.id ?? ""),
    );
  }

  useEffect(() => {
    if (!csrf) return;
    const controller = new AbortController();
    const update = async () => {
      if (document.hidden || controller.signal.aborted) return;
      try {
        await load(controller.signal);
        if (!controller.signal.aborted) setError("");
      } catch (cause) {
        if (!controller.signal.aborted) setError(readFailure(cause));
      }
      try {
        const next = await integrationPost<MemoryState>(
          "memory/state",
          {},
          csrf,
          controller.signal,
        );
        if (!controller.signal.aborted) setMemory(next);
      } catch {
        if (!controller.signal.aborted) setMemory(null);
      }
    };
    void update();
    const timer = window.setInterval(() => void update(), 15000);
    document.addEventListener("visibilitychange", update);
    return () => {
      controller.abort();
      active.current?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [csrf, refresh, roleId]);

  function chooseRole(value: string) {
    active.current?.abort();
    directoryGeneration.current++;
    loadedPages.current = 1;
    setRoleId(value);
    saveRolePreference(storageKey, value);
    setView(null);
    setSelected("");
    setQuery("");
    setError("");
    setNotice("");
    setBusy(false);
    setSaving(false);
    setBindingOpen(false);
  }

  async function loadUnbound(more = false) {
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await integrationPost<Directory>(
        "bot-observation/groups",
        { limit: 100, cursor: more ? unboundCursor : null, role_id: null },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setUnbound((previous) =>
        more ? [...previous, ...result.groups] : result.groups,
      );
      setUnboundCursor(result.next_cursor);
      if (!more) setBindingId(result.groups[0]?.id ?? "");
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function bindGroup() {
    const target = unbound.find((group) => group.id === bindingId);
    if (!target || !roleId || busy) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    try {
      const result = await integrationPost<{ group: GroupEntry }>(
        "bot-observation/group-policy",
        {
          owner_id: target.owner_id,
          group_id: target.group_id,
          expected_revision: target.revision,
          rule: { ...target.rule, actor_id: roleId },
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setView(
        (previous) =>
          previous && {
            ...previous,
            groups: [...previous.groups, result.group],
          },
      );
      setSelected(result.group.id);
      setBindingOpen(false);
      setNotice("群聊已绑定到当前角色。开启观察后才会整理新消息。");
      setRefresh((value) => value + 1);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function more() {
    if (!view?.next_cursor || busy) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      await load(controller.signal, true);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function save(rule: GroupRule): Promise<boolean> {
    if (!current || busy) return false;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await integrationPost<{
        group: GroupEntry;
        connection: {
          pending: boolean;
          revision: number;
          state: string;
          group_policy: GroupPolicy;
        };
      }>(
        "bot-observation/group-policy",
        {
          owner_id: current.owner_id,
          group_id: current.group_id,
          expected_revision: current.revision,
          rule: { ...rule, actor_id: roleId },
          client_request_id: requestId(),
        },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        const saved = result.connection;
        setView(
          (previous) =>
            previous && {
              ...previous,
              groups: previous.groups.map((group) =>
                group.id === result.group.id
                  ? result.group
                  : group.owner_id !== current.owner_id
                    ? group
                    : {
                        ...group,
                        pending: saved.pending,
                        revision: saved.revision,
                        state: saved.state,
                        rule:
                          saved.group_policy.groups[group.group_id] ??
                          saved.group_policy.default,
                      },
              ),
            },
        );
        setNotice(
          saved.pending
            ? "状态已保存，等待宿主确认。"
            : "群状态已保存并由宿主确认。",
        );
      }
      return !controller.signal.aborted;
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
      return false;
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        setSaving(false);
      }
    }
  }

  const groups =
    view?.groups.filter((group) =>
      `${group.name ?? ""} ${group.group_id} ${group.owner_name} ${group.account_id}`.includes(
        query.trim(),
      ),
    ) ?? [];
  const canRead =
    current?.has_archive &&
    current.read_enabled &&
    current.memory_group_id &&
    memory?.available &&
    role?.available &&
    !current.pending;
  const renderOverview = (controls?: ReactNode, summary?: ReactNode) =>
    current && (
      <GroupOverview
        key={current.id}
        group={current}
        roleName={actors.find((actor) => actor.id === roleId)?.label ?? roleId}
        disabled={!view?.unlocked || busy}
        saving={saving}
        error={error}
        controls={controls}
        summary={summary}
        onSave={save}
      />
    );
  return (
    <div className="group-directory-page scroll-page">
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>群档案</h2>
            <p className="muted">
              查看当前角色绑定的群聊。新发现的群先绑定角色，再选择观察或回复方式。
            </p>
          </div>
          <div className="group-directory-actions">
            <label className="people-role">
              查看角色
              <select
                aria-label="查看角色"
                value={roleId}
                disabled={busy}
                onChange={(event) => chooseRole(event.target.value)}
              >
                {!roleId && <option value="">选择角色</option>}
                {actors.map((actor) => (
                  <option key={actor.id} value={actor.id}>
                    {actor.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button"
              disabled={busy}
              onClick={() => setRefresh((value) => value + 1)}
            >
              <RefreshCw size={16} aria-hidden="true" />
              刷新群档案
            </button>
            {roleId && view?.unlocked && (
              <DetailCard
                title="绑定群聊"
                open={bindingOpen}
                onOpenChange={(open) => {
                  setBindingOpen(open);
                  if (open) void loadUnbound();
                }}
              >
                <p>
                  将尚未绑定角色的群聊加入当前角色；绑定本身不会开启观察或回复。
                </p>
                {error && <p role="alert">{error}</p>}
                <label className="people-role">
                  待绑定群聊
                  <select
                    aria-label="待绑定群聊"
                    value={bindingId}
                    disabled={busy}
                    onChange={(event) => setBindingId(event.target.value)}
                  >
                    {!unbound.length && (
                      <option value="">
                        {busy ? "正在读取…" : "没有待绑定群聊"}
                      </option>
                    )}
                    {unbound.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name ?? `群 ${group.group_id}`} ·{" "}
                        {group.group_id} · {group.owner_name}
                      </option>
                    ))}
                  </select>
                </label>
                {unboundCursor && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() => void loadUnbound(true)}
                  >
                    加载更多待绑定群
                  </button>
                )}
                <button
                  className="button primary"
                  disabled={busy || !bindingId}
                  onClick={() => void bindGroup()}
                >
                  绑定到当前角色
                </button>
              </DetailCard>
            )}
          </div>
        </div>
        {view && !view.unlocked && view.available && (
          <ObservationUnlock
            csrf={csrf}
            title="解锁群状态管理"
            description="群档案可以直接查看。调整状态沿用机器人管理的密码验证。"
            buttonLabel="解锁群状态"
            onUnlocked={() => setRefresh((value) => value + 1)}
          />
        )}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {!view && !error && <p role="status">正在读取群档案…</p>}
        {view && !view.available && (
          <StatePanel kind="unconfigured" title="群目录暂不可用">
            <p>请检查当前账号的机器人管理权限与账号连接。</p>
          </StatePanel>
        )}
        {view?.available && !view.groups.length && (
          <StatePanel kind="empty" title="当前角色尚未绑定群聊">
            <p>通过上方“绑定群聊”加入已发现的群。</p>
          </StatePanel>
        )}
      </section>
      {!!view?.groups.length && (
        <div className="group-directory-layout scroll-layout">
          <section className="panel group-directory-list">
            <label>
              查找群
              <input
                aria-label="查找群"
                placeholder="群名、群号或 BOT"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <ul aria-label="群聊列表" tabIndex={0}>
              {groups.map((group) => (
                <li key={group.id}>
                  <button
                    type="button"
                    aria-pressed={selected === group.id}
                    onClick={() => {
                      setSelected(group.id);
                      setError("");
                      setNotice("");
                    }}
                  >
                    <UsersRound size={18} aria-hidden="true" />
                    <span>
                      <strong>{group.name ?? `群 ${group.group_id}`}</strong>
                      <small>
                        {group.group_id} ·{" "}
                        {group.member_count === null
                          ? "人数未知"
                          : `${group.member_count} 人`}
                      </small>
                      <small>
                        {group.owner_name} · BOT {group.account_id}
                      </small>
                    </span>
                    <span className="group-directory-mode">
                      {GROUP_MODE_LABELS[group.rule.mode]}
                      {group.pending ? " · 待确认" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {!groups.length && (
              <p className="muted">已加载的档案中没有匹配的群。</p>
            )}
            {view.next_cursor && (
              <button
                className="button"
                disabled={busy}
                onClick={() => void more()}
              >
                加载更多群
              </button>
            )}
          </section>
          {current && (
            <div
              key={current.id}
              className="group-directory-content scroll-pane"
              role="region"
              aria-label="群聊详情"
              tabIndex={0}
            >
              {canRead && role && current.memory_group_id ? (
                <GroupInsights
                  key={
                    current.id +
                    ":" +
                    role.id +
                    ":" +
                    role.version +
                    ":" +
                    current.memory_group_id
                  }
                  roleId={role.id}
                  roleVersion={role.version}
                  csrf={csrf}
                  fixedGroupId={current.memory_group_id}
                  renderOverview={renderOverview}
                />
              ) : (
                <div className="group-workspace">
                  {renderOverview()}
                  <section className="panel">
                    <h2>
                      {current.has_archive ? "群内容暂不可读取" : "空档案"}
                    </h2>
                    <p className="muted">
                      {current.has_archive
                        ? "已有收集记录按原角色和读取授权保留；当前权限或宿主状态未就绪，暂不展示内容。"
                        : "目前仅登记基础资料，尚无已收集的聊天内容或整理结果。开启观察后才会逐步形成话题、群画像、互动与热度。"}
                    </p>
                  </section>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
