import { useEffect, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { useAuth } from "../../app/Auth";
import { useRoleWorkspace } from "../../app/RoleWorkspace";
import {
  GLOBAL_SCOPE,
  actorScope,
  useDomainResource,
  useDomainCommand,
} from "../../api/useDomainResource";
import type { Page } from "../../api/contracts";
import { Drawer } from "../../components/Drawer";
import { CommandFeedback } from "../../components/CommandFeedback";
import type { Actor, PersonaCatalog } from "./roleTypes";
import "./roles.css";

export default function RoleManager() {
  const workspace = useRoleWorkspace();
  const { session } = useAuth();
  const [cursor, setCursor] = useState<string | null>(null);
  const roles = useDomainResource<Page<Actor>>("companion/roles/list", {
    ...GLOBAL_SCOPE,
    cursor,
    limit: 40,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const canCreate =
    session?.principal?.permissions.some(
      (value) => value === "*" || value === "companion.manage",
    ) === true;
  useEffect(() => {
    if (roles.accessLost) setSelected(null);
  }, [roles.accessLost]);
  return (
    <section className="role-manager scroll-page">
      <header className="role-section-heading">
        <div>
          <h2>角色</h2>
          <p>
            名称、固定作者人格与启用状态。模型分工和能力使用各自的实际设置。
          </p>
        </div>
        <div className="domain-actions">
          <button className="button" onClick={() => void roles.refresh()}>
            <RefreshCw />
            刷新
          </button>
          {canCreate && (
            <button
              className="button primary"
              onClick={() => setSelected("new")}
            >
              <Plus />
              新建角色
            </button>
          )}
        </div>
      </header>
      {roles.error && <p role="alert">{roles.error}</p>}
      <div className="role-grid">
        {roles.data?.items.map((role) => (
          <article className="role-card panel" key={role.actor_id}>
            <div className="role-card-header">
              <span className="role-context-avatar">
                {role.name.slice(0, 1)}
              </span>
              <div>
                <h3>{role.name}</h3>
                <span className="badge">
                  {role.enabled ? "已启用" : "已停用"}
                </span>
              </div>
            </div>
            <p>
              {role.persona_id
                ? `固定人格修订 ${role.persona_revision}`
                : "尚未选择作者人格"}
            </p>
            <div className="domain-actions">
              <button
                className="button"
                onClick={() => workspace.select(role.actor_id)}
              >
                选择这位角色
              </button>
              <button
                className="button"
                onClick={() => setSelected(role.actor_id)}
              >
                基本资料
              </button>
            </div>
          </article>
        ))}
      </div>
      {roles.data && !roles.data.items.length && <p>还没有可访问的角色。</p>}
      <div className="domain-actions">
        {cursor && (
          <button className="button" onClick={() => setCursor(null)}>
            回到首批角色
          </button>
        )}
        {roles.data?.next_cursor && (
          <button
            className="button"
            onClick={() => setCursor(roles.data!.next_cursor)}
          >
            更多角色
          </button>
        )}
      </div>
      <Drawer
        title={selected === "new" ? "新建角色" : "角色基本资料"}
        open={!!selected}
        onClose={() => setSelected(null)}
        variant="card"
      >
        {selected && (
          <RoleEditor
            key={selected}
            actorId={selected === "new" ? null : selected}
            close={() => setSelected(null)}
            saved={() => {
              setSelected(null);
              void roles.refresh();
              void workspace.refresh();
            }}
          />
        )}
      </Drawer>
    </section>
  );
}
function RoleEditor({
  actorId,
  close,
  saved,
}: {
  actorId: string | null;
  close: () => void;
  saved: () => void;
}) {
  const workspace = useRoleWorkspace();
  const actor = useDomainResource<Actor>(
    "companion/roles/get",
    actorScope(actorId),
    !!actorId,
  );
  const catalog = useDomainResource<PersonaCatalog>(
    "companion/personas/catalog",
    GLOBAL_SCOPE,
  );
  const action = useDomainCommand<{ actor_id: string }>(
    `role:${actorId ?? "new"}`,
    "global",
  );
  const row = actor.data;
  if (actorId && !row)
    return (
      <p role={actor.error ? "alert" : "status"}>
        {actor.error || "正在读取角色资料…"}
      </p>
    );
  const canManage = !actorId || row?.can_manage === true;
  return (
    <>
      <form
        className="domain-form"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const [persona_id, version] = String(data.get("persona") ?? "").split(
            "@",
          );
          void action
            .run(
              actorId ? "companion/roles/save" : "companion/roles/create",
              {
                actor_id: actorId,
                person_id: null,
                conversation_id: null,
                name: data.get("name"),
                enabled: data.get("enabled") === "on",
                persona_id: persona_id || null,
                persona_revision: persona_id ? Number(version) : null,
              },
              row?.revision ?? null,
            )
            .then((result) => {
              if (result) saved();
            });
        }}
      >
        <label>
          名称
          <input
            name="name"
            defaultValue={row?.name ?? ""}
            required
            maxLength={128}
            readOnly={!canManage}
          />
        </label>
        <label>
          固定作者人格
          <select
            name="persona"
            defaultValue={
              row?.persona_id ? `${row.persona_id}@${row.persona_revision}` : ""
            }
            disabled={!canManage}
          >
            <option value="">暂不指定</option>
            {row?.persona_id &&
              !catalog.data?.items.some(
                (item) =>
                  item.persona_id === row.persona_id &&
                  item.current_revision === row.persona_revision,
              ) && (
                <option value={`${row.persona_id}@${row.persona_revision}`}>
                  当前固定版本 · {row.persona_revision}
                </option>
              )}
            {catalog.data?.items.map((item) => (
              <option
                key={item.persona_id}
                value={`${item.persona_id}@${item.current_revision}`}
              >
                {item.name} · 修订 {item.current_revision}
              </option>
            ))}
          </select>
        </label>
        {catalog.error && <p role="alert">{catalog.error}</p>}
        <label className="inline-check">
          <input
            type="checkbox"
            name="enabled"
            defaultChecked={row?.enabled ?? false}
            disabled={!canManage}
          />
          启用角色与已配置的生活作息
        </label>
        <p className="muted">
          保存新的人格版本后，角色继续使用这里明确选择的版本。
        </p>
        <div className="domain-actions">
          {canManage && (
            <button className="button primary" disabled={action.busy}>
              保存基本资料
            </button>
          )}
          <button type="button" className="button" onClick={close}>
            收起
          </button>
        </div>
      </form>
      {actorId && (
        <div className="domain-actions">
          <a href="#/roles/persona" onClick={() => workspace.select(actorId)}>
            人格版本与作者正文
          </a>
          <a href="#/roles/settings" onClick={() => workspace.select(actorId)}>
            模型分工与能力目录
          </a>
          {canManage && (
            <button
              className="text-link"
              disabled={action.busy}
              onClick={() =>
                void action
                  .run(
                    "companion/roles/delete",
                    {
                      actor_id: actorId,
                      person_id: null,
                      conversation_id: null,
                    },
                    row!.revision,
                  )
                  .then((result) => {
                    if (result) saved();
                  })
              }
            >
              移除角色
            </button>
          )}
        </div>
      )}
      <CommandFeedback {...action} />
    </>
  );
}
