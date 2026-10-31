import { Drawer } from "../../../components/Drawer";
import { DetailCard } from "../../../components/DetailCard";
import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../../../app/Auth";
import { useRoleWorkspace } from "../../../app/RoleWorkspace";
import {
  integrationPost,
  readFailure,
  writeFailure,
} from "../../../app/integrationApi";
import {
  readRolePreference,
  saveRolePreference,
} from "../../../app/rolePreference";
import { requestId } from "../../../app/requestId";
import { StatePanel } from "../../../components/StatePanel";
import { StatusRail } from "../../../components/StatusRail";
import { configureSkills, manageSkills, readSkills } from "./api";
import { SkillConnection } from "./SkillConnection";
import { SkillSources } from "./SkillSources";
import type { CredentialChange, Skill, SkillsCatalog } from "./types";
import "../external.css";
import "./skills.css";

type Actor = { actor_id: string; label?: string };
const states: Record<
  Skill["availability"]["state"],
  { label: string; tone: "blue" | "gray" | "yellow" | "red" }
> = {
  available: { label: "可用", tone: "blue" },
  disabled: { label: "已停用", tone: "gray" },
  not_configured: { label: "未配置", tone: "yellow" },
  unsupported: { label: "待接入", tone: "gray" },
  unavailable: { label: "暂时不可用", tone: "red" },
};

