import { useEffect, useState } from "react";
import { CommandFeedback } from "../../../components/CommandFeedback";
import { SourceEvidence } from "../../../components/SourceEvidence";
import { RelationshipForm } from "./RelationshipForm";
import { types, type Projection, type RelationshipTarget } from "./api";
import { useRelationships } from "./useRelationships";
import "./relationships.css";
export default function RelationshipPanel(props: {
  csrf: string;
  selection?: RelationshipTarget;
}) {
  return (
    <RelationshipContent
      key={`${props.selection?.roleId ?? ""}:${props.selection?.personId ?? ""}`}
      selection={props.selection}
    />
  );
}
function RelationshipContent({
  selection,
}: {
  selection?: RelationshipTarget;
}) {
  const state = useRelationships(selection);
  const value = state.view.data;
  const [editing, setEditing] = useState<Projection | null>(null);
  useEffect(() => {
    setEditing(null);
  }, [state.roleId, state.personId]);
  useEffect(() => {
    if (state.view.accessLost) setEditing(null);
  }, [state.view.accessLost]);
  return (
    <section className="relationships" aria-label="关系与相处背景">
      <h3>关系与相处背景</h3>
      <p>关系、称呼与共同经历影响相处方式，实际访问与回复权限分别管理。</p>
      {!selection && (
        <div className="relationship-selectors">
          <label>
            角色
            <select
              value={state.roleId}
              onChange={(event) => state.chooseRole(event.target.value)}
            >
              <option value="">选择角色</option>
              {state.roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            人物
            <select
              value={state.personId}
              onChange={(event) => state.choosePerson(event.target.value)}
            >
              <option value="">选择已授权人物</option>
              {state.people.data?.items.map((person) => (
                <option key={person.person_id} value={person.person_id}>
                  {person.display_name}
                </option>
              ))}
            </select>
          </label>
          {state.cursor && (
            <button className="text-link" onClick={() => state.setCursor(null)}>
              回到首批人物
            </button>
          )}
          {state.people.data?.next_cursor && (
            <button
              className="text-link"
              onClick={() => state.setCursor(state.people.data!.next_cursor)}
            >
              更多人物
            </button>
          )}
        </div>
      )}
      {state.people.error && <p role="alert">{state.people.error}</p>}
      {state.view.error && <p role="alert">{state.view.error}</p>}
      {state.view.loading && !value && <p>正在读取当前关系依据…</p>}
      {value && (
        <>
          <header className="section-heading">
            <div>
              <h4>
                {value.display_label} ·{" "}
                {types[value.relationship_type] ?? value.relationship_type}
              </h4>
              <p>
                当前修订 {value.revision} ·{" "}
                {value.frozen ? "自动演变已冻结" : "允许自动演变"}
                {value.frozen_at
                  ? ` · ${new Date(value.frozen_at).toLocaleString("zh-CN")}`
                  : ""}
              </p>
            </div>
            <button
              className="button"
              onClick={() => void state.view.refresh()}
            >
              核对当前背景
            </button>
          </header>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {value.summary || "还没有单独记下相处背景。"}
          </p>
          <SourceEvidence sources={value.source_refs} />
          {(
            [
              ["boundaries", "边界"],
              ["preferences", "相处偏好"],
              ["shared_experiences", "共同经历"],
            ] as const
          ).map(([field, label]) => (
            <section className="panel" key={field}>
              <h4>{label}</h4>
              {!value[field].length && <p className="muted">尚无可读记录。</p>}
              {value[field].map((item) => (
                <article className="domain-record" key={item.detail_id}>
                  <p>{item.content}</p>
                  <small>
                    {item.explicit ? "明确记录" : "有来源的认识"} · 修订{" "}
                    {item.revision}
                  </small>
                  <SourceEvidence sources={item.source_refs} />
                </article>
              ))}
            </section>
          ))}
          {value.can_manage && (
            <div className="domain-actions">
              <button
                className="button"
                onClick={() => setEditing(structuredClone(value))}
              >
                更正关系背景
              </button>
              <button
                className="button"
                disabled={state.command.busy}
                onClick={() =>
                  void state.save(
                    {
                      relationship_type: value.relationship_type,
                      display_label: value.display_label,
                      summary: value.summary,
                      frozen: !value.frozen,
                    },
                    value.revision,
                  )
                }
              >
                {value.frozen ? "恢复以后自动演变" : "冻结以后自动演变"}
              </button>
            </div>
          )}
          {editing && (
            <RelationshipForm
              value={editing}
              busy={state.command.busy}
              save={state.save}
              close={() => setEditing(null)}
            />
          )}
          <CommandFeedback {...state.command} />
        </>
      )}
    </section>
  );
}
