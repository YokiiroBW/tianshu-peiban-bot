import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { integrationPost, readFailure } from "../../app/integrationApi";
import { StatePanel } from "../../components/StatePanel";
import { DailyLife } from "./DailyLife";
import { LifeWorkspace } from "./LifeWorkspace";
import "./life.css";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import { BookShelf } from "../../components/BookShelf";
import { Drawer } from "../../components/Drawer";
import { WritingPanel } from "./WritingPanel";
import { DiaryPreparation } from "./DiaryPreparation";

type Connection = {
  can_retry?: boolean;
  available: boolean;
  code: string;
  peer: { configured: boolean; verified_at: string | null; code: string };
};
type Actor = {
  actor_id: string;
  label?: string;
  actor_version: number;
  world_id: string;
  room_id: string;
};
type Actors = {
  schema_version: number;
  fictional: true;
  items: Actor[];
  next_after_actor_id: string | null;
};
type Snapshot = {
  actor_id: string;
  actor_version: number;
  world_id: string;
  room_id: string;
  timezone: string;
  activity: string | null;
  mood: string;
  outfit_ref: string | null;
  changed_at: number;
  observed_at: number;
  state_basis: "last_persisted";
  fictional: true;
};
type DiaryProvenance = {
  kind: "fictional" | "digital" | "mixed";
  source_kinds: (
    "digital_experience" | "observation" | "interpretation" | "fictional"
  )[];
  selection_complete: boolean;
  timezone: string;
  occurred_from: string;
  occurred_before: string;
};
type Diary = {
  diary_id: string;
  actor_id: string;
  day: string;
  state: string;
  version: number;
  published_revision_id: string;
  fictional?: boolean;
  provenance?: DiaryProvenance;
  captured: {
    recipe_id: string;
    recipe_version: number;
    material_version: string;
  };
};
type Diaries = {
  actor_id: string;
  items: Diary[];
  next_after: { day: string; diary_id: string } | null;
  fictional: boolean;
};
type Revision = {
  diary_id: string;
  diary_version: number;
  revision_id: string;
  actor_id: string;
  content: string;
  created_at: number;
  captured: {
    recipe_id: string;
    recipe_version: number;
    material_version: string;
    config_version: number;
  };
  fictional: boolean;
  provenance?: DiaryProvenance;
};

function diarySource(provenance?: DiaryProvenance) {
  if (!provenance) return "虚构剧情";
  const labels = {
    digital_experience: "实际数字经历",
    observation: "旁听记录",
    interpretation: "主观理解",
    fictional: "虚构剧情",
  };
  return provenance.source_kinds.map((kind) => labels[kind]).join(" · ");
}

function dateText(value: number) {
  return Number.isFinite(value)
    ? new Date(value * 1000).toLocaleString("zh-CN")
    : "没有可靠时间";
}

function unavailableTitle(code: string) {
  if (code.endsWith("_read_required")) return "当前账号未获角色生活读取权限";
  if (code.endsWith("_credential_missing")) return "角色生活服务凭据缺失";
  return "角色生活尚未配置";
}