export function SkillsPanel({ actorId }: { actorId?: string }) {
  const { session } = useAuth();
  const workspace = useRoleWorkspace();
  const csrf = session?.authenticated ? session.csrf : "";
  const preference = `tianshu-skills-role:${encodeURIComponent(session?.username || csrf)}`;
  const [actors, setActors] = useState<Actor[]>([]);
  const [actor, setActor] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<SkillsCatalog | null>(null);
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef<AbortController | null>(null);
  const attempt = useRef<{ signature: string; id: string } | null>(null);

  function start() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    return controller;
  }

  async function listActors(after: string | null = null) {
    if (!csrf) return;
    const controller = start();
    try {
      const response = await integrationPost<{
        items: Actor[];
        next_after_actor_id: string | null;
      }>(
        "life/actors",
        { limit: 50, after_actor_id: after },
        csrf,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setActors((previous) =>
        after ? [...previous, ...response.items] : response.items,
      );
      setCursor(response.next_after_actor_id);
      if (!after)
        setActor((previous) =>
          actorId
            ? response.items.some((item) => item.actor_id === actorId)
              ? actorId
              : ""
            : response.items.some((item) => item.actor_id === previous)
              ? previous
              : (response.items.find(
                  (item) => item.actor_id === readRolePreference(preference),
                )?.actor_id ??
                response.items[0]?.actor_id ??
                ""),
        );
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  async function load() {
    if (!actor || !csrf) return;
    const controller = start();
    try {
      const value = await readSkills({ actor, csrf }, controller.signal);
      if (!controller.signal.aborted) setCatalog(value);
    } catch (cause) {
      if (!controller.signal.aborted) setError(readFailure(cause));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    setCatalog(null);
    setActors([]);
    setActor("");
    setEditing("");
    setNotice("");
    void listActors();
    return () => request.current?.abort();
  }, [csrf]);
  useEffect(() => {
    setCatalog(null);
    setEditing("");
    setNotice("");
    void load();
    return () => request.current?.abort();
  }, [actor, csrf]);

  async function mutate(
    operation: string,
    value: object,
    credential?: CredentialChange,
    revision: number | null = null,
  ) {
    if (!catalog) return false;
    const controller = start();
    setNotice("");
    const signature = JSON.stringify([
      actor,
      operation,
      value,
      credential,
      revision,
      catalog.actor_version,
    ]);
    if (attempt.current?.signature !== signature)
      attempt.current = { signature, id: requestId() };
    const clientId = attempt.current.id;
    try {
      const next = credential
        ? await configureSkills(
            { actor, csrf },
            operation as "skill.update" | "source.configure",
            value,
            credential,
            revision,
            catalog.actor_version,
            clientId,
            controller.signal,
          )
        : await manageSkills(
            { actor, csrf },
            operation,
            value,
            catalog.actor_version,
            clientId,
            controller.signal,
          );
      if (controller.signal.aborted) return false;
      setCatalog(next);
      const refreshedSource = next.sources.find(
        (source) =>
          source.source_id === (value as { source_id?: string }).source_id,
      );
      setNotice(
        operation === "source.refresh"
          ? refreshedSource?.state === "ready"
            ? "目录已刷新；技能状态以当前可用性为准。"
            : "目录刷新未完成，已保留原有技能目录。"
          : "已保存当前角色的技能设置。",
      );
      setEditing("");
      attempt.current = null;
      return true;
    } catch (cause) {
      if (!controller.signal.aborted) setError(writeFailure(cause));
      return false;
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function selectActor(next: string) {
    request.current?.abort();
    setCatalog(null);
    setActor(next);
    saveRolePreference(preference, next);
  }
  function comfyLink() {
    workspace.select(actor);
    saveRolePreference("tianshu-life-tab", "images");
    saveRolePreference(
      `tianshu-life-role:${encodeURIComponent(session?.username || csrf)}`,
      actor,
    );
  }

  if (!csrf)
    return (
      <StatePanel kind="error" title="请先登录">
        <p>技能管理使用当前账号的角色授权。</p>
      </StatePanel>
    );

  return (
    <div className="skills-page">
      <section className="panel skills-header external-form">
        <div className="section-heading">
          <h2>角色技能</h2>
          <button
            className="button"
            disabled={busy}
            onClick={() => void (actor ? load() : listActors())}
          >
            <RefreshCw aria-hidden="true" />
            刷新状态
          </button>
        </div>
        <p className="muted">
          查看角色可以使用的能力，管理启停与来源。停用后角色不再发起新的对话或主动调用，已开始的任务和原有管理入口继续可用。
        </p>
        <label>
          选择角色
          <select
            value={actor}
            disabled={busy || actors.length === 0 || !!actorId}
            onChange={(event) => selectActor(event.target.value)}
          >
            {actors.length === 0 && <option value="" />}
            {actors.map((item) => (
              <option key={item.actor_id} value={item.actor_id}>
                {item.label || item.actor_id}
              </option>
            ))}
          </select>
        </label>
        {cursor && (
          <button
            className="button"
            disabled={busy}
            onClick={() => void listActors(cursor)}
          >
            继续读取角色
          </button>
        )}
        {catalog && (
          <small>
            角色设置版本 {catalog.actor_version} · 目录版本{" "}
            {catalog.catalog_version.slice(0, 12)}
          </small>
        )}
      </section>
      {busy && !catalog && (
        <StatePanel kind="loading" title="正在读取技能目录">
          <p>请稍候。</p>
        </StatePanel>
      )}
      {error && (
        <StatePanel
          kind="error"
          title="本次操作未完成"
          action={
            <button
              className="button"
              disabled={busy}
              onClick={() => void (actor ? load() : listActors())}
            >
              重新读取状态
            </button>
          }
        >
          <p>{error}</p>
        </StatePanel>
      )}
      {notice && <p role="status">{notice}</p>}
      {!busy && !error && actors.length === 0 && (
        <StatePanel kind="empty" title="没有可管理角色">
          <p>请先到角色管理创建或启用角色。</p>
          <a className="button" href="#/roles/0">
            角色管理
          </a>
        </StatePanel>
      )}
      {catalog && (
        <>
          <section className="skills-directory" aria-label="技能目录">
            {catalog.skills.length === 0 && (
              <StatePanel kind="empty" title="目录没有技能">
                <p>服务已返回空目录，添加外部来源后可以刷新查看。</p>
              </StatePanel>
            )}
            {catalog.skills.map((skill) => {
              const state =
                states[skill.availability.state] ?? states.unavailable;
              const source = catalog.sources.find(
                (item) => item.source_id === skill.source_id,
              );
              return (
                <article className="panel skill-card" key={skill.id}>
                  <div className="section-heading">
                    <h3>{skill.title}</h3>
                    <StatusRail tone={state.tone} label={state.label} />
                  </div>
                  <p>{skill.description}</p>
                  <dl className="skill-metadata">
                    <div>
                      <dt>来源</dt>
                      <dd>{source?.name ?? skill.source_id}</dd>
                    </div>
                    <div>
                      <dt>版本</dt>
                      <dd>{skill.version}</dd>
                    </div>
                    <div>
                      <dt>分类</dt>
                      <dd>{skill.domain}</dd>
                    </div>
                  </dl>
                  <DetailCard title="查看能力与状态">
                    <ul>
                      {skill.operations.map((operation) => (
                        <li key={operation}>{operation}</li>
                      ))}
                    </ul>
                    {skill.availability.reason_code && (
                      <small>状态码：{skill.availability.reason_code}</small>
                    )}
                  </DetailCard>
                  {skill.installed ? (
                    <div className="skills-actions">
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() =>
                          void mutate(
                            skill.enabled ? "skill.disable" : "skill.enable",
                            { skill_id: skill.id },
                          )
                        }
                      >
                        {skill.enabled ? "停用技能" : "启用技能"}
                      </button>
                      {skill.definition.handler_id === "image.generate" && (
                        <a
                          className="button"
                          href="#/roles/life"
                          onClick={comfyLink}
                        >
                          管理生图与 ComfyUI
                        </a>
                      )}
                      {skill.definition.handler_id === "gscore.query" && (
                        <button
                          className="button"
                          disabled={busy}
                          onClick={() =>
                            setEditing(editing === skill.id ? "" : skill.id)
                          }
                        >
                          配置 GSCore
                        </button>
                      )}
                    </div>
                  ) : (
                    <p className="muted">
                      当前执行适配器尚未接入，此条目暂时不能使用。
                    </p>
                  )}
                  {skill.definition.handler_id === "image.generate" && (
                    <small>
                      在角色生活的“衣柜与相册”中管理已有连接与工作流。
                    </small>
                  )}
                  {editing === skill.id &&
                    skill.definition.handler_id === "gscore.query" && (
                      <Drawer
                        title={"GSCore 连接设置"}
                        variant="card"
                        open={true}
                        onClose={() => setEditing("")}
                      >
                        {error && <p role="alert">{error}</p>}
                        {notice && <p role="status">{notice}</p>}
                        <SkillConnection
                          key={`${actor}:${skill.id}`}
                          access={{ actor, csrf }}
                          skill={skill}
                          busy={busy}
                          save={(value, credential, revision) =>
                            mutate("skill.update", value, credential, revision)
                          }
                        />
                      </Drawer>
                    )}
                </article>
              );
            })}
          </section>
          <SkillSources
            key={actor}
            access={{ actor, csrf }}
            sources={catalog.sources}
            busy={busy}
            refresh={(source) =>
              void mutate("source.refresh", { source_id: source.source_id })
            }
            save={(value, credential, revision) =>
              mutate("source.configure", value, credential, revision)
            }
          />
        </>
      )}
    </div>
  );
}
