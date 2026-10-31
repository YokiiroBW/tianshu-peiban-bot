import { useEffect, useRef, useState } from "react";
import {
  Search,
  UsersRound,
  UserRound,
  RefreshCw,
  MessageCircle,
  ChevronRight,
} from "lucide-react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import {
  readRolePreference,
  saveRolePreference,
} from "../../app/rolePreference";
import { StatePanel } from "../../components/StatePanel";
import { PersonDetail } from "./PersonDetail";
import { ObservationUnlock } from "./ObservationUnlock";
import {
  personName,
  type MemoryState,
  type Observations,
  type Profile,
  type Profiles,
  type FoundPage,
  type Person,
} from "./types";
import "./people.css";

type Discovery = { connectionId: string; page: FoundPage; error: string };
export default function PeoplePage({ section }: { section: number }) {
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const storageKey = `tianshu-memory-role:${encodeURIComponent(session?.username || csrf)}`;
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [observations, setObservations] = useState<Observations | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profileCursor, setProfileCursor] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Discovery[]>([]);
  const [roleId, setRoleId] = useState("");
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [epoch, setEpoch] = useState(0);
  const active = useRef<AbortController | null>(null);
  const role = memory?.roles.find((item) => item.id === roleId) ?? null;
  useEffect(() => {
    if (!csrf) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setErrors([]);
    setProfiles([]);
    setDiscovery([]);
    setObservations(null);
    setSelected("");
    setProfileCursor(null);
    void (async () => {
      try {
        const state = await integrationPost<MemoryState>(
          "memory/state",
          {},
          csrf,
          controller.signal,
        ).catch(async () => {
          const directory = await integrationPost<{
            actors: { id: string; label: string }[];
          }>(
            "bot-observation/groups",
            { limit: 1, cursor: null, role_id: null },
            csrf,
            controller.signal,
          );
          return {
            available: false,
            code: "memory_unavailable",
            actor_id: null,
            roles: directory.actors.map((actor) => ({
              ...actor,
              version: 0,
              available: false,
              reason: "记忆服务暂不可用",
            })),
          } satisfies MemoryState;
        });
        if (controller.signal.aborted) return;
        setMemory(state);
        const remembered = readRolePreference(storageKey);
        const chosen =
          state.roles.find((item) => item.id === roleId)?.id ??
          state.roles.find((item) => item.id === remembered)?.id ??
          state.actor_id ??
          state.roles[0]?.id ??
          "";
        if (chosen !== roleId) {
          setRoleId(chosen);
          return;
        }
        if (!chosen) return;
        const results = await Promise.allSettled([
          integrationPost<Profiles>(
            "qq-admin/profiles",
            { limit: 100, after: null, role_id: chosen },
            csrf,
            controller.signal,
          ),
          integrationPost<Observations>(
            "bot-observation/view",
            {},
            csrf,
            controller.signal,
          ),
        ]);
        if (controller.signal.aborted) return;
        const [directory, accounts] = results;
        const warnings: string[] = [];
        if (directory.status === "fulfilled") {
          setProfiles(directory.value.items);
          setProfileCursor(directory.value.next_cursor);
        } else warnings.push("身份目录：" + readFailure(directory.reason));
        if (accounts.status === "fulfilled") {
          setObservations(accounts.value);
          const pages = await Promise.all(
            accounts.value.connections
              .filter((item) => accounts.value.unlocked && item.read_enabled)
              .map(async (connection): Promise<Discovery> => {
                try {
                  return {
                    connectionId: connection.id,
                    page: await integrationPost<FoundPage>(
                      "bot-observation/discovered",
                      {
                        id: connection.id,
                        limit: 100,
                        cursor: null,
                        role_id: chosen,
                      },
                      csrf,
                      controller.signal,
                    ),
                    error: "",
                  };
                } catch (cause) {
                  return {
                    connectionId: connection.id,
                    page: { items: [], next_cursor: null },
                    error: connection.name + "：" + readFailure(cause),
                  };
                }
              }),
          );
          if (controller.signal.aborted) return;
          setDiscovery(pages);
        } else warnings.push("会话观察：" + readFailure(accounts.reason));
        setErrors(warnings);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setMemory(null);
          setErrors(["角色与记忆：" + readFailure(cause)]);
        }
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    })();
    return () => controller.abort();
  }, [csrf, storageKey, roleId, epoch, section]);

  const byId = new Map<string, Person>();
  for (const profile of profiles)
    byId.set(profile.qq_id, { qqId: profile.qq_id, profile, sources: [] });
  for (const found of discovery) {
    const connection = observations?.connections.find(
      (item) => item.id === found.connectionId,
    );
    if (!connection) continue;
    for (const item of found.page.items) {
      const person = byId.get(item.author) ?? {
        qqId: item.author,
        sources: [],
      };
      person.sources.push({ ...item, connection });
      byId.set(item.author, person);
    }
  }
  const allPeople = [...byId.values()].sort(
    (a, b) =>
      Math.max(0, ...b.sources.map((source) => source.last_at)) -
        Math.max(0, ...a.sources.map((source) => source.last_at)) ||
      a.qqId.localeCompare(b.qqId),
  );
  const visible = allPeople.filter(
    (person) =>
      (filter === "all" ||
        person.sources.some((source) =>
          source.conversation.startsWith(`${filter}:`),
        )) &&
      [
        person.qqId,
        person.profile?.display_name,
        ...(person.profile?.aliases.map((alias) => alias.value) ?? []),
      ].some((value) =>
        value?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
      ),
  );
  const person = byId.get(selected) ?? null;
  const hasMore = Boolean(
    profileCursor || discovery.some((item) => item.page.next_cursor),
  );
  async function more() {
    if (busy || !csrf || !hasMore) return;
    const controller = new AbortController();
    active.current?.abort();
    active.current = controller;
    setBusy(true);
    setErrors([]);
    try {
      if (profileCursor) {
        const result = await integrationPost<Profiles>(
          "qq-admin/profiles",
          { limit: 100, after: profileCursor, role_id: roleId },
          csrf,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setProfiles((before) => {
          const ids = new Set(before.map((item) => item.qq_id));
          return [
            ...before,
            ...result.items.filter((item) => !ids.has(item.qq_id)),
          ];
        });
        setProfileCursor(result.next_cursor);
      }
      const updated = await Promise.all(
        discovery.map(async (source) => {
          if (!source.page.next_cursor) return source;
          try {
            const result = await integrationPost<FoundPage>(
              "bot-observation/discovered",
              {
                id: source.connectionId,
                limit: 100,
                cursor: source.page.next_cursor,
                role_id: roleId,
              },
              csrf,
              controller.signal,
            );
            const ids = new Set(
              source.page.items.map(
                (item) => `${item.author}:${item.conversation}`,
              ),
            );
            return {
              ...source,
              error: "",
              page: {
                ...result,
                items: [
                  ...source.page.items,
                  ...result.items.filter(
                    (item) => !ids.has(`${item.author}:${item.conversation}`),
                  ),
                ],
              },
            };
          } catch (cause) {
            return { ...source, error: readFailure(cause) };
          }
        }),
      );
      if (!controller.signal.aborted) setDiscovery(updated);
    } catch (cause) {
      if (!controller.signal.aborted) setErrors([readFailure(cause)]);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  useEffect(() => () => active.current?.abort(), []);
  if (!csrf)
    return (
      <StatePanel kind="error" title="请先登录">
        <p>登录后查看获授权的用户档案。</p>
      </StatePanel>
    );
  return (
    <div className="people-page scroll-page">
      <header className="people-toolbar">
        <div className="people-page-intro">
          <span className="people-icon-tile">
            <UsersRound aria-hidden="true" />
          </span>
          <div>
            <h2>认识的每一个人</h2>
            <p>查看当前角色认识和绑定的用户档案。</p>
          </div>
        </div>
        <label className="people-role">
          查看角色
          <select
            aria-label="查看角色"
            value={roleId}
            onChange={(event) => {
              active.current?.abort();
              setSelected("");
              setProfiles([]);
              setDiscovery([]);
              setRoleId(event.target.value);
              saveRolePreference(storageKey, event.target.value);
            }}
          >
            <option value="">{memory?.roles.length ? "选择角色" : ""}</option>
            {memory?.roles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
                {item.available ? "" : "（记忆暂不可读）"}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button"
          disabled={busy}
          onClick={() => setEpoch((value) => value + 1)}
        >
          <RefreshCw size={16} aria-hidden="true" />
          刷新档案
        </button>
      </header>
      {(errors.length > 0 || discovery.some((item) => item.error)) && (
        <div className="people-warning" role="alert">
          <strong>部分资料未能读取</strong>
          {[
            ...errors,
            ...discovery.filter((item) => item.error).map((item) => item.error),
          ].map((message) => (
            <p key={message}>{message}</p>
          ))}
          <small>读取失败不会被当作“没有用户”或“没有画像”。</small>
        </div>
      )}
      <>
        {observations?.available && !observations.unlocked && (
          <ObservationUnlock
            csrf={csrf}
            onUnlocked={() => setEpoch((value) => value + 1)}
          />
        )}
      </>
      <div className="people-layout scroll-layout">
        <aside
          className="people-directory"
          aria-label="用户目录"
          aria-busy={busy}
        >
          <div className="people-directory-heading">
            <h3>用户目录</h3>
            <span>{allPeople.length} 位已加载</span>
          </div>
          <label className="people-search">
            <Search size={17} aria-hidden="true" />
            <input
              aria-label="搜索已加载用户"
              placeholder="搜索昵称、QQ 或别名"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="people-filters" aria-label="用户来源筛选">
            {[
              ["all", "全部"],
              ["private", "私聊"],
              ["group", "群成员"],
            ].map(([key, label]) => (
              <button
                type="button"
                key={key}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="people-directory-note">
            已观察到的人也会出现，无需先形成画像。
          </p>
          {busy && !allPeople.length && (
            <p role="status" className="people-empty">
              正在读取用户目录…
            </p>
          )}
          {!busy && visible.length === 0 && (
            <p className="people-empty">
              {query || filter !== "all"
                ? "已加载的用户中暂无匹配项。"
                : errors.length
                  ? "目录暂时没有可显示的结果，请重试读取。"
                  : "还没有已登记或已观察到的用户。"}
            </p>
          )}
          <ul className="people-list" aria-label="用户列表" tabIndex={0}>
            {visible.map((item) => (
              <li key={item.qqId}>
                <button
                  type="button"
                  className="people-person"
                  data-selected={selected === item.qqId || undefined}
                  onClick={() => setSelected(item.qqId)}
                >
                  <span className="people-avatar">
                    {item.profile?.display_name?.slice(0, 1) || (
                      <UserRound size={21} aria-hidden="true" />
                    )}
                  </span>
                  <span className="people-person-text">
                    <strong>{personName(item)}</strong>
                    <small>QQ {item.qqId}</small>
                    <span className="people-person-tags">
                      {item.sources.some((source) =>
                        source.conversation.startsWith("private:"),
                      ) && (
                        <span>
                          <MessageCircle size={11} aria-hidden="true" />
                          私聊
                        </span>
                      )}
                      {item.sources.some((source) =>
                        source.conversation.startsWith("group:"),
                      ) && <span>群成员</span>}
                      {!item.profile && <span>待确认身份</span>}
                    </span>
                  </span>
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          {hasMore && (
            <button
              className="button people-more"
              disabled={busy}
              onClick={() => void more()}
            >
              加载更多用户
            </button>
          )}
          <small className="people-directory-note">
            搜索与来源筛选覆盖已加载范围{hasMore ? "，可继续加载。" : "。"}
          </small>
        </aside>
        <div
          key={`${roleId}:${selected}`}
          className="people-detail-area scroll-pane"
          role="region"
          aria-label="用户详情"
          tabIndex={0}
        >
          {person ? (
            <PersonDetail
              key={`${csrf}:${roleId}:${person.qqId}:${epoch}`}
              person={person}
              role={role}
              roles={memory?.roles ?? []}
              memoryAvailable={memory?.available === true}
              csrf={csrf}
              observations={observations}
              onObservationLocked={() =>
                setObservations(
                  (before) => before && { ...before, unlocked: false },
                )
              }
            />
          ) : (
            <div className="people-welcome">
              <span className="people-welcome-icon">
                <UsersRound aria-hidden="true" />
              </span>
              <h3>每次相遇，都从这里开始</h3>
              <p>
                选择当前角色目录中的一位用户，查看身份、共享画像、收到的消息与回复权限。
              </p>
              <small>
                用户身份、消息归档和记忆画像分别记录；一句“hi”也足以出现在目录中。
              </small>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