/** Read-only persisted life and authorized published diary revisions. */
export default function LifePage({
  mode = "life",
}: {
  mode?: "life" | "diary" | "writing";
}) {
  const workspace = useRoleWorkspace();
  const { session } = useAuth();
  const csrf = session?.authenticated ? session.csrf : "";
  const [state, setState] = useState<Connection | null>(null);
  const [actors, setActors] = useState<Actor[]>([]);
  const [actorCursor, setActorCursor] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [diaries, setDiaries] = useState<Diary[]>([]);
  const [diaryCursor, setDiaryCursor] = useState<Diaries["next_after"]>(null);
  const [selected, setSelected] = useState<Diary | null>(null);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stateError, setStateError] = useState("");
  const active = useRef<AbortController | null>(null);
  const generation = useRef(0);

  function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function loadState() {
    if (!csrf) return;
    const controller = start();
    generation.current++;
    setState(null);

    setStateError("");
    setActors([]);
    setActorCursor(null);
    setActor("");
    setSnapshot(null);
    setDiaries([]);
    setSelected(null);
    setRevision(null);
    try {
      const next = await integrationPost<Connection>(
        "life/state",
        {},
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted) setState(next);
    } catch (cause) {
      if (!controller.signal.aborted) setStateError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    generation.current++;
    setActors([]);
    setActorCursor(null);
    setActor("");
    setSnapshot(null);
    setDiaries([]);
    setSelected(null);
    setRevision(null);
    void loadState();
    return () => active.current?.abort();
  }, [csrf]);

  async function listActors(after_actor_id: string | null = null) {
    if (!csrf) return;
    const controller = start();
    const mark = generation.current;
    try {
      const answer = await integrationPost<Actors>(
        "life/actors",
        { limit: 20, after_actor_id },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted || generation.current !== mark) return;

      setActors((before) =>
        after_actor_id ? [...before, ...answer.items] : answer.items,
      );
      setActorCursor(answer.next_after_actor_id);
    } catch (cause) {
      if (!controller.signal.aborted && generation.current === mark)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    if (state?.available) void listActors();
  }, [state?.available, csrf]);

  async function readActor(id: string) {
    if (!csrf || !id) return;
    const controller = start();
    const mark = generation.current;
    setSnapshot(null);
    setDiaries([]);
    setDiaryCursor(null);
    setSelected(null);
    setRevision(null);
    try {
      const [life, list] = await Promise.all([
        integrationPost<Snapshot>(
          "life/snapshot",
          { actor_id: id },
          csrf,
          controller.signal,
        ),
        integrationPost<Diaries>(
          "life/diaries",
          { actor_id: id, limit: 20, after: null },
          csrf,
          controller.signal,
        ),
      ]);
      if (controller.signal.aborted || generation.current !== mark) return;

      setSnapshot(life);
      setDiaries(list.items);
      setDiaryCursor(list.next_after);
    } catch (cause) {
      if (!controller.signal.aborted && generation.current === mark)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    generation.current++;
    if (actor) active.current?.abort();
    setError("");
    setSnapshot(null);
    setDiaries([]);
    setSelected(null);
    setRevision(null);
    if (actor) void readActor(actor);
  }, [actor, csrf]);

  useEffect(() => {
    const next = actors.some((row) => row.actor_id === workspace.actor)
      ? workspace.actor
      : "";
    if (next !== actor) {
      generation.current++;
      active.current?.abort();
      setBusy(false);
      setActor(next);
      setSnapshot(null);
      setDiaries([]);
      setSelected(null);
      setRevision(null);
    }
  }, [workspace.actor, actors]);

  async function moreDiaries() {
    if (!csrf || !actor || !diaryCursor) return;
    const controller = start();
    const mark = generation.current;
    try {
      const answer = await integrationPost<Diaries>(
        "life/diaries",
        { actor_id: actor, limit: 20, after: diaryCursor },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted || generation.current !== mark) return;

      setDiaries((before) => [...before, ...answer.items]);
      setDiaryCursor(answer.next_after);
    } catch (cause) {
      if (!controller.signal.aborted && generation.current === mark)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function readRevision(row: Diary) {
    if (!csrf || !actor) return;
    const controller = start();
    const mark = generation.current;
    setSelected(row);
    setRevision(null);
    try {
      const answer = await integrationPost<Revision>(
        "life/revision",
        {
          actor_id: actor,
          diary_id: row.diary_id,
          revision_id: row.published_revision_id,
          expected_diary_version: row.version,
        },
        csrf,
        controller.signal,
      );
      if (!controller.signal.aborted && generation.current === mark) {
        setRevision(answer);
      }
    } catch (cause) {
      if (!controller.signal.aborted && generation.current === mark)
        setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  if (!csrf)
    return (
      <StatePanel kind="error" title="请先登录">
        <p>角色生活与日记只对已授权账号开放。</p>
      </StatePanel>
    );
  if (stateError)
    return (
      <StatePanel
        kind="error"
        title="连接状态没有读到"
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
      <StatePanel kind="loading" title="正在读取生活连接">
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
          部署端需要登记生活读取身份和角色授权。网页无需填写服务地址或令牌。
        </p>
        <p>状态码：{state.code}</p>
      </StatePanel>
    );

  const roleName =
    workspace.roles.find((r) => r.id === workspace.actor)?.name ??
    actors.find((r) => r.actor_id === actor)?.label ??
    "角色";
  return (
    <div className="life-page scroll-page">
      <header className="role-section-heading">
        <div className="role-identity-title">
          <span className="role-context-avatar">{roleName.slice(0, 1)}</span>
          <div>
            <h2>
              {mode === "diary"
                ? `${roleName}的日记`
                : mode === "writing"
                  ? `${roleName}的创作`
                  : `${roleName}的生活`}
            </h2>
            <p>
              {mode === "diary"
                ? "翻开日常里值得记住的一页。"
                : mode === "writing"
                  ? "故事、灵感与尚未完成的篇章。"
                  : "近况、计划与经历，各有自己的位置。"}
            </p>
          </div>
        </div>
        <button
          className="button"
          onClick={() => void listActors()}
          disabled={busy}
        >
          <RefreshCw aria-hidden="true" />
          刷新
        </button>
      </header>
      {actorCursor && (
        <button
          className="button"
          disabled={busy}
          onClick={() => void listActors(actorCursor)}
        >
          继续读取角色
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {!actor && !busy && (
        <StatePanel kind="empty" title="当前角色暂无可读取的生活内容">
          <p>请在侧栏选择已授权的角色，或到角色管理检查运行状态。</p>
        </StatePanel>
      )}
      {actor === workspace.actor &&
        actor &&
        (mode === "life" ? (
          <LifeWorkspace
            key={`workspace:${csrf}:${actor}`}
            access={{ actor, csrf }}
            overview={
              <DailyLife
                key={`${csrf}:${actor}`}
                actor={actor}
                csrf={csrf}
                canRetry={state.can_retry === true}
                snapshot={snapshot?.actor_id === actor ? snapshot : null}
              />
            }
          />
        ) : mode === "writing" ? (
          <WritingPanel key={actor} access={{ actor, csrf }} />
        ) : (
          <>
            {state.can_retry === true && snapshot?.actor_id === actor && (
              <DiaryPreparation
                key={`${actor}:${csrf}`}
                access={{ actor, csrf }}
                today={new Date(snapshot.observed_at * 1000).toLocaleDateString(
                  "sv-SE",
                  { timeZone: snapshot.timezone },
                )}
              />
            )}
            <BookShelf
              entries={diaries.map((row) => ({
                id: row.diary_id,
                title: row.day,
                subtitle: diarySource(row.provenance),
                badge: "生活日记",
                category: row.day.slice(0, 7).replace("-", " 年 ") + " 月",
                selected: selected?.diary_id === row.diary_id,
              }))}
              empty="还没有已发布日记"
              onOpen={(id) => {
                const row = diaries.find((r) => r.diary_id === id);
                if (row) void readRevision(row);
              }}
            />
            {diaryCursor && (
              <button
                className="button"
                disabled={busy}
                onClick={() => void moreDiaries()}
              >
                继续读取日记
              </button>
            )}
            <Drawer
              title={selected ? `${selected.day} · ${roleName}` : "日记"}
              open={!!selected}
              onClose={() => {
                active.current?.abort();
                setBusy(false);
                setSelected(null);
                setRevision(null);
              }}
              variant="card"
            >
              {error && <p role="alert">{error}</p>}
              {!revision && !error ? (
                <p>正在读取日记…</p>
              ) : (
                revision && (
                  <article className="life-revision">
                    <p>{revision.content}</p>
                    <small>
                      {dateText(revision.created_at)} ·{" "}
                      {diarySource(revision.provenance)}
                    </small>
                  </article>
                )
              )}
            </Drawer>
          </>
        ))}
    </div>
  );
}
